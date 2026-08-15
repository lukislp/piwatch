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

_COLUMNS = ("t", *NODE_HISTORY_FIELDS)


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
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS node_samples "
        f"(node TEXT NOT NULL, {', '.join(f'{c} REAL' for c in _COLUMNS)})"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_node_samples_node_t ON node_samples(node, t)")
    conn.commit()
    return conn


def _insert(conn: sqlite3.Connection, node: str, entry: dict) -> None:
    placeholders = ", ".join("?" * (len(_COLUMNS) + 1))
    conn.execute(
        f"INSERT INTO node_samples (node, {', '.join(_COLUMNS)}) VALUES ({placeholders})",
        (node, *(entry.get(c) for c in _COLUMNS)),
    )
    conn.commit()


def prune(conn: sqlite3.Connection, retention_seconds: float) -> int:
    """Deletes rows older than the retention window; returns how many were removed."""
    cursor = conn.execute("DELETE FROM node_samples WHERE t < ?", (time.time() - retention_seconds,))
    conn.commit()
    return cursor.rowcount


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
        log.warning("Could not reload persisted history from %s (%s) -- starting empty", path, exc)


async def run(state: ClusterState) -> None:
    if not _enabled():
        return
    path = os.environ["PIWATCH_HISTORY_DB"]
    try:
        conn = _connect(path)
    except Exception as exc:
        log.warning("Could not open history DB at %s (%s) -- persistence disabled", path, exc)
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
                except Exception as exc:
                    log.warning("History write failed (%s) -- sample dropped", exc)
            if time.time() - last_prune > PRUNE_INTERVAL:
                await asyncio.to_thread(prune, conn, retention)
                last_prune = time.time()
    finally:
        state.unsubscribe(q)
        conn.close()
