"""Unit tests for the PiWatch backend core (auth, state, parsers, checks)."""
import asyncio
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------- auth ----------------

def _fresh_auth(monkeypatch, password: str | None):
    if password is None:
        monkeypatch.delenv("PIWATCH_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("PIWATCH_PASSWORD", password)
    import app.auth as auth_mod

    return importlib.reload(auth_mod)


def test_token_roundtrip(monkeypatch):
    auth = _fresh_auth(monkeypatch, "testsecret123")
    token = auth.create_token()
    assert auth.verify_token(token)


def test_token_tampering_rejected(monkeypatch):
    auth = _fresh_auth(monkeypatch, "testsecret123")
    token = auth.create_token()
    assert not auth.verify_token(token[:-2] + "xx")
    assert not auth.verify_token("garbage")
    assert not auth.verify_token(None)


def test_auth_disabled_without_password(monkeypatch):
    auth = _fresh_auth(monkeypatch, None)
    assert not auth.auth_enabled()
    assert auth.verify_token(None)  # everything allowed


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("PIWATCH_TOKEN_TTL", "-10")
    auth = _fresh_auth(monkeypatch, "testsecret123")
    assert not auth.verify_token(auth.create_token())


def test_cross_replica_token(monkeypatch):
    """Token issued by replica A must validate on replica B (same secret)."""
    auth_a = _fresh_auth(monkeypatch, "testsecret123")
    token = auth_a.create_token()
    auth_b = _fresh_auth(monkeypatch, "testsecret123")  # simulates second replica
    assert auth_b.verify_token(token)


# ---------------- state / pub-sub ----------------

def test_state_publish_and_snapshot():
    from app.state import ClusterState

    async def scenario():
        st = ClusterState()
        q = st.subscribe()
        st.upsert_node("pi-1", {"name": "pi-1", "ready": True})
        msg = q.get_nowait()
        assert msg["type"] == "node"
        assert msg["data"]["name"] == "pi-1"
        snap = st.snapshot()
        assert "pi-1" in snap["nodes"]
        st.unsubscribe(q)

    asyncio.run(scenario())


def test_node_history_ring_buffer():
    from app.state import HISTORY_LEN, ClusterState

    async def scenario():
        st = ClusterState()
        for i in range(HISTORY_LEN + 50):
            st.record_node_sample("pi-1", {"cpu_pct": float(i % 100)})
        assert len(st.node_history["pi-1"]) == HISTORY_LEN

    asyncio.run(scenario())


def test_hardware_merges_uptime_into_node_metrics():
    """Regression: Overview reads uptime_s from node_metrics."""
    from app.state import ClusterState

    async def scenario():
        st = ClusterState()
        st.record_hardware("pi-1", {"temp_c": 50.0, "uptime_s": 12345, "disk_used_pct": 40.0})
        assert st.node_metrics["pi-1"]["uptime_s"] == 12345
        assert st.node_metrics["pi-1"]["temp_c"] == 50.0

    asyncio.run(scenario())


def test_spa_path_traversal_blocked(tmp_path, monkeypatch):
    """Regression: '../' in the SPA catch-all must never leave STATIC_DIR."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>ok</html>")
    (tmp_path / "secret.txt").write_text("should-not-leak")
    monkeypatch.setenv("PIWATCH_STATIC_DIR", str(static))
    monkeypatch.setenv("PIWATCH_DEMO", "1")
    import app.main as main_mod

    main_mod = importlib.reload(main_mod)
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        r = client.get("/../secret.txt")
        assert "should-not-leak" not in r.text  # falls back to index.html
        # urllib-style raw traversal via the route param:
        r = client.get("/%2e%2e/secret.txt")
        assert "should-not-leak" not in r.text
        assert client.get("/healthz").json() == {"ok": True}


def test_healthcheck_uptime_calculation():
    from app.state import ClusterState

    async def scenario():
        st = ClusterState()
        cfg = {"name": "svc", "type": "http", "url": "http://x"}
        for ok in [True, True, True, False]:
            st.record_check("svc", cfg, ok, 10.0 if ok else None)
        entry = st.healthchecks["svc"]
        assert entry["uptime_pct"] == 75.0
        assert entry["last"]["ok"] is False

    asyncio.run(scenario())


def test_remove_check_deletes_and_publishes_only_when_present():
    from app.state import ClusterState

    async def scenario():
        st = ClusterState()
        q = st.subscribe()
        st.record_check("svc", {"name": "svc"}, True, 5.0)
        q.get_nowait()  # drain the "healthcheck" publish from record_check above

        st.remove_check("svc")
        assert "svc" not in st.healthchecks
        msg = q.get_nowait()
        assert msg["type"] == "healthcheck_deleted"
        assert msg["data"] == {"name": "svc"}

        # a second removal (already gone) must not publish again
        st.remove_check("svc")
        assert q.empty()

    asyncio.run(scenario())


# ---------------- kubernetes quantity parsers ----------------

@pytest.mark.parametrize(
    "value,expected",
    [("250m", 0.25), ("1", 1.0), ("2", 2.0), ("1500000000n", 1.5)],
)
def test_parse_cpu(value, expected):
    from app.collectors.metrics import parse_cpu

    assert parse_cpu(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value,expected",
    [("1024Ki", 1024 * 1024), ("8Gi", 8 * 1024**3), ("512Mi", 512 * 1024**2), ("1000", 1000.0)],
)
def test_parse_mem(value, expected):
    from app.collectors.metrics import parse_mem

    assert parse_mem(value) == pytest.approx(expected)
