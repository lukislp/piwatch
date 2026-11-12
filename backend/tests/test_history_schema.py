"""History DB schema: migrations vs. the model, upgrade of pre-existing files, and
visibility of failing writes.

Uses a real SQLite file -- the same engine production runs on -- not a mock.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from app.collectors import history
from app.state import NODE_HISTORY_FIELDS

# The table exactly as it existed before the sd_* columns (and as created on the live
# cluster): written out literally, so it can never drift along with the model.
LEGACY_DDL = (
    "CREATE TABLE node_samples (node TEXT NOT NULL, t REAL, cpu_pct REAL, mem_pct REAL, "
    "temp_c REAL, nvme_read_bytes_per_s REAL, nvme_write_bytes_per_s REAL, "
    "net_rx_bytes_per_s REAL, net_tx_bytes_per_s REAL)"
)


def _cols(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(node_samples)")}
    finally:
        conn.close()


def test_history_schema_matches_node_history_fields(tmp_path):
    """Guard: a field added to NODE_HISTORY_FIELDS without a migration fails here (and in
    CI), instead of silently dropping every sample in production."""
    path = str(tmp_path / "fresh.db")
    history._connect(path).close()
    assert _cols(path) == {"node", "t", *NODE_HISTORY_FIELDS}


def test_history_insert_succeeds_for_every_model_field_on_a_fresh_db(tmp_path):
    conn = history._connect(str(tmp_path / "fresh.db"))
    entry = {"t": 1.0, **{f: float(i) for i, f in enumerate(NODE_HISTORY_FIELDS)}}
    history._insert(conn, "pi-1", entry)
    row = conn.execute(f"SELECT {', '.join(NODE_HISTORY_FIELDS)} FROM node_samples").fetchone()
    conn.close()
    assert row == tuple(float(i) for i in range(len(NODE_HISTORY_FIELDS)))


def test_history_migrates_legacy_table_without_sd_columns_and_keeps_rows(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_DDL)
    raw.execute("INSERT INTO node_samples (node, t, cpu_pct) VALUES ('pi-1', 5.0, 9.0)")
    raw.commit()
    raw.close()
    assert "sd_read_bytes_per_s" not in _cols(path)

    conn = history._connect(path)  # what the pod does on start
    history._insert(conn, "pi-1", {"t": 6.0, "sd_read_bytes_per_s": 123.0})
    rows = conn.execute("SELECT t, cpu_pct, sd_read_bytes_per_s FROM node_samples ORDER BY t").fetchall()
    conn.close()

    assert _cols(path) == {"node", "t", *NODE_HISTORY_FIELDS}
    assert rows == [(5.0, 9.0, None), (6.0, None, 123.0)]


def test_history_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_DDL)
    raw.close()
    for _ in range(3):  # repeated starts / replicas restarting
        history._connect(path).close()
    conn = sqlite3.connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == len(history.MIGRATIONS)
    assert _cols(path) == {"node", "t", *NODE_HISTORY_FIELDS}


def test_history_migration_tolerates_unversioned_db_that_already_has_the_columns(tmp_path):
    """A database patched by hand (user_version still 0, columns present) must not fail
    with 'duplicate column name'."""
    path = str(tmp_path / "patched.db")
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_DDL)
    raw.execute("ALTER TABLE node_samples ADD COLUMN sd_read_bytes_per_s REAL")
    raw.execute("ALTER TABLE node_samples ADD COLUMN sd_write_bytes_per_s REAL")
    raw.close()
    history._connect(path).close()
    assert _cols(path) == {"node", "t", *NODE_HISTORY_FIELDS}


def test_history_refuses_database_from_a_newer_build(tmp_path):
    path = str(tmp_path / "future.db")
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {len(history.MIGRATIONS) + 1}")
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        history._connect(path)


def test_history_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_DDL)
    raw.close()

    def boom(conn):
        conn.execute("ALTER TABLE node_samples ADD COLUMN half_done REAL")
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(history, "MIGRATIONS", (*history.MIGRATIONS, boom))
    with pytest.raises(sqlite3.OperationalError):
        history._connect(path)
    assert "half_done" not in _cols(path)
    conn = sqlite3.connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == len(history.MIGRATIONS) - 1  # the earlier steps stay applied


def test_write_status_logs_error_once_then_throttles_and_recovers(caplog):
    status = history.WriteStatus()
    with caplog.at_level(logging.INFO, logger="piwatch.history"):
        status.record_failure(sqlite3.OperationalError("no such column"))
        status.record_failure(sqlite3.OperationalError("no such column"))
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "no such column" in errors[0].getMessage()
    assert status.healthy is False and status.failed_writes == 2

    with caplog.at_level(logging.INFO, logger="piwatch.history"):
        status.record_success()
    assert status.healthy is True
    assert any("recovered" in r.getMessage() for r in caplog.records)


def test_history_run_reports_failing_writes(monkeypatch, tmp_path, caplog):
    """End to end: a sample that cannot be written is logged as an error and shows up in
    history.status() -- it is no longer a silent warning."""
    import asyncio

    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_HISTORY_DB", str(tmp_path / "h.db"))
    monkeypatch.setattr(history, "write_status", history.WriteStatus())

    def failing_insert(conn, node, entry):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(history, "_insert", failing_insert)

    async def scenario():
        st = ClusterState()
        task = asyncio.create_task(history.run(st))
        await asyncio.sleep(0.05)
        st.record_node_sample("pi-1", {"cpu_pct": 1.0})
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.ERROR, logger="piwatch.history"):
        asyncio.run(scenario())

    assert any(r.levelno == logging.ERROR for r in caplog.records)
    status = history.status()
    assert status["ok"] is False and status["failed_writes"] == 1
    assert "disk I/O error" in status["last_error"]


def test_history_status_is_none_when_persistence_is_off(monkeypatch):
    monkeypatch.delenv("PIWATCH_HISTORY_DB", raising=False)
    assert history.status() is None
