"""Persists node_history to a local SQLite file -- opt-in via PIWATCH_HISTORY_DB -- so the
~3h in-memory chart window (state.node_history, ~3h at one sample every 10s -- see
state.py's HISTORY_LEN) survives a pod restart instead of starting empty every time.

Bounded, not unlimited: rows older than PIWATCH_HISTORY_RETENTION_DAYS (default 7) are
pruned periodically, so the file doesn't grow forever even though the process writes to it
continuously for as long as it runs.

Storage is a plain hostPath directory (see deploy/deployment.yaml), one file per replica --
NOT a shared PVC. This Deployment runs 2 replicas spread across different nodes via
podAntiAffinity for HA/failover (see README's Architecture section: independent state per
replica, no shared storage, no leader election); a ReadWriteOnce PVC can only be mounted by
pods on one node, which would either break that spread or make the second replica unable to
mount it at all. hostPath keeps each replica's history tied to whichever node it's
currently running on: a normal restart on the same node keeps its history, a reschedule to
a different node (rare -- only on node failure/drain) starts that replica fresh, matching
the app's existing "each replica is independently reconstructable" design rather than
fighting it.

Implemented via state.subscribe() like a WebSocket client, not a hook inside
record_node_sample() -- keeps state.py itself unaware persistence exists at all, and
reuses the pub/sub plumbing that's already there for every other consumer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from collections import deque

from ..state import HISTORY_LEN, NODE_HISTORY_FIELDS, ClusterState

log = logging.getLogger("piwatch.history")

PRUNE_INTERVAL = 3600  # seconds between retention sweeps
DEFAULT_RETENTION_DAYS = 7

ERROR_LOG_INTERVAL = 300  # seconds between repeated "write failed" errors during an outage

_COLUMNS = ("t", *NODE_HISTORY_FIELDS)


# ---------------------------------------------------------------------------------------
# Schema migrations
#
# The database is a plain SQLite file that outlives every deploy (hostPath), so the schema
# has to evolve in place: a column added to NODE_HISTORY_FIELDS does NOT appear in a table
# that already exists. (Adding the sd_* fields without a migration made every insert fail
# from 2026-09-05 on.) Migrations are an ordered, append-only list applied by _migrate();
# the applied count is stored in SQLite's `PRAGMA user_version`.
#
# Rules: never edit or reorder an entry, only append. Each step is written out literally --
# not derived from NODE_HISTORY_FIELDS -- so a new field without a new step is caught by
# test_history_schema_matches_node_history_fields. Each step is also idempotent, because
# databases created before versioning existed carry user_version 0 but already hold some
# (or all) of these tables and columns.
# ---------------------------------------------------------------------------------------


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(node_samples)")}


def _add_columns(conn: sqlite3.Connection, names: tuple[str, ...]) -> None:
    present = _columns(conn)
    for name in names:
        if name not in present:
            conn.execute(f"ALTER TABLE node_samples ADD COLUMN {name} REAL")


def _m1_create_node_samples(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS node_samples (node TEXT NOT NULL, t REAL, "
        "cpu_pct REAL, mem_pct REAL, temp_c REAL, "
        "nvme_read_bytes_per_s REAL, nvme_write_bytes_per_s REAL, "
        "net_rx_bytes_per_s REAL, net_tx_bytes_per_s REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_node_samples_node_t ON node_samples(node, t)")


def _m2_add_sd_throughput(conn: sqlite3.Connection) -> None:
    _add_columns(conn, ("sd_read_bytes_per_s", "sd_write_bytes_per_s"))


MIGRATIONS = (_m1_create_node_samples, _m2_add_sd_throughput)


def _migrate(conn: sqlite3.Connection) -> None:
    """Applies every migration newer than the file's user_version, each in its own
    transaction. Safe to call on every start: a current database is a no-op."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError(
            f"history DB schema version {version} is newer than this build supports "
            f"({len(MIGRATIONS)}) -- refusing to use it"
        )
    for target, step in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN")
        try:
            step(conn)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _enabled() -> bool:
    return bool(os.environ.get("PIWATCH_HISTORY_DB"))


def _retention_seconds() -> float:
    days = float(os.environ.get("PIWATCH_HISTORY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    return days * 86400


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # check_same_thread=False: run()'s inserts/prunes happen via asyncio.to_thread, which
    # can land on a different worker thread each call. Safe here because this connection is
    # only ever driven by run()'s single-consumer loop -- one await at a time, never two
    # threads touching it concurrently.
    # isolation_level=None: autocommit, so _migrate() controls its own transactions.
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _insert(conn: sqlite3.Connection, node: str, entry: dict) -> None:
    placeholders = ", ".join("?" * (len(_COLUMNS) + 1))
    conn.execute(
        f"INSERT INTO node_samples (node, {', '.join(_COLUMNS)}) VALUES ({placeholders})",
        (node, *(entry.get(c) for c in _COLUMNS)),
    )


def prune(conn: sqlite3.Connection, retention_seconds: float) -> int:
    """Deletes rows older than the retention window; returns how many were removed."""
    cursor = conn.execute("DELETE FROM node_samples WHERE t < ?", (time.time() - retention_seconds,))
    return cursor.rowcount


class WriteStatus:
    """Outcome of the persistence loop, surfaced on /readyz so a broken history store is
    visible without reading pod logs. Informational only: a history failure must never
    make a pod unready, the live dashboard works fine without it."""

    def __init__(self) -> None:
        self.failed_writes = 0
        self.last_error: str | None = None
        self._last_logged = 0.0

    @property
    def healthy(self) -> bool:
        return self.last_error is None

    def record_failure(self, exc: Exception) -> None:
        self.failed_writes += 1
        self.last_error = str(exc)
        # One sample every few seconds per node: log the first failure and then a summary
        # every ERROR_LOG_INTERVAL, instead of flooding the log during an outage.
        if self.failed_writes == 1 or time.time() - self._last_logged >= ERROR_LOG_INTERVAL:
            self._last_logged = time.time()
            log.error(
                "History write failed (%s) -- sample dropped (%d dropped so far)",
                exc, self.failed_writes,
            )

    def record_success(self) -> None:
        if self.last_error is not None:
            log.info("History writes recovered after %d dropped samples", self.failed_writes)
            self.last_error = None
            self._last_logged = 0.0


write_status = WriteStatus()


def status() -> dict | None:
    """None when persistence is off, otherwise the write health for /readyz."""
    if not _enabled():
        return None
    return {
        "ok": write_status.healthy,
        "failed_writes": write_status.failed_writes,
        "last_error": write_status.last_error,
    }


def load_startup_history(state: ClusterState) -> None:
    """One-shot, synchronous: reloads each node's most recent samples (bounded to
    HISTORY_LEN, same cap as the live ring buffer) before the app starts watching the
    cluster, so charts aren't empty for the first ~3h after every restart. Best-effort --
    a missing/corrupt file just means starting with empty history, same as before this
    feature existed."""
    if not _enabled():
        return
    path = os.environ["PIWATCH_HISTORY_DB"]
    try:
        conn = _connect(path)
        try:
            nodes = [r[0] for r in conn.execute("SELECT DISTINCT node FROM node_samples")]
            for node in nodes:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM node_samples "
                    f"WHERE node = ? ORDER BY t DESC LIMIT ?",
                    (node, HISTORY_LEN),
                ).fetchall()
                hist = state.node_history.setdefault(node, deque(maxlen=HISTORY_LEN))
                hist.extend(dict(zip(_COLUMNS, row, strict=True)) for row in reversed(rows))
        finally:
            conn.close()
    except Exception as exc:
        log.error("Could not reload persisted history from %s (%s) -- starting empty", path, exc)
        write_status.record_failure(exc)


async def run(state: ClusterState) -> None:
    if not _enabled():
        return
    path = os.environ["PIWATCH_HISTORY_DB"]
    try:
        conn = _connect(path)
    except Exception as exc:
        log.error("Could not open history DB at %s (%s) -- persistence disabled", path, exc)
        write_status.record_failure(exc)
        return

    retention = _retention_seconds()
    last_prune = time.time()
    q = state.subscribe()
    try:
        prune(conn, retention)
        while True:
            msg = await q.get()
            if msg["type"] == "node_metrics":
                # d's own "t" (set by record_node_sample) is what was appended to
                # node_history -- not msg["t"], which publish() stamps separately/later.
                d = msg["data"]
                try:
                    await asyncio.to_thread(_insert, conn, d["node"], d)
                    write_status.record_success()
                except Exception as exc:
                    write_status.record_failure(exc)
            if time.time() - last_prune > PRUNE_INTERVAL:
                await asyncio.to_thread(prune, conn, retention)
                last_prune = time.time()
    finally:
        state.unsubscribe(q)
        conn.close()
