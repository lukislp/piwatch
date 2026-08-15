"""Coverage-focused tests for k8s_watch, ws, metrics collectors and main's
startup-mode branches.

Follows the house style of test_backend.py: plain pytest functions,
`asyncio.run(scenario())` for ad-hoc async scenarios, `importlib.reload`
plus `monkeypatch.setenv` for modules that read environment variables at
import time, and `TestClient` for HTTP/WebSocket behavior.

Kubernetes interactions are always faked -- these tests never talk to a
real cluster or apiserver.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from datetime import datetime, timezone
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ==================================================================
# app.collectors.k8s_watch
# ==================================================================


def _node_obj(name="pi-1", role_label=True, with_optional=True):
    """Build a fake kubernetes_asyncio V1Node-shaped object."""
    conditions = [types.SimpleNamespace(type="Ready", status="True")]
    labels = {"node-role.kubernetes.io/control-plane": ""} if role_label else {}
    addresses = [types.SimpleNamespace(type="InternalIP", address="192.168.1.10")]
    node_info = (
        types.SimpleNamespace(
            architecture="arm64", kubelet_version="v1.29.4+k3s1", os_image="Debian 12"
        )
        if with_optional
        else None
    )
    status = types.SimpleNamespace(
        conditions=conditions,
        addresses=addresses,
        node_info=node_info,
        capacity={"cpu": "4", "memory": "8Gi"} if with_optional else None,
    )
    metadata = types.SimpleNamespace(
        name=name,
        labels=labels,
        creation_timestamp=datetime.now(timezone.utc) if with_optional else None,
    )
    spec = types.SimpleNamespace(unschedulable=False)
    return types.SimpleNamespace(status=status, metadata=metadata, spec=spec)


def _pod_obj(
    name="p1", namespace="default", waiting=False, no_statuses=False,
    oom=False, oom_in_last_state=False,
):
    if no_statuses:
        statuses = []
    else:
        state = types.SimpleNamespace(
            waiting=types.SimpleNamespace(reason="CrashLoopBackOff") if waiting else None,
            terminated=types.SimpleNamespace(reason="OOMKilled") if oom else None,
        )
        last_state = types.SimpleNamespace(
            terminated=types.SimpleNamespace(reason="OOMKilled") if oom_in_last_state else None
        )
        statuses = [
            types.SimpleNamespace(
                restart_count=2, ready=not waiting, state=state, last_state=last_state
            )
        ]
    metadata = types.SimpleNamespace(
        name=name, namespace=namespace, creation_timestamp=datetime.now(timezone.utc)
    )
    spec = types.SimpleNamespace(
        node_name="pi-1", containers=[types.SimpleNamespace(name="app", image="registry.local/app:v1")]
    )
    status = types.SimpleNamespace(
        container_statuses=statuses, phase="Running", reason=None
    )
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _deployment_obj(name="d1", namespace="default"):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    containers = [types.SimpleNamespace(image="registry.local/app:latest")]
    template = types.SimpleNamespace(spec=types.SimpleNamespace(containers=containers))
    spec = types.SimpleNamespace(replicas=2, template=template)
    status = types.SimpleNamespace(ready_replicas=2, available_replicas=2, updated_replicas=2)
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _event_obj(uid="evt-1"):
    metadata = types.SimpleNamespace(uid=uid, creation_timestamp=None)
    involved = types.SimpleNamespace(kind="Pod", name="p1", namespace="default")
    return types.SimpleNamespace(
        metadata=metadata,
        last_timestamp=datetime.now(timezone.utc),
        event_time=None,
        type="Normal",
        reason="Scheduled",
        message="assigned",
        involved_object=involved,
        count=3,
    )


def test_map_node_with_role_label_and_optionals():
    from app.collectors.k8s_watch import map_node

    d = map_node(_node_obj(role_label=True, with_optional=True))
    assert d["ready"] is True
    assert d["roles"] == ["control-plane"]
    assert d["arch"] == "arm64"
    assert d["internal_ip"] == "192.168.1.10"
    assert d["cpu_capacity"] == "4"
    assert d["created"] is not None


def test_map_node_defaults_worker_role_no_optionals():
    from app.collectors.k8s_watch import map_node

    d = map_node(_node_obj(role_label=False, with_optional=False))
    assert d["roles"] == ["worker"]  # no node-role.* label -> default fallback
    assert d["arch"] is None
    assert d["kubelet"] is None
    assert d["os_image"] is None
    assert d["cpu_capacity"] is None
    assert d["created"] is None


def test_map_pod_waiting_reason_and_ready_ratio():
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(waiting=True))
    assert d["key"] == "default/p1"
    assert d["reason"] == "CrashLoopBackOff"
    assert d["ready"] == "0/1"
    assert d["restarts"] == 2
    assert d["containers"] == ["app"]
    assert d["images"] == ["registry.local/app:v1"]
    assert d["oom_killed"] is False


def test_map_pod_no_container_statuses():
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(no_statuses=True))
    assert d["ready"] == "0/0"
    assert d["restarts"] == 0
    assert d["reason"] is None
    assert d["oom_killed"] is False


def test_map_pod_detects_oom_killed_in_current_state():
    """Rare in practice (usually recovered by the time it's observed), but
    the field exists on the real API object -- worth covering directly."""
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(oom=True))
    assert d["oom_killed"] is True


def test_map_pod_detects_oom_killed_in_last_state():
    """The common case: kubelet already restarted the container (it's back
    to Running), but last_state still records the OOM kill that caused it."""
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(oom_in_last_state=True))
    assert d["oom_killed"] is True


def test_map_deployment():
    from app.collectors.k8s_watch import map_deployment

    d = map_deployment(_deployment_obj())
    assert d["key"] == "default/d1"
    assert d["replicas"] == 2
    assert d["images"] == ["registry.local/app:latest"]


def test_map_event_uses_last_timestamp():
    from app.collectors.k8s_watch import map_event

    d = map_event(_event_obj())
    assert d["object"] == "Pod/p1"
    assert d["count"] == 3
    assert d["t"] is not None


def test_apply_all_kinds_upsert_and_delete():
    """Directly exercises _apply's dispatch table for every resource kind."""
    from app.collectors.k8s_watch import _apply
    from app.state import ClusterState

    st = ClusterState()

    _apply(st, "nodes", "ADDED", {"name": "pi-1"})
    assert "pi-1" in st.nodes
    _apply(st, "nodes", "DELETED", {"name": "pi-1"})
    assert "pi-1" not in st.nodes

    _apply(st, "pods", "ADDED", {"key": "ns/p1"})
    assert "ns/p1" in st.pods
    _apply(st, "pods", "DELETED", {"key": "ns/p1"})
    assert "ns/p1" not in st.pods

    _apply(st, "deployments", "ADDED", {"key": "ns/d1"})
    assert "ns/d1" in st.deployments
    _apply(st, "deployments", "DELETED", {"key": "ns/d1"})
    assert "ns/d1" not in st.deployments

    _apply(st, "events", "ADDED", {"uid": "e1"})
    assert len(st.events) == 1
    # a DELETED event is a no-op: events have no delete semantics
    _apply(st, "events", "DELETED", {"uid": "e2"})
    assert len(st.events) == 1


def test_load_config_incluster(monkeypatch):
    from kubernetes_asyncio import config as kconfig

    from app.collectors import k8s_watch

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    calls = []
    monkeypatch.setattr(kconfig, "load_incluster_config", lambda: calls.append("incluster"))
    asyncio.run(k8s_watch.load_config())
    assert calls == ["incluster"]


def test_load_config_kubeconfig(monkeypatch):
    from kubernetes_asyncio import config as kconfig

    from app.collectors import k8s_watch

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

    calls = []

    async def fake_load_kube_config():
        calls.append("kubeconfig")

    monkeypatch.setattr(kconfig, "load_kube_config", fake_load_kube_config)
    asyncio.run(k8s_watch.load_config())
    assert calls == ["kubeconfig"]


class _FakeApiClient:
    """Fakes kubernetes_asyncio.client.ApiClient as an async context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeList:
    def __init__(self, items, resource_version="1"):
        self.items = items
        self.metadata = types.SimpleNamespace(resource_version=resource_version)


class _FakeStream:
    """Async context manager + async iterator over scripted watch events,
    then raises to simulate a 410 Gone / connection drop."""

    def __init__(self, events, error):
        self._events = list(events)
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        raise self._error


class _FakeWatch:
    """Fakes kubernetes_asyncio.watch.Watch.

    Subclasses override `events`/`error` via `type(...)` per test; declared
    as ClassVar so ruff doesn't flag them as a mutable-default footgun.
    """

    events: ClassVar[list] = []
    error: ClassVar[Exception] = RuntimeError("410 Gone")

    def stream(self, lister, resource_version=None, timeout_seconds=None):
        return _FakeStream(self.__class__.events, self.__class__.error)


def _patch_k8s_client(monkeypatch, nodes=None, pods=None, deployments=None, events=None):
    """Patch kubernetes_asyncio.client's Api classes used by _watch_loop."""
    from kubernetes_asyncio import client as kclient

    class _FakeCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_node(self):
            return _FakeList(nodes or [])

        async def list_pod_for_all_namespaces(self):
            return _FakeList(pods or [])

        async def list_event_for_all_namespaces(self):
            return _FakeList(events or [])

    class _FakeAppsV1Api:
        def __init__(self, api_client):
            pass

        async def list_deployment_for_all_namespaces(self):
            return _FakeList(deployments or [])

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _FakeCoreV1Api)
    monkeypatch.setattr(kclient, "AppsV1Api", _FakeAppsV1Api)


@pytest.mark.parametrize(
    "kind,seed_kw,initial_obj,event_obj",
    [
        ("nodes", "nodes", _node_obj(), _node_obj(name="pi-2")),
        ("pods", "pods", _pod_obj(), _pod_obj(name="p2")),
        ("deployments", "deployments", _deployment_obj(), _deployment_obj(name="d2")),
        ("events", "events", _event_obj(), _event_obj(uid="e2")),
    ],
)
def test_watch_loop_seeds_state_then_reconnects_on_drop(
    monkeypatch, kind, seed_kw, initial_obj, event_obj
):
    """Exercises the initial list+seed, the live ADDED watch event, and the
    reconnect/backoff path (simulated 410 Gone -> log -> sleep -> propagate)."""
    from kubernetes_asyncio import watch as kwatch

    from app.collectors import k8s_watch
    from app.state import ClusterState

    _patch_k8s_client(monkeypatch, **{seed_kw: [initial_obj]})

    added = {"type": "ADDED", "object": event_obj}
    fake_watch_cls = type(
        "FakeWatch",
        (_FakeWatch,),
        {"events": [added], "error": RuntimeError("410 Gone")},
    )
    monkeypatch.setattr(kwatch, "Watch", fake_watch_cls)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()  # stop the infinite loop deterministically

    monkeypatch.setattr(k8s_watch.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, kind))

    collection = getattr(st, kind)
    # initial list seeds 1 item, the watch stream ADDs a second (different key)
    # before the simulated 410 Gone triggers the reconnect/backoff path
    assert len(collection) == 2
    assert sleep_calls == [1]  # backoff starts at 1s after the simulated 410


def test_watch_loop_unknown_kind_raises_value_error_and_backs_off(monkeypatch):
    """kind not in {nodes,pods,deployments,events} -> ValueError, caught by the
    generic except, triggering the same backoff/reconnect path."""
    from app.collectors import k8s_watch
    from app.state import ClusterState

    _patch_k8s_client(monkeypatch)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(k8s_watch.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, "bogus-kind"))
    assert sleep_calls == [1]


def test_watch_loop_propagates_cancellation_from_inside_try(monkeypatch):
    """A CancelledError raised while listing/watching must be re-raised as-is
    (not swallowed as a generic reconnect-worthy Exception)."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import k8s_watch
    from app.state import ClusterState

    class _CancellingCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_node(self):
            raise asyncio.CancelledError()

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _CancellingCoreV1Api)
    monkeypatch.setattr(kclient, "AppsV1Api", lambda api_client: None)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, "nodes"))


def test_watch_loop_backoff_grows_across_repeated_failures(monkeypatch):
    """First reconnect sleeps 1s and doubles the backoff; second reconnect
    must then sleep 2s -- covers the `backoff = min(backoff * 2, 30)` line.

    The initial list() must keep failing on every attempt here: a successful
    list resets backoff to 1 (line 135), so growth is only observable when
    every reconnect attempt fails before ever reaching that reset.
    """
    from kubernetes_asyncio import client as kclient

    from app.collectors import k8s_watch
    from app.state import ClusterState

    class _AlwaysFailingCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_node(self):
            raise RuntimeError("apiserver unreachable")

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _AlwaysFailingCoreV1Api)
    monkeypatch.setattr(kclient, "AppsV1Api", lambda api_client: None)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()  # stop after observing backoff growth

    monkeypatch.setattr(k8s_watch.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, "nodes"))
    assert sleep_calls == [1, 2]


def test_run_starts_all_four_watch_loops(monkeypatch):
    from app.collectors import k8s_watch
    from app.state import ClusterState

    calls = []

    async def fake_load_config():
        calls.append("config")

    async def fake_watch_loop(state, kind):
        calls.append(kind)

    monkeypatch.setattr(k8s_watch, "load_config", fake_load_config)
    monkeypatch.setattr(k8s_watch, "_watch_loop", fake_watch_loop)

    st = ClusterState()
    asyncio.run(k8s_watch.run(st))
    assert calls[0] == "config"
    assert set(calls[1:]) == {"nodes", "pods", "deployments", "events"}


# ==================================================================
# app.collectors.metrics
# ==================================================================


def test_parse_cpu_micro_suffix():
    from app.collectors.metrics import parse_cpu

    assert parse_cpu("500u") == pytest.approx(0.0005)


class _FakeCustomObjectsApi:
    """Serves separate item lists per `plural` (nodes vs. pods), since
    metrics.run() now queries both every iteration."""

    def __init__(self, items_by_plural=None, error=None):
        self._items_by_plural = items_by_plural or {}
        self._error = error
        self.calls = 0

    async def list_cluster_custom_object(self, group, version, plural):
        if self._error:
            raise self._error
        self.calls += 1
        return {"items": self._items_by_plural.get(plural, [])}


def test_metrics_run_records_valid_and_skips_unparsable(monkeypatch):
    """Happy path: one node with a known capacity, one node falling back to
    defaults, and one malformed entry hitting the KeyError/ValueError branch."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import metrics
    from app.state import ClusterState

    st = ClusterState()
    st.nodes["pi-1"] = {"cpu_capacity": "4", "mem_capacity": "8Gi"}
    # pi-2 intentionally absent from st.nodes -> exercises the default fallback

    items = [
        {"metadata": {"name": "pi-1"}, "usage": {"cpu": "200m", "memory": "512Mi"}},
        {"metadata": {"name": "pi-2"}, "usage": {"cpu": "100m", "memory": "256Mi"}},
        {"metadata": {"name": "pi-3"}, "usage": {"cpu": "not-a-number", "memory": "256Mi"}},
    ]
    fake_api = _FakeCustomObjectsApi({"nodes": items})
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(metrics.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(metrics.run(st))

    assert fake_api.calls == 2  # nodes + pods
    assert "pi-1" in st.node_metrics
    assert st.node_metrics["pi-1"]["cpu_pct"] == pytest.approx(5.0)  # 0.2 cores / 4
    assert "pi-2" in st.node_metrics  # used default 4-core / 8Gi fallback
    assert "pi-3" not in st.node_metrics  # unparsable usage.cpu -> skipped
    assert sleep_calls == [metrics.POLL_INTERVAL]


def test_metrics_run_retries_after_apiserver_error(monkeypatch):
    """metrics-server unreachable -> outer except -> warn + 30s backoff."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import metrics
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi(error=RuntimeError("connection refused"))
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(metrics.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(metrics.run(st))
    assert sleep_calls == [30]


def test_metrics_run_records_pod_usage_and_skips_unparsable(monkeypatch):
    """Same happy-path/skip semantics as node metrics, but per pod: each
    pod's containers are summed into one CPU/RAM sample."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import metrics
    from app.state import ClusterState

    st = ClusterState()

    pod_items = [
        {
            "metadata": {"name": "app-1", "namespace": "default"},
            "containers": [
                {"usage": {"cpu": "100m", "memory": "128Mi"}},
                {"usage": {"cpu": "50m", "memory": "64Mi"}},
            ],
        },
        {
            "metadata": {"name": "app-2", "namespace": "default"},
            "containers": [{"usage": {"cpu": "not-a-number", "memory": "64Mi"}}],
        },
    ]
    fake_api = _FakeCustomObjectsApi({"pods": pod_items})
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(metrics.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(metrics.run(st))

    assert st.pod_metrics["default/app-1"]["cpu_cores"] == pytest.approx(0.15)
    assert st.pod_metrics["default/app-1"]["mem_bytes"] == 128 * 1024**2 + 64 * 1024**2
    assert "default/app-2" not in st.pod_metrics  # unparsable usage.cpu -> skipped


# ==================================================================
# app.collectors.flux
# ==================================================================


def _kustomization_obj(
    name="piwatch-deploy", namespace="flux-system", ready=True, revision="main@sha1:abc123",
    interval=None, last_reconciled=None,
):
    obj = {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {},
        "status": {
            "lastAppliedRevision": revision,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if ready else "False",
                    "reason": "ReconciliationSucceeded" if ready else "ReconciliationFailed",
                    "message": "Applied revision: " + revision if ready else "build failed",
                    "lastTransitionTime": "2026-01-01T00:00:00Z",
                }
            ],
        },
    }
    if interval is not None:
        obj["spec"]["interval"] = interval
    if last_reconciled is not None:
        obj["status"]["history"] = [{"lastReconciled": last_reconciled}]
    return obj


def test_map_kustomization_extracts_ready_condition():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(_kustomization_obj(ready=True))
    assert d["key"] == "flux-system/piwatch-deploy"
    assert d["ready"] is True
    assert d["reason"] == "ReconciliationSucceeded"
    assert d["last_applied_revision"] == "main@sha1:abc123"


def test_map_kustomization_not_ready():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(_kustomization_obj(ready=False))
    assert d["ready"] is False
    assert d["reason"] == "ReconciliationFailed"


def test_map_kustomization_missing_ready_condition_defaults_to_not_ready():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization({"metadata": {"name": "x", "namespace": "ns"}, "status": {}})
    assert d["ready"] is False
    assert d["reason"] is None
    assert d["next_reconcile_t"] is None


def test_map_kustomization_computes_next_reconcile_from_history_and_interval():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(
        _kustomization_obj(interval="5m", last_reconciled="2026-01-01T00:00:00Z")
    )
    import datetime

    expected = datetime.datetime(2026, 1, 1, 0, 5, 0, tzinfo=datetime.timezone.utc).timestamp()
    assert d["next_reconcile_t"] == pytest.approx(expected)


def test_map_kustomization_falls_back_to_ready_transition_time_without_history():
    """No status.history yet (e.g. right after the resource is created) --
    falls back to the Ready condition's lastTransitionTime."""
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(_kustomization_obj(interval="1m"))
    import datetime

    expected = datetime.datetime(2026, 1, 1, 0, 1, 0, tzinfo=datetime.timezone.utc).timestamp()
    assert d["next_reconcile_t"] == pytest.approx(expected)


def test_map_kustomization_next_reconcile_none_without_interval():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(_kustomization_obj(last_reconciled="2026-01-01T00:00:00Z"))
    assert d["next_reconcile_t"] is None


def test_map_kustomization_extracts_resource_count_source_and_apply_pending():
    from app.collectors.flux import _map_kustomization

    obj = _kustomization_obj()
    obj["spec"]["sourceRef"] = {"kind": "GitRepository", "name": "piwatch"}
    obj["status"]["inventory"] = {
        "entries": [{"id": "monitoring_piwatch_apps_Deployment", "v": "v1"}, {"id": "monitoring_piwatch__Service", "v": "v1"}]
    }
    d = _map_kustomization(obj)
    assert d["managed_resource_count"] == 2
    assert d["source_kind"] == "GitRepository"
    assert d["source_name"] == "piwatch"
    assert d["source_namespace"] == "flux-system"  # falls back to the Kustomization's own namespace
    assert d["apply_pending"] is False  # lastAttemptedRevision unset -> nothing to compare


def test_map_kustomization_apply_pending_when_attempted_differs_from_applied():
    """A stuck/in-flight apply: the last attempt hasn't (yet, or ever) matched
    what's actually applied -- the Ready condition alone wouldn't catch this
    if it's still reporting the last successful state."""
    from app.collectors.flux import _map_kustomization

    obj = _kustomization_obj(revision="main@sha1:old111")
    obj["status"]["lastAttemptedRevision"] = "main@sha1:new222"
    d = _map_kustomization(obj)
    assert d["apply_pending"] is True


def test_map_kustomization_no_inventory_defaults_resource_count_to_zero():
    from app.collectors.flux import _map_kustomization

    d = _map_kustomization(_kustomization_obj())
    assert d["managed_resource_count"] == 0
    assert d["source_kind"] is None


def _git_repository_obj(name="piwatch", namespace="flux-system", ready=True, revision="main@sha1:abc123"):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"url": "https://github.com/lukislp/piwatch.git", "ref": {"branch": "master"}},
        "status": {
            "artifact": {"revision": revision, "lastUpdateTime": "2026-01-01T00:00:00Z"},
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if ready else "False",
                    "reason": "Succeeded" if ready else "Failed",
                    "message": f"stored artifact for revision '{revision}'" if ready else "auth failed",
                }
            ],
        },
    }


def test_map_git_repository_extracts_source_status():
    from app.collectors.flux import _map_git_repository

    d = _map_git_repository(_git_repository_obj())
    assert d["key"] == "flux-system/piwatch"
    assert d["ready"] is True
    assert d["url"] == "https://github.com/lukislp/piwatch.git"
    assert d["ref"] == "master"
    assert d["revision"] == "main@sha1:abc123"


def test_map_git_repository_not_ready_surfaces_reason():
    from app.collectors.flux import _map_git_repository

    d = _map_git_repository(_git_repository_obj(ready=False))
    assert d["ready"] is False
    assert d["reason"] == "Failed"
    assert "auth failed" in d["message"]


def _image_repository_obj(name="piwatch", namespace="flux-system", tag_count=18):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "status": {"lastScanResult": {"tagCount": tag_count, "scanTime": "2026-01-01T00:00:00Z"}},
    }


def _image_policy_obj(
    name="piwatch", namespace="flux-system", repo_name="piwatch", latest_tag="1.8.0", previous_tag="1.7.1",
):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"imageRepositoryRef": {"name": repo_name}},
        "status": {
            "conditions": [{"type": "Ready", "status": "True", "reason": "Succeeded", "message": "resolved"}],
            "latestRef": {"name": f"ghcr.io/lukislp/{repo_name}", "tag": latest_tag},
            "observedPreviousRef": {"name": f"ghcr.io/lukislp/{repo_name}", "tag": previous_tag},
        },
    }


def test_map_image_policy_joins_scan_result_by_repo_ref():
    from app.collectors.flux import _map_image_policy

    scan_by_repo = {"flux-system/piwatch": {"tag_count": 18, "scan_time": "2026-01-01T00:00:00Z"}}
    d = _map_image_policy(_image_policy_obj(), scan_by_repo)
    assert d["image"] == "ghcr.io/lukislp/piwatch"
    assert d["latest_tag"] == "1.8.0"
    assert d["previous_tag"] == "1.7.1"
    assert d["tag_count"] == 18
    assert d["last_scan_time"] == "2026-01-01T00:00:00Z"


def test_map_image_policy_degrades_gracefully_without_matching_scan():
    """The ImageRepository poll can fail independently (see
    test_flux_run_all_resource_kinds_degrade_independently) -- ImagePolicy
    mapping must still work with an empty scan_by_repo, just without scan info."""
    from app.collectors.flux import _map_image_policy

    d = _map_image_policy(_image_policy_obj(), {})
    assert d["latest_tag"] == "1.8.0"
    assert d["tag_count"] is None
    assert d["last_scan_time"] is None


def _image_update_automation_obj(name="piwatch", namespace="flux-system", ready=True):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True" if ready else "False",
                    "reason": "Succeeded" if ready else "Failed",
                    "message": "repository up-to-date",
                }
            ],
            "lastAutomationRunTime": "2026-01-01T00:00:00Z",
            "lastPushCommit": "abc123def456",
            "lastPushTime": "2026-01-01T00:00:00Z",
        },
    }


def test_map_image_update_automation_extracts_push_status():
    from app.collectors.flux import _map_image_update_automation

    d = _map_image_update_automation(_image_update_automation_obj())
    assert d["ready"] is True
    assert d["last_push_commit"] == "abc123def456"
    assert d["last_automation_run_time"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5m", 300),
        ("1h30m", 5400),
        ("30s", 30),
        ("1500ms", 1.5),
        ("90m", 5400),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_go_duration(value, expected):
    from app.collectors.flux import _parse_go_duration

    result = _parse_go_duration(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_iso_handles_zulu_suffix():
    from app.collectors.flux import _parse_iso

    assert _parse_iso("2026-01-01T00:00:00Z") is not None
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None


def test_flux_run_polls_and_publishes_kustomizations(monkeypatch):
    from kubernetes_asyncio import client as kclient

    from app.collectors import flux
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi({"kustomizations": [_kustomization_obj()]})
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(flux.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(flux.run(st))

    assert "flux-system/piwatch-deploy" in st.flux_kustomizations
    assert st.flux_kustomizations["flux-system/piwatch-deploy"]["ready"] is True
    assert sleep_calls == [flux.POLL_INTERVAL]


def test_flux_run_all_resource_kinds_degrade_independently_and_keep_polling(monkeypatch):
    """Flux is optional -- a missing CRD (or missing RBAC for it) must not
    crash the app. Each of the 5 resource kinds is polled in its own
    try/except, so even when ALL of them fail (e.g. no Flux installed at
    all), the loop still just does its normal POLL_INTERVAL cadence --
    the harsher RETRY_INTERVAL backoff is reserved for a structural failure
    outside any single resource kind (see the next test)."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import flux
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi(error=RuntimeError("404 Not Found"))
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(flux.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(flux.run(st))

    assert st.flux_kustomizations == {}
    assert st.flux_git_repositories == {}
    assert st.flux_image_policies == {}
    assert st.flux_image_automations == {}
    assert sleep_calls == [flux.POLL_INTERVAL]


def test_flux_run_backs_off_on_structural_failure(monkeypatch):
    """A failure outside any single resource kind's try/except (e.g. the
    ApiClient itself can't be constructed) hits the outer handler and backs
    off harder than a per-kind 404 would."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import flux
    from app.state import ClusterState

    class _BrokenApiClient:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *exc):
            return False

    st = ClusterState()
    monkeypatch.setattr(kclient, "ApiClient", _BrokenApiClient)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(flux.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(flux.run(st))

    assert sleep_calls == [flux.RETRY_INTERVAL]


def test_flux_run_polls_and_publishes_all_resource_kinds(monkeypatch):
    from kubernetes_asyncio import client as kclient

    from app.collectors import flux
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi(
        {
            "kustomizations": [_kustomization_obj()],
            "gitrepositories": [_git_repository_obj()],
            "imagerepositories": [_image_repository_obj()],
            "imagepolicies": [_image_policy_obj()],
            "imageupdateautomations": [_image_update_automation_obj()],
        }
    )
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(flux.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(flux.run(st))

    assert st.flux_kustomizations["flux-system/piwatch-deploy"]["ready"] is True
    assert st.flux_git_repositories["flux-system/piwatch"]["ready"] is True
    # the ImagePolicy's scan_by_repo join picked up the ImageRepository polled in the same cycle
    assert st.flux_image_policies["flux-system/piwatch"]["tag_count"] == 18
    assert st.flux_image_automations["flux-system/piwatch"]["last_push_commit"] == "abc123def456"


# ==================================================================
# app.ws
# ==================================================================


def _fresh_ws_app(monkeypatch, password=None):
    """Reload app.auth (to pick up the password) and app.ws (to pick up the
    reloaded verify_token), then wire ws.router into a minimal FastAPI app
    with a brand-new ClusterState so tests never see cross-test pollution."""
    if password is None:
        monkeypatch.delenv("PIWATCH_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("PIWATCH_PASSWORD", password)
    import app.auth as auth_mod
    import app.ws as ws_mod

    auth_mod = importlib.reload(auth_mod)
    ws_mod = importlib.reload(ws_mod)

    from app.state import ClusterState

    fresh_state = ClusterState()
    monkeypatch.setattr(ws_mod, "state", fresh_state)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(ws_mod.router)
    return app, ws_mod, auth_mod, fresh_state


def test_ws_state_unauthorized_closes_with_4401(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app, _ws_mod, _auth_mod, _fresh_state = _fresh_ws_app(monkeypatch, password="secret123")
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_text()
        assert excinfo.value.code == 4401


def test_ws_state_full_snapshot_then_broadcast(monkeypatch):
    from fastapi.testclient import TestClient

    app, _ws_mod, _auth_mod, fresh_state = _fresh_ws_app(monkeypatch, password=None)
    fresh_state.upsert_node("seed", {"name": "seed", "ready": True})

    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "full_state"
        assert "seed" in first["data"]["nodes"]

        # Publish a delta from the server's own event loop thread (via the
        # test session's portal) -- state.subscribe()'s Queue is not
        # cross-thread safe, so mutations must run on the portal's loop.
        websocket.portal.call(
            fresh_state.upsert_node, "pi-1", {"name": "pi-1", "ready": True}
        )
        second = websocket.receive_json()
        assert second["type"] == "node"
        assert second["data"]["name"] == "pi-1"

    assert len(fresh_state._subscribers) == 0  # unsubscribed in the finally block


def test_ws_state_heartbeat_ping_on_idle(monkeypatch):
    from fastapi.testclient import TestClient

    app, ws_mod, _auth_mod, _fresh_state = _fresh_ws_app(monkeypatch, password=None)
    monkeypatch.setattr(ws_mod, "HEARTBEAT_S", 0.05)

    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "full_state"
        second = websocket.receive_json()
        assert second["type"] == "ping"


def test_ws_logs_unauthorized_closes_with_4401(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app, _ws_mod, _auth_mod, _fresh_state = _fresh_ws_app(monkeypatch, password="secret123")
    client = TestClient(app)
    with client.websocket_connect("/ws/logs/default/mypod") as websocket:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_text()
        assert excinfo.value.code == 4401


def test_ws_logs_demo_mode_streams_fake_lines(monkeypatch):
    from fastapi.testclient import TestClient

    app, _ws_mod, _auth_mod, fresh_state = _fresh_ws_app(monkeypatch, password=None)
    fresh_state.demo_mode = True

    client = TestClient(app)
    with client.websocket_connect("/ws/logs/monitoring/piwatch-7c9d4-a") as websocket:
        msg = websocket.receive_json()
        assert msg["type"] == "log"
        assert "line" in msg


def test_ws_logs_non_demo_source_error_becomes_log_error(monkeypatch):
    """Real-cluster log source failures must surface as a 'log_error' message
    (covers the except branch in ws_logs' sender())."""
    from fastapi.testclient import TestClient

    app, ws_mod, _auth_mod, fresh_state = _fresh_ws_app(monkeypatch, password=None)
    fresh_state.demo_mode = False

    async def broken_source(namespace, pod, container):
        raise RuntimeError("pod not found")
        yield "unreachable"  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr(ws_mod, "_k8s_log_lines", broken_source)

    client = TestClient(app)
    with client.websocket_connect("/ws/logs/default/mypod") as websocket:
        msg = websocket.receive_json()
        assert msg["type"] == "log_error"
        assert "pod not found" in msg["error"]


class _FailingWebSocket:
    """Minimal fake satisfying the subset of the Starlette WebSocket API that
    ws_state() touches -- lets us drive the disconnect-during-send except
    branch directly and deterministically, without racing TestClient's
    threaded portal teardown."""

    def __init__(self, fail_exc):
        self.sent = []
        self.closed = None
        self._fail_exc = fail_exc

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def send_text(self, text):
        self.sent.append(text)
        raise self._fail_exc


def test_ws_state_send_disconnect_is_swallowed(monkeypatch):
    """A send() failure (peer went away) must be caught by the
    `except (WebSocketDisconnect, RuntimeError): pass` and still unsubscribe."""
    from starlette.websockets import WebSocketDisconnect

    import app.ws as ws_mod
    from app.state import ClusterState

    fresh_state = ClusterState()
    monkeypatch.setattr(ws_mod, "state", fresh_state)
    monkeypatch.delenv("PIWATCH_PASSWORD", raising=False)
    import app.auth as auth_mod

    importlib.reload(auth_mod)
    ws_mod = importlib.reload(ws_mod)
    monkeypatch.setattr(ws_mod, "state", fresh_state)

    fake_ws = _FailingWebSocket(WebSocketDisconnect(code=1001))
    asyncio.run(ws_mod.ws_state(fake_ws, token=None))  # must not raise
    assert len(fresh_state._subscribers) == 0  # finally still unsubscribed


def test_ws_state_send_runtime_error_is_swallowed(monkeypatch):
    import app.ws as ws_mod
    from app.state import ClusterState

    fresh_state = ClusterState()
    monkeypatch.setattr(ws_mod, "state", fresh_state)
    monkeypatch.delenv("PIWATCH_PASSWORD", raising=False)
    import app.auth as auth_mod

    importlib.reload(auth_mod)
    ws_mod = importlib.reload(ws_mod)
    monkeypatch.setattr(ws_mod, "state", fresh_state)

    fake_ws = _FailingWebSocket(RuntimeError("connection already closed"))
    asyncio.run(ws_mod.ws_state(fake_ws, token=None))  # must not raise
    assert len(fresh_state._subscribers) == 0


class _FakeLogContent:
    """Fakes the httpx-style streaming body (`resp.content`) that
    _k8s_log_lines iterates over."""

    def __init__(self, lines: list[str]):
        self._chunks = [line.encode() for line in lines]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        raise StopAsyncIteration


class _FakeLogResponse:
    def __init__(self, lines: list[str]):
        self.content = _FakeLogContent(lines)
        self.closed = False

    def close(self):
        self.closed = True


def test_k8s_log_lines_streams_and_closes_response(monkeypatch):
    """Direct test of the real (non-demo) log source generator: decodes raw
    chunks from the k8s API response and always closes it afterwards."""
    from kubernetes_asyncio import client as kclient

    import app.ws as ws_mod

    fake_resp = _FakeLogResponse(["hello world\n", "second line\n"])

    class _FakeCoreV1Api:
        def __init__(self, api_client):
            pass

        async def read_namespaced_pod_log(self, **kwargs):
            assert kwargs["name"] == "mypod"
            assert kwargs["namespace"] == "default"
            return fake_resp

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _FakeCoreV1Api)

    async def scenario():
        return [
            line
            async for line in ws_mod._k8s_log_lines("default", "mypod", None)
        ]

    lines = asyncio.run(scenario())
    assert lines == ["hello world", "second line"]
    assert fake_resp.closed is True


# ==================================================================
# app.main -- startup-mode branches
# ==================================================================


def _reload_main(monkeypatch, **env):
    """Set/clear env vars then reload app.main so its module-level reads
    (STATIC_DIR, etc.) pick up the new values. Also swaps in a brand-new
    ClusterState: app.state.state is a process-wide singleton, and earlier
    tests (in this file or test_backend.py) may have already flipped
    demo_mode / seeded nodes on it, which would silently poison readyz/
    get_state assertions here."""
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import app.main as main_mod

    main_mod = importlib.reload(main_mod)

    from app.state import ClusterState

    monkeypatch.setattr(main_mod, "state", ClusterState())
    return main_mod


def test_cluster_reachable_true_when_in_cluster_env(monkeypatch):
    main_mod = _reload_main(monkeypatch, KUBERNETES_SERVICE_HOST="10.0.0.1", PIWATCH_DEMO=None)
    assert asyncio.run(main_mod._cluster_reachable()) is True


def test_cluster_reachable_true_when_kubeconfig_loads(monkeypatch):
    main_mod = _reload_main(monkeypatch, KUBERNETES_SERVICE_HOST=None, PIWATCH_DEMO=None)

    async def ok_load_kube_config():
        return None

    from kubernetes_asyncio import config as kconfig

    monkeypatch.setattr(kconfig, "load_kube_config", ok_load_kube_config)
    assert asyncio.run(main_mod._cluster_reachable()) is True


def test_cluster_reachable_false_when_no_kubeconfig(monkeypatch):
    main_mod = _reload_main(monkeypatch, KUBERNETES_SERVICE_HOST=None, PIWATCH_DEMO=None)

    async def failing_load_kube_config():
        raise FileNotFoundError("no kubeconfig")

    from kubernetes_asyncio import config as kconfig

    monkeypatch.setattr(kconfig, "load_kube_config", failing_load_kube_config)
    assert asyncio.run(main_mod._cluster_reachable()) is False


def test_lifespan_real_cluster_mode_starts_k8s_collectors(monkeypatch, tmp_path):
    """PIWATCH_DEMO unset + reachable cluster -> lifespan starts the
    k8s_watch/metrics/hardware collectors (not demo.run)."""
    nostatic = tmp_path / "nostatic"  # deliberately not created -> else branch
    main_mod = _reload_main(
        monkeypatch,
        PIWATCH_DEMO=None,
        KUBERNETES_SERVICE_HOST="10.0.0.1",
        PIWATCH_STATIC_DIR=str(nostatic),
    )

    started = []

    async def fake_collector(state):
        started.append(state)
        await asyncio.sleep(3600)  # stays alive until lifespan cancels it

    monkeypatch.setattr(main_mod.k8s_watch, "run", fake_collector)
    monkeypatch.setattr(main_mod.metrics, "run", fake_collector)
    monkeypatch.setattr(main_mod.hardware, "run", fake_collector)
    monkeypatch.setattr(main_mod.flux, "run", fake_collector)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        # not demo mode and no nodes seeded yet -> not ready
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json() == {"ready": False}
    assert len(started) == 4  # k8s_watch, metrics, hardware, flux all launched


def test_get_state_endpoint_returns_snapshot(monkeypatch, tmp_path):
    nostatic = tmp_path / "nostatic"
    main_mod = _reload_main(
        monkeypatch, PIWATCH_DEMO="1", PIWATCH_STATIC_DIR=str(nostatic), PIWATCH_PASSWORD=None
    )
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        assert "nodes" in body and "demo_mode" in body


def test_readyz_true_in_demo_mode(monkeypatch, tmp_path):
    nostatic = tmp_path / "nostatic"
    main_mod = _reload_main(monkeypatch, PIWATCH_DEMO="1", PIWATCH_STATIC_DIR=str(nostatic))
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        assert r.json() == {"ready": True}


def test_spa_serves_existing_file_directly(monkeypatch, tmp_path):
    """A real file at the STATIC_DIR root (outside /assets) is served as-is
    via FileResponse(candidate), not the index.html fallback."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>index</html>")
    (static / "favicon.ico").write_text("ICO-BYTES")

    main_mod = _reload_main(
        monkeypatch, PIWATCH_DEMO="1", PIWATCH_STATIC_DIR=str(static)
    )
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        r = client.get("/favicon.ico")
        assert r.text == "ICO-BYTES"


def test_static_dir_missing_logs_warning_and_skips_mount(monkeypatch, tmp_path, caplog):
    nostatic = tmp_path / "does-not-exist"
    with caplog.at_level("WARNING", logger="piwatch"):
        main_mod = _reload_main(
            monkeypatch, PIWATCH_DEMO="1", PIWATCH_STATIC_DIR=str(nostatic)
        )
    assert any("missing" in rec.message for rec in caplog.records)
    # no catch-all route registered -> unknown paths 404 instead of falling
    # back to a (non-existent) index.html
    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        r = client.get("/some/random/path")
        assert r.status_code == 404
