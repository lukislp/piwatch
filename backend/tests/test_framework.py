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
import contextlib
import importlib
import os
import ssl
import sys
import time
import types
from datetime import datetime, timezone
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ==================================================================
# app.collectors.k8s_watch
# ==================================================================


def _node_obj(name="pi-1", role_label=True, with_optional=True, unschedulable=False, taints=None):
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
    spec = types.SimpleNamespace(unschedulable=unschedulable, taints=taints)
    return types.SimpleNamespace(status=status, metadata=metadata, spec=spec)


def _pod_obj(
    name="p1", namespace="default", waiting=False, no_statuses=False,
    oom=False, oom_in_last_state=False, terminated=None, last_terminated=None,
):
    """terminated/last_terminated: optional (reason, exit_code) tuples for the
    container's current/last state, independent of the oom convenience flags
    (which just set reason="OOMKilled", exit_code=137 -- SIGKILL)."""
    if no_statuses:
        statuses = []
    else:
        cur_reason, cur_code = ("OOMKilled", 137) if oom else (terminated or (None, None))
        last_reason, last_code = ("OOMKilled", 137) if oom_in_last_state else (last_terminated or (None, None))
        state = types.SimpleNamespace(
            waiting=types.SimpleNamespace(reason="CrashLoopBackOff") if waiting else None,
            terminated=types.SimpleNamespace(reason=cur_reason, exit_code=cur_code) if cur_reason else None,
        )
        last_state = types.SimpleNamespace(
            terminated=types.SimpleNamespace(reason=last_reason, exit_code=last_code) if last_reason else None
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


def _statefulset_obj(name="s1", namespace="default"):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    containers = [types.SimpleNamespace(image="registry.local/app:latest")]
    template = types.SimpleNamespace(spec=types.SimpleNamespace(containers=containers))
    spec = types.SimpleNamespace(replicas=3, template=template)
    status = types.SimpleNamespace(ready_replicas=3, updated_replicas=3)
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _daemonset_obj(name="ds1", namespace="default"):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    containers = [types.SimpleNamespace(image="registry.local/agent:latest")]
    template = types.SimpleNamespace(spec=types.SimpleNamespace(containers=containers))
    spec = types.SimpleNamespace(template=template)
    status = types.SimpleNamespace(
        desired_number_scheduled=3, number_ready=3, updated_number_scheduled=3
    )
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _service_obj(name="svc1", namespace="default", svc_type="LoadBalancer", ingress=None):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    spec = types.SimpleNamespace(
        type=svc_type,
        cluster_ip="10.43.1.1",
        ports=[types.SimpleNamespace(port=443, protocol="TCP", name="https")],
    )
    lb = types.SimpleNamespace(ingress=ingress) if ingress is not None else types.SimpleNamespace(ingress=None)
    status = types.SimpleNamespace(load_balancer=lb)
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _hpa_obj(
    name="h1", namespace="default", target_kind="Deployment", target_name="d1",
    min_replicas=1, max_replicas=5, current_replicas=2, desired_replicas=2,
    target_pct=70, current_pct=82, metric_type="Resource",
    able_to_scale="True", scaling_active="True", scaling_limited="False",
):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    target = types.SimpleNamespace(kind=target_kind, name=target_name)
    metric_target = types.SimpleNamespace(average_utilization=target_pct)
    resource_metric = types.SimpleNamespace(name="cpu", target=metric_target)
    metrics = [types.SimpleNamespace(type=metric_type, resource=resource_metric)]
    spec = types.SimpleNamespace(
        scale_target_ref=target, min_replicas=min_replicas, max_replicas=max_replicas,
        metrics=metrics,
    )
    metric_current = types.SimpleNamespace(average_utilization=current_pct)
    resource_current = types.SimpleNamespace(name="cpu", current=metric_current)
    current_metrics = [types.SimpleNamespace(type=metric_type, resource=resource_current)]
    conditions = [
        types.SimpleNamespace(type="AbleToScale", status=able_to_scale),
        types.SimpleNamespace(type="ScalingActive", status=scaling_active),
        types.SimpleNamespace(type="ScalingLimited", status=scaling_limited),
    ]
    status = types.SimpleNamespace(
        current_replicas=current_replicas, desired_replicas=desired_replicas,
        current_metrics=current_metrics, conditions=conditions,
    )
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def _networkpolicy_obj(
    name="np1", namespace="default", match_labels=None,
    policy_types=("Ingress",), ingress=(), egress=(),
):
    pod_selector = types.SimpleNamespace(match_labels=match_labels)
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    spec = types.SimpleNamespace(
        pod_selector=pod_selector, policy_types=list(policy_types),
        ingress=list(ingress), egress=list(egress),
    )
    return types.SimpleNamespace(metadata=metadata, spec=spec)


def _pv_obj(
    name="pv1", phase="Released", capacity="5Gi", storage_class="local-path",
    reclaim_policy="Retain", claim_namespace="home", claim_name="old-claim",
):
    metadata = types.SimpleNamespace(name=name)
    claim_ref = (
        types.SimpleNamespace(namespace=claim_namespace, name=claim_name)
        if claim_namespace or claim_name
        else None
    )
    spec = types.SimpleNamespace(
        capacity={"storage": capacity} if capacity else {},
        storage_class_name=storage_class,
        persistent_volume_reclaim_policy=reclaim_policy,
        claim_ref=claim_ref,
    )
    status = types.SimpleNamespace(phase=phase)
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
    assert d["unschedulable"] is False
    assert d["taints"] == []


def test_map_node_defaults_worker_role_no_optionals():
    from app.collectors.k8s_watch import map_node

    d = map_node(_node_obj(role_label=False, with_optional=False))
    assert d["roles"] == ["worker"]  # no node-role.* label -> default fallback
    assert d["arch"] is None
    assert d["kubelet"] is None
    assert d["os_image"] is None
    assert d["cpu_capacity"] is None
    assert d["created"] is None


def test_map_node_extracts_cordon_and_taints():
    from app.collectors.k8s_watch import map_node

    taints = [
        types.SimpleNamespace(key="dedicated", value="storage", effect="PreferNoSchedule"),
        types.SimpleNamespace(key="node.kubernetes.io/unreachable", value=None, effect="NoExecute"),
    ]
    d = map_node(_node_obj(unschedulable=True, taints=taints))
    assert d["unschedulable"] is True
    assert d["taints"] == [
        {"key": "dedicated", "value": "storage", "effect": "PreferNoSchedule"},
        {"key": "node.kubernetes.io/unreachable", "value": None, "effect": "NoExecute"},
    ]


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


def test_map_pod_last_exit_reason_prefers_current_terminated_state():
    """A container terminated RIGHT NOW (e.g. a finished one-shot container)
    takes priority over last_state."""
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(terminated=("Completed", 0), last_terminated=("Error", 1)))
    assert d["last_exit_reason"] == "Completed"
    assert d["last_exit_code"] == 0


def test_map_pod_last_exit_reason_falls_back_to_last_state():
    """Common case: kubelet already restarted the container back to Running,
    but last_state still holds the forensic info about the crash."""
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(last_terminated=("Error", 1)))
    assert d["last_exit_reason"] == "Error"
    assert d["last_exit_code"] == 1


def test_map_pod_last_exit_reason_none_when_never_terminated():
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj())
    assert d["last_exit_reason"] is None
    assert d["last_exit_code"] is None


def test_map_pod_oom_killed_sets_last_exit_reason_too():
    from app.collectors.k8s_watch import map_pod

    d = map_pod(_pod_obj(oom=True))
    assert d["last_exit_reason"] == "OOMKilled"
    assert d["last_exit_code"] == 137


def test_map_deployment():
    from app.collectors.k8s_watch import map_deployment

    d = map_deployment(_deployment_obj())
    assert d["key"] == "default/d1"
    assert d["replicas"] == 2
    assert d["images"] == ["registry.local/app:latest"]


def test_map_statefulset():
    from app.collectors.k8s_watch import map_statefulset

    d = map_statefulset(_statefulset_obj())
    assert d["key"] == "default/s1"
    assert d["replicas"] == 3
    assert d["ready"] == 3
    assert d["updated"] == 3
    assert d["images"] == ["registry.local/app:latest"]


def test_map_daemonset():
    from app.collectors.k8s_watch import map_daemonset

    d = map_daemonset(_daemonset_obj())
    assert d["key"] == "default/ds1"
    assert d["desired"] == 3
    assert d["ready"] == 3
    assert d["updated"] == 3
    assert d["images"] == ["registry.local/agent:latest"]


def test_map_service_extracts_external_ip_from_ingress():
    from app.collectors.k8s_watch import map_service

    ingress = [types.SimpleNamespace(ip="192.168.1.50", hostname=None)]
    d = map_service(_service_obj(ingress=ingress))
    assert d["key"] == "default/svc1"
    assert d["type"] == "LoadBalancer"
    assert d["cluster_ip"] == "10.43.1.1"
    assert d["external_ips"] == ["192.168.1.50"]
    assert d["ports"] == [{"port": 443, "protocol": "TCP", "name": "https"}]


def test_map_service_falls_back_to_hostname_when_no_ip():
    from app.collectors.k8s_watch import map_service

    ingress = [types.SimpleNamespace(ip=None, hostname="lb.example.com")]
    d = map_service(_service_obj(ingress=ingress))
    assert d["external_ips"] == ["lb.example.com"]


def test_map_service_no_ingress_yet_is_empty_external_ips():
    """Pending: LoadBalancer type but the controller hasn't assigned an address yet."""
    from app.collectors.k8s_watch import map_service

    d = map_service(_service_obj(ingress=None))
    assert d["external_ips"] == []


def test_map_hpa_extracts_target_replicas_and_resource_metrics():
    from app.collectors.k8s_watch import map_hpa

    d = map_hpa(_hpa_obj())
    assert d["key"] == "default/h1"
    assert d["target_kind"] == "Deployment"
    assert d["target_name"] == "d1"
    assert d["min_replicas"] == 1
    assert d["max_replicas"] == 5
    assert d["current_replicas"] == 2
    assert d["desired_replicas"] == 2
    assert d["metrics"] == [{"name": "cpu", "target_pct": 70}]
    assert d["current_metrics"] == [{"name": "cpu", "current_pct": 82}]
    assert d["able_to_scale"] == "True"
    assert d["scaling_active"] == "True"
    assert d["scaling_limited"] == "False"


def test_map_hpa_ignores_non_resource_metrics():
    """Pods/Object/External metrics don't have a universal target-% shape --
    see the comment in map_hpa. Just skipped, not an error."""
    from app.collectors.k8s_watch import map_hpa

    d = map_hpa(_hpa_obj(metric_type="External"))
    assert d["metrics"] == []
    assert d["current_metrics"] == []


def test_map_network_policy_extracts_selector_types_and_rule_counts():
    from app.collectors.k8s_watch import map_network_policy

    obj = _networkpolicy_obj(
        match_labels={"app": "mosquitto"}, policy_types=["Ingress", "Egress"],
        ingress=[object()], egress=[object(), object()],
    )
    d = map_network_policy(obj)
    assert d["key"] == "default/np1"
    assert d["pod_selector"] == "app=mosquitto"
    assert d["policy_types"] == ["Ingress", "Egress"]
    assert d["ingress_rules"] == 1
    assert d["egress_rules"] == 2


def test_map_network_policy_no_selector_labels_means_all_pods():
    from app.collectors.k8s_watch import map_network_policy

    d = map_network_policy(_networkpolicy_obj(match_labels=None))
    assert d["pod_selector"] == "(all pods)"


def test_map_pv_extracts_phase_capacity_and_stale_claim_ref():
    from app.collectors.k8s_watch import map_pv

    d = map_pv(_pv_obj())
    assert d["key"] == "pv1"
    assert d["phase"] == "Released"
    assert d["capacity"] == "5Gi"
    assert d["storage_class"] == "local-path"
    assert d["reclaim_policy"] == "Retain"
    assert d["claim_namespace"] == "home"
    assert d["claim_name"] == "old-claim"


def test_map_pv_no_claim_ref_when_never_bound():
    from app.collectors.k8s_watch import map_pv

    d = map_pv(_pv_obj(claim_namespace=None, claim_name=None))
    assert d["claim_namespace"] is None
    assert d["claim_name"] is None


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

    _apply(st, "statefulsets", "ADDED", {"key": "ns/s1"})
    assert "ns/s1" in st.statefulsets
    _apply(st, "statefulsets", "DELETED", {"key": "ns/s1"})
    assert "ns/s1" not in st.statefulsets

    _apply(st, "daemonsets", "ADDED", {"key": "ns/ds1"})
    assert "ns/ds1" in st.daemonsets
    _apply(st, "daemonsets", "DELETED", {"key": "ns/ds1"})
    assert "ns/ds1" not in st.daemonsets

    _apply(st, "services", "ADDED", {"key": "ns/svc1", "type": "LoadBalancer"})
    assert "ns/svc1" in st.services
    # a Service edited to no longer be LoadBalancer must be actively removed
    _apply(st, "services", "MODIFIED", {"key": "ns/svc1", "type": "ClusterIP"})
    assert "ns/svc1" not in st.services
    _apply(st, "services", "ADDED", {"key": "ns/svc1", "type": "LoadBalancer"})
    _apply(st, "services", "DELETED", {"key": "ns/svc1", "type": "LoadBalancer"})
    assert "ns/svc1" not in st.services

    _apply(st, "hpas", "ADDED", {"key": "ns/h1"})
    assert "ns/h1" in st.hpas
    _apply(st, "hpas", "DELETED", {"key": "ns/h1"})
    assert "ns/h1" not in st.hpas

    _apply(st, "networkpolicies", "ADDED", {"key": "ns/np1"})
    assert "ns/np1" in st.network_policies
    _apply(st, "networkpolicies", "DELETED", {"key": "ns/np1"})
    assert "ns/np1" not in st.network_policies

    _apply(st, "persistentvolumes", "ADDED", {"key": "pv1", "phase": "Released"})
    assert "pv1" in st.orphaned_pvs
    # a PV rebound back to a healthy phase must be actively removed
    _apply(st, "persistentvolumes", "MODIFIED", {"key": "pv1", "phase": "Bound"})
    assert "pv1" not in st.orphaned_pvs
    _apply(st, "persistentvolumes", "ADDED", {"key": "pv1", "phase": "Failed"})
    _apply(st, "persistentvolumes", "DELETED", {"key": "pv1", "phase": "Failed"})
    assert "pv1" not in st.orphaned_pvs

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


def _patch_k8s_client(
    monkeypatch, nodes=None, pods=None, deployments=None, statefulsets=None, daemonsets=None,
    services=None, hpas=None, networkpolicies=None, persistentvolumes=None, events=None,
):
    """Patch kubernetes_asyncio.client's Api classes used by _watch_loop."""
    from kubernetes_asyncio import client as kclient

    class _FakeCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_node(self):
            return _FakeList(nodes or [])

        async def list_pod_for_all_namespaces(self):
            return _FakeList(pods or [])

        async def list_service_for_all_namespaces(self):
            return _FakeList(services or [])

        async def list_persistent_volume(self):
            return _FakeList(persistentvolumes or [])

        async def list_event_for_all_namespaces(self):
            return _FakeList(events or [])

    class _FakeAppsV1Api:
        def __init__(self, api_client):
            pass

        async def list_deployment_for_all_namespaces(self):
            return _FakeList(deployments or [])

        async def list_stateful_set_for_all_namespaces(self):
            return _FakeList(statefulsets or [])

        async def list_daemon_set_for_all_namespaces(self):
            return _FakeList(daemonsets or [])

    class _FakeAutoscalingV2Api:
        def __init__(self, api_client):
            pass

        async def list_horizontal_pod_autoscaler_for_all_namespaces(self):
            return _FakeList(hpas or [])

    class _FakeNetworkingV1Api:
        def __init__(self, api_client):
            pass

        async def list_network_policy_for_all_namespaces(self):
            return _FakeList(networkpolicies or [])

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _FakeCoreV1Api)
    monkeypatch.setattr(kclient, "AppsV1Api", _FakeAppsV1Api)
    monkeypatch.setattr(kclient, "AutoscalingV2Api", _FakeAutoscalingV2Api)
    monkeypatch.setattr(kclient, "NetworkingV1Api", _FakeNetworkingV1Api)


@pytest.mark.parametrize(
    "kind,seed_kw,initial_obj,event_obj",
    [
        ("nodes", "nodes", _node_obj(), _node_obj(name="pi-2")),
        ("pods", "pods", _pod_obj(), _pod_obj(name="p2")),
        ("deployments", "deployments", _deployment_obj(), _deployment_obj(name="d2")),
        ("statefulsets", "statefulsets", _statefulset_obj(), _statefulset_obj(name="s2")),
        ("daemonsets", "daemonsets", _daemonset_obj(), _daemonset_obj(name="ds2")),
        ("services", "services", _service_obj(), _service_obj(name="svc2")),
        ("hpas", "hpas", _hpa_obj(), _hpa_obj(name="h2")),
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


def test_watch_loop_seeds_orphaned_pvs_then_reconnects_on_drop(monkeypatch):
    """Same as test_watch_loop_seeds_state_then_reconnects_on_drop above, but kept
    separate: "persistentvolumes" (the k8s kind) maps to st.orphaned_pvs (a
    differently-named state dict, since only Released/Failed PVs are ever stored
    there), so the generic getattr(st, kind) pattern the other cases share doesn't
    apply here."""
    from kubernetes_asyncio import watch as kwatch

    from app.collectors import k8s_watch
    from app.state import ClusterState

    _patch_k8s_client(monkeypatch, persistentvolumes=[_pv_obj(name="pv1")])

    added = {"type": "ADDED", "object": _pv_obj(name="pv2")}
    fake_watch_cls = type(
        "FakeWatch", (_FakeWatch,), {"events": [added], "error": RuntimeError("410 Gone")}
    )
    monkeypatch.setattr(kwatch, "Watch", fake_watch_cls)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(k8s_watch.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, "persistentvolumes"))

    assert set(st.orphaned_pvs) == {"pv1", "pv2"}
    assert sleep_calls == [1]


def test_watch_loop_seeds_network_policies_then_reconnects_on_drop(monkeypatch):
    """Same as test_watch_loop_seeds_orphaned_pvs above: "networkpolicies" (the k8s
    kind, matching kubectl's own plural) maps to st.network_policies (Python
    snake_case), so the generic getattr(st, kind) pattern doesn't apply here either."""
    from kubernetes_asyncio import watch as kwatch

    from app.collectors import k8s_watch
    from app.state import ClusterState

    _patch_k8s_client(monkeypatch, networkpolicies=[_networkpolicy_obj(name="np1")])

    added = {"type": "ADDED", "object": _networkpolicy_obj(name="np2")}
    fake_watch_cls = type(
        "FakeWatch", (_FakeWatch,), {"events": [added], "error": RuntimeError("410 Gone")}
    )
    monkeypatch.setattr(kwatch, "Watch", fake_watch_cls)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(k8s_watch.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(k8s_watch._watch_loop(st, "networkpolicies"))

    assert set(st.network_policies) == {"default/np1", "default/np2"}
    assert sleep_calls == [1]


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


def test_run_starts_all_ten_watch_loops(monkeypatch):
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
    assert set(calls[1:]) == {
        "nodes", "pods", "deployments", "statefulsets", "daemonsets", "services", "hpas",
        "networkpolicies", "persistentvolumes", "events",
    }


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
# app.collectors.pvc
# ==================================================================


def _pvc_obj(
    name="home-assistant-config", namespace="home", requested="5Gi", capacity="5Gi",
    storage_class="local-path", access_modes=None, volume_name="pvc-abc123", phase="Bound",
    has_resources=True,
):
    metadata = types.SimpleNamespace(name=name, namespace=namespace)
    resources = types.SimpleNamespace(requests={"storage": requested}) if has_resources else None
    spec = types.SimpleNamespace(
        resources=resources,
        storage_class_name=storage_class,
        access_modes=access_modes if access_modes is not None else ["ReadWriteOnce"],
        volume_name=volume_name,
    )
    status = types.SimpleNamespace(phase=phase, capacity={"storage": capacity} if capacity else None)
    return types.SimpleNamespace(metadata=metadata, spec=spec, status=status)


def test_map_pvc_extracts_capacity_and_binding_metadata():
    from app.collectors.pvc import map_pvc

    d = map_pvc(_pvc_obj())
    assert d["key"] == "home/home-assistant-config"
    assert d["namespace"] == "home"
    assert d["phase"] == "Bound"
    assert d["storage_class"] == "local-path"
    assert d["access_modes"] == ["ReadWriteOnce"]
    assert d["volume_name"] == "pvc-abc123"
    assert d["requested_bytes"] == 5 * 1024**3
    assert d["capacity_bytes"] == 5 * 1024**3
    assert d["usage_bytes"] is None
    assert d["usage_pct"] is None


def test_map_pvc_pending_with_no_bound_capacity_yet():
    from app.collectors.pvc import map_pvc

    d = map_pvc(_pvc_obj(phase="Pending", capacity=None))
    assert d["phase"] == "Pending"
    assert d["requested_bytes"] == 5 * 1024**3
    assert d["capacity_bytes"] is None


def test_map_pvc_no_resources_spec_defaults_requested_to_none():
    from app.collectors.pvc import map_pvc

    d = map_pvc(_pvc_obj(has_resources=False))
    assert d["requested_bytes"] is None


class _FakePromResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _prom_vector(entries):
    """entries: list of (namespace, pvc, value)."""
    return {
        "data": {
            "result": [
                {"metric": {"namespace": ns, "persistentvolumeclaim": pvc}, "value": [1700000000, str(v)]}
                for ns, pvc, v in entries
            ]
        }
    }


def test_query_prometheus_parses_vector_result_into_key_value_map(monkeypatch):
    import httpx

    from app.collectors.pvc import _query_prometheus

    async def fake_get(_self, _url, params=None, **_kw):
        return _FakePromResponse(_prom_vector([("home", "mosquitto-data", 12345.6)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def scenario():
        async with httpx.AsyncClient() as client:
            return await _query_prometheus(client, "http://prom:9090", "some_query")

    result = asyncio.run(scenario())
    assert result == {"home/mosquitto-data": pytest.approx(12345.6)}


def test_query_prometheus_skips_entries_missing_labels_or_unparsable_value(monkeypatch):
    import httpx

    from app.collectors.pvc import _query_prometheus

    data = {
        "data": {
            "result": [
                {"metric": {"namespace": "home"}, "value": [1700000000, "1"]},  # missing pvc label
                {"metric": {"namespace": "home", "persistentvolumeclaim": "x"}, "value": [1700000000, "not-a-number"]},
                {"metric": {"namespace": "home", "persistentvolumeclaim": "ok"}, "value": [1700000000, "42"]},
            ]
        }
    }

    async def fake_get(_self, _url, params=None, **_kw):
        return _FakePromResponse(data)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def scenario():
        async with httpx.AsyncClient() as client:
            return await _query_prometheus(client, "http://prom:9090", "some_query")

    result = asyncio.run(scenario())
    assert result == {"home/ok": pytest.approx(42.0)}


def test_merge_prometheus_usage_accepts_capacity_matching_the_pvcs_own_declared_size(monkeypatch):
    import httpx

    from app.collectors.pvc import _merge_prometheus_usage

    async def fake_get(_self, _url, params=None, **_kw):
        if "capacity" in params["query"]:
            return _FakePromResponse(_prom_vector([("home", "data", 10 * 1024**3)]))
        return _FakePromResponse(_prom_vector([("home", "data", 4 * 1024**3)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    items = {"home/data": {"capacity_bytes": 10 * 1024**3}}

    async def scenario():
        async with httpx.AsyncClient() as client:
            await _merge_prometheus_usage(client, "http://prom:9090", items)

    asyncio.run(scenario())
    assert items["home/data"]["usage_bytes"] == 4 * 1024**3
    assert items["home/data"]["usage_pct"] == pytest.approx(40.0)


def test_merge_prometheus_usage_discards_capacity_that_wildly_exceeds_the_pvcs_declared_size(monkeypatch):
    """local-path-provisioner (and similar no-quota provisioners) make kubelet report the whole
    node disk as "capacity" for every PVC on it -- must be discarded, not shown as real usage."""
    import httpx

    from app.collectors.pvc import _merge_prometheus_usage

    async def fake_get(_self, _url, params=None, **_kw):
        if "capacity" in params["query"]:
            return _FakePromResponse(_prom_vector([("home", "data", 229 * 1024**3)]))
        return _FakePromResponse(_prom_vector([("home", "data", 38 * 1024**3)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    items = {"home/data": {"capacity_bytes": 256 * 1024**2, "usage_bytes": None, "usage_pct": None}}  # 256Mi PVC

    async def scenario():
        async with httpx.AsyncClient() as client:
            await _merge_prometheus_usage(client, "http://prom:9090", items)

    asyncio.run(scenario())
    assert items["home/data"]["usage_bytes"] is None
    assert items["home/data"]["usage_pct"] is None


def test_merge_prometheus_usage_skips_when_pvc_has_no_declared_capacity_yet(monkeypatch):
    """A Pending PVC (not yet bound) has no capacity_bytes to sanity-check against."""
    import httpx

    from app.collectors.pvc import _merge_prometheus_usage

    async def fake_get(_self, _url, params=None, **_kw):
        if "capacity" in params["query"]:
            return _FakePromResponse(_prom_vector([("home", "data", 10 * 1024**3)]))
        return _FakePromResponse(_prom_vector([("home", "data", 4 * 1024**3)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    items = {"home/data": {"capacity_bytes": None, "usage_bytes": None, "usage_pct": None}}

    async def scenario():
        async with httpx.AsyncClient() as client:
            await _merge_prometheus_usage(client, "http://prom:9090", items)

    asyncio.run(scenario())
    assert items["home/data"]["usage_bytes"] is None
    assert items["home/data"]["usage_pct"] is None


def _fake_core_v1_api(items):
    class _FakeCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_persistent_volume_claim_for_all_namespaces(self):
            return _FakeList(items)

    return _FakeCoreV1Api


def test_pvc_run_publishes_metadata_without_prometheus_configured(monkeypatch):
    """No PIWATCH_PROMETHEUS_URL set -> metadata-only PVCs, no HTTP calls made."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import pvc
    from app.state import ClusterState

    monkeypatch.delenv("PIWATCH_PROMETHEUS_URL", raising=False)
    st = ClusterState()
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _fake_core_v1_api([_pvc_obj()]))

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(pvc.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pvc.run(st))

    assert "home/home-assistant-config" in st.pvcs
    assert st.pvcs["home/home-assistant-config"]["usage_pct"] is None
    assert sleep_calls == [pvc.POLL_INTERVAL]


def test_pvc_run_merges_prometheus_usage_when_configured(monkeypatch):
    import httpx
    from kubernetes_asyncio import client as kclient

    from app.collectors import pvc
    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_PROMETHEUS_URL", "http://prom:9090")
    st = ClusterState()
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _fake_core_v1_api([_pvc_obj()]))

    async def fake_get(_self, _url, params=None, **_kw):
        if "capacity" in params["query"]:
            return _FakePromResponse(_prom_vector([("home", "home-assistant-config", 5 * 1024**3)]))
        return _FakePromResponse(_prom_vector([("home", "home-assistant-config", 2 * 1024**3)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(pvc.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pvc.run(st))

    d = st.pvcs["home/home-assistant-config"]
    assert d["usage_bytes"] == 2 * 1024**3
    assert d["usage_pct"] == pytest.approx(40.0)
    assert sleep_calls == [pvc.POLL_INTERVAL]


def test_pvc_run_discards_usage_when_prometheus_capacity_is_really_the_node_disk(monkeypatch):
    """Storage classes without real per-volume quotas (e.g. local-path-provisioner) make kubelet
    fall back to statfs() on the underlying node disk -- caught live: a 256Mi PVC "using" 25-38GiB,
    because kubelet_volume_stats_capacity_bytes reported the whole node's disk size identically for
    every PVC on it, wildly exceeding what the PVC itself declared. Must be discarded, not shown."""
    import httpx
    from kubernetes_asyncio import client as kclient

    from app.collectors import pvc
    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_PROMETHEUS_URL", "http://prom:9090")
    st = ClusterState()
    small_pvc = _pvc_obj(name="mosquitto-data", requested="256Mi", capacity="256Mi")
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _fake_core_v1_api([small_pvc]))

    node_disk_bytes = 229 * 1024**3  # ~229GiB, the whole node's disk -- not this PVC's 256Mi

    async def fake_get(_self, _url, params=None, **_kw):
        if "capacity" in params["query"]:
            return _FakePromResponse(_prom_vector([("home", "mosquitto-data", node_disk_bytes)]))
        return _FakePromResponse(_prom_vector([("home", "mosquitto-data", 38 * 1024**3)]))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(pvc.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pvc.run(st))

    d = st.pvcs["home/mosquitto-data"]
    assert d["usage_bytes"] is None
    assert d["usage_pct"] is None
    assert sleep_calls == [pvc.POLL_INTERVAL]


def test_pvc_run_degrades_quietly_when_prometheus_unreachable(monkeypatch):
    """Prometheus is optional and independent of the PVC listing itself -- an
    outage there must not affect the PVC metadata poll cadence or crash it."""
    import httpx
    from kubernetes_asyncio import client as kclient

    from app.collectors import pvc
    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_PROMETHEUS_URL", "http://prom:9090")
    st = ClusterState()
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _fake_core_v1_api([_pvc_obj()]))

    async def fake_get(_self, _url, params=None, **_kw):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(pvc.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pvc.run(st))

    d = st.pvcs["home/home-assistant-config"]
    assert d["usage_pct"] is None
    assert sleep_calls == [pvc.POLL_INTERVAL]


def test_pvc_run_backs_off_on_k8s_listing_failure(monkeypatch):
    from kubernetes_asyncio import client as kclient

    from app.collectors import pvc
    from app.state import ClusterState

    monkeypatch.delenv("PIWATCH_PROMETHEUS_URL", raising=False)
    st = ClusterState()

    class _BrokenCoreV1Api:
        def __init__(self, api_client):
            pass

        async def list_persistent_volume_claim_for_all_namespaces(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CoreV1Api", _BrokenCoreV1Api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(pvc.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pvc.run(st))

    assert st.pvcs == {}
    assert sleep_calls == [pvc.RETRY_INTERVAL]


# ==================================================================
# app.collectors.gateway
# ==================================================================


def _gateway_obj(
    name="gw1", namespace="default", programmed=True,
    listener_names=("web", "web-tls"), attached_routes=(1, 1),
):
    listeners_spec = [{"name": n, "port": 443, "protocol": "HTTPS"} for n in listener_names]
    listeners_status = [
        {
            "name": n,
            "attachedRoutes": ar,
            "conditions": [
                {
                    "type": "Programmed",
                    "status": "True" if programmed else "False",
                    "reason": "Programmed" if programmed else "Invalid",
                }
            ],
        }
        for n, ar in zip(listener_names, attached_routes)
    ]
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"gatewayClassName": "nginx", "listeners": listeners_spec},
        "status": {
            "conditions": [
                {
                    "type": "Programmed",
                    "status": "True" if programmed else "False",
                    "reason": "Programmed" if programmed else "Invalid",
                    "message": "The Gateway is programmed" if programmed else "bad config",
                }
            ],
            "addresses": [{"type": "IPAddress", "value": "192.168.1.50"}],
            "listeners": listeners_status,
        },
    }


def _http_route_obj(
    name="route1", namespace="default", accepted=True, resolved_refs=True,
    parent_name="gw1", hostnames=("app.example.com",), backend_name="app-svc", with_parent_status=True,
):
    parents = []
    if with_parent_status:
        parents.append(
            {
                "parentRef": {"name": parent_name},
                "conditions": [
                    {
                        "type": "Accepted",
                        "status": "True" if accepted else "False",
                        "reason": "Accepted" if accepted else "NotAllowedByListeners",
                        "message": "The Route is accepted" if accepted else "denied by listener",
                    },
                    {
                        "type": "ResolvedRefs",
                        "status": "True" if resolved_refs else "False",
                        "reason": "ResolvedRefs" if resolved_refs else "BackendNotFound",
                        "message": "All references are resolved" if resolved_refs else "service not found",
                    },
                ],
            }
        )
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "hostnames": list(hostnames),
            "parentRefs": [{"name": parent_name, "kind": "Gateway"}],
            "rules": [{"backendRefs": [{"name": backend_name, "kind": "Service"}]}],
        },
        "status": {"parents": parents},
    }


def _ratelimitpolicy_obj(
    name="piwatch-rate-limit", namespace="monitoring", accepted=True,
    target_name="piwatch", rate="20r/s", burst=200, with_ancestor_status=True,
):
    ancestors = []
    if with_ancestor_status:
        ancestors.append(
            {
                "ancestorRef": {"kind": "HTTPRoute", "name": target_name, "namespace": namespace},
                "conditions": [
                    {
                        "type": "Accepted",
                        "status": "True" if accepted else "False",
                        "reason": "Accepted" if accepted else "Invalid",
                        "message": "The Policy is accepted" if accepted else "bad target",
                    }
                ],
            }
        )
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "targetRefs": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": target_name}],
            "rateLimit": {
                "local": {"rules": [{"rate": rate, "burst": burst, "zoneSize": "10m"}]},
                "rejectCode": 503,
            },
        },
        "status": {"ancestors": ancestors},
    }


def test_map_rate_limit_policy_accepted_and_extracts_targets_rules():
    from app.collectors.gateway import _map_rate_limit_policy

    d = _map_rate_limit_policy(_ratelimitpolicy_obj())
    assert d["key"] == "monitoring/piwatch-rate-limit"
    assert d["targets"] == ["HTTPRoute/piwatch"]
    assert d["rules"] == ["20r/s (burst 200)"]
    assert d["reject_code"] == 503
    assert d["accepted"] is True


def test_map_rate_limit_policy_not_accepted_surfaces_reason():
    from app.collectors.gateway import _map_rate_limit_policy

    d = _map_rate_limit_policy(_ratelimitpolicy_obj(accepted=False))
    assert d["accepted"] is False
    assert d["reason"] == "Invalid"


def test_map_rate_limit_policy_no_ancestor_status_defaults_to_not_accepted():
    from app.collectors.gateway import _map_rate_limit_policy

    d = _map_rate_limit_policy(_ratelimitpolicy_obj(with_ancestor_status=False))
    assert d["accepted"] is False


def test_map_gateway_extracts_status_addresses_and_listener_counts():
    from app.collectors.gateway import _map_gateway

    d = _map_gateway(_gateway_obj())
    assert d["key"] == "default/gw1"
    assert d["ready"] is True
    assert d["gateway_class_name"] == "nginx"
    assert d["addresses"] == ["192.168.1.50"]
    assert d["listener_count"] == 2
    assert d["listeners_ready"] == 2
    assert d["attached_routes"] == 2


def test_map_gateway_not_programmed_surfaces_reason_and_partial_listeners():
    from app.collectors.gateway import _map_gateway

    d = _map_gateway(_gateway_obj(programmed=False))
    assert d["ready"] is False
    assert d["reason"] == "Invalid"
    # listener-level Programmed conditions also flip with the parent object in this fixture
    assert d["listeners_ready"] == 0


def test_map_http_route_accepted_and_resolved():
    from app.collectors.gateway import _map_http_route

    d = _map_http_route(_http_route_obj())
    assert d["key"] == "default/route1"
    assert d["hostnames"] == ["app.example.com"]
    assert d["parent_names"] == ["gw1"]
    assert d["backend_names"] == ["app-svc"]
    assert d["accepted"] is True
    assert d["resolved_refs"] is True
    assert d["reason"] is None


def test_map_http_route_not_accepted_surfaces_reason():
    from app.collectors.gateway import _map_http_route

    d = _map_http_route(_http_route_obj(accepted=False))
    assert d["accepted"] is False
    assert d["reason"] == "NotAllowedByListeners"


def test_map_http_route_backend_not_resolved():
    """The failure mode a Deployment/Pod-only view can't catch: the route's
    backendRef points at a Service that doesn't exist or doesn't match."""
    from app.collectors.gateway import _map_http_route

    d = _map_http_route(_http_route_obj(resolved_refs=False))
    assert d["resolved_refs"] is False
    assert d["reason"] == "BackendNotFound"


def test_map_gateway_extracts_per_listener_hostname_port_protocol():
    from app.collectors.gateway import _map_gateway

    item = _gateway_obj()
    item["spec"]["listeners"] = [
        {"name": "web-heim", "hostname": "app.heim.lan", "port": 443, "protocol": "HTTPS"},
        {"name": "web-public", "hostname": "app.example.com", "port": 443, "protocol": "HTTPS"},
    ]
    d = _map_gateway(item)
    assert d["listeners"] == [
        {"name": "web-heim", "hostname": "app.heim.lan", "port": 443, "protocol": "HTTPS"},
        {"name": "web-public", "hostname": "app.example.com", "port": 443, "protocol": "HTTPS"},
    ]


def test_map_http_route_parent_namespace_defaults_to_own():
    """No namespace on the parentRef -- defaults to the route's own, per the
    Gateway API spec (same-namespace references usually omit it)."""
    from app.collectors.gateway import _map_http_route

    d = _map_http_route(_http_route_obj(namespace="home"))
    assert d["parent_names"] == ["gw1"]
    assert d["parent_namespaces"] == ["home"]


def test_map_http_route_parent_namespace_explicit_cross_namespace():
    from app.collectors.gateway import _map_http_route

    item = _http_route_obj(namespace="studylife-scale")
    item["spec"]["parentRefs"] = [{"name": "gw1", "namespace": "nginx-gateway", "kind": "Gateway"}]
    d = _map_http_route(item)
    assert d["parent_names"] == ["gw1"]
    assert d["parent_namespaces"] == ["nginx-gateway"]


def test_map_http_route_no_parent_status_defaults_to_not_accepted():
    """Freshly created, or no matching Gateway found at all -- can't claim
    accepted just because nothing said otherwise."""
    from app.collectors.gateway import _map_http_route

    d = _map_http_route(_http_route_obj(with_parent_status=False))
    assert d["accepted"] is False


def test_gateway_run_polls_and_publishes_all_three_kinds(monkeypatch):
    from kubernetes_asyncio import client as kclient

    from app.collectors import gateway
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi(
        {
            "gateways": [_gateway_obj()],
            "httproutes": [_http_route_obj()],
            "ratelimitpolicies": [_ratelimitpolicy_obj()],
        }
    )
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gateway.run(st))

    assert st.gateways["default/gw1"]["ready"] is True
    assert st.http_routes["default/route1"]["accepted"] is True
    assert st.rate_limit_policies["monitoring/piwatch-rate-limit"]["accepted"] is True
    assert sleep_calls == [gateway.POLL_INTERVAL]


def test_gateway_run_all_kinds_degrade_independently_and_keep_polling(monkeypatch):
    """Gateway API (and its NGINX-specific RateLimitPolicy extension) is optional -- a
    missing CRD (or missing RBAC) must not crash the app, just back off at the normal
    POLL_INTERVAL cadence."""
    from kubernetes_asyncio import client as kclient

    from app.collectors import gateway
    from app.state import ClusterState

    st = ClusterState()
    fake_api = _FakeCustomObjectsApi(error=RuntimeError("404 Not Found"))
    monkeypatch.setattr(kclient, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(kclient, "CustomObjectsApi", lambda api_client: fake_api)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gateway.run(st))

    assert st.gateways == {}
    assert st.http_routes == {}
    assert st.rate_limit_policies == {}
    assert sleep_calls == [gateway.POLL_INTERVAL]


def test_gateway_run_backs_off_on_structural_failure(monkeypatch):
    from kubernetes_asyncio import client as kclient

    from app.collectors import gateway
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

    monkeypatch.setattr(gateway.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gateway.run(st))

    assert sleep_calls == [gateway.RETRY_INTERVAL]


# ==================================================================
# app.collectors.autochecks
# ==================================================================


def _ac_gateway(namespace="nginx-gateway", name="gw1", listeners=None, addresses=("192.168.1.50",)):
    return {
        "key": f"{namespace}/{name}",
        "namespace": namespace,
        "name": name,
        "addresses": list(addresses),
        "listeners": listeners or [
            {"name": "web", "hostname": "app.heim.lan", "port": 443, "protocol": "HTTPS"}
        ],
    }


def _ac_route(
    namespace="home", name="route1", accepted=True, resolved_refs=True,
    hostnames=("app.heim.lan",), parent_name="gw1", parent_namespace="nginx-gateway",
):
    return {
        "key": f"{namespace}/{name}",
        "namespace": namespace,
        "name": name,
        "hostnames": list(hostnames),
        "parent_names": [parent_name],
        "parent_namespaces": [parent_namespace],
        "accepted": accepted,
        "resolved_refs": resolved_refs,
    }


def _ac_service(
    namespace="nginx-gateway", name="gw1-nginx", cluster_ip="10.43.9.1",
    external_ips=("192.168.1.50",), ports=None,
):
    # Default name deliberately differs from any _ac_gateway's default name (gw1) --
    # NGINX Gateway Fabric really does name it "<gateway>-nginx", verified live; matching
    # by name/namespace was tried first and doesn't work, see autochecks.py's docstring.
    return {
        "key": f"{namespace}/{name}",
        "namespace": namespace,
        "name": name,
        "cluster_ip": cluster_ip,
        "external_ips": list(external_ips),
        "ports": ports if ports is not None else [{"port": 443, "protocol": "TCP", "name": "https"}],
    }


def test_route_checks_builds_check_for_accepted_resolved_route_with_known_gateway():
    from app.collectors.autochecks import route_checks
    from app.state import ClusterState

    st = ClusterState()
    st.gateways = {"nginx-gateway/gw1": _ac_gateway()}
    st.http_routes = {"home/route1": _ac_route()}
    st.services = {"nginx-gateway/gw1-nginx": _ac_service()}

    checks = route_checks(st)
    assert checks["app.heim.lan"] == {
        "kind": "route",
        "cluster_ip": "10.43.9.1",
        "port": 443,
        "tls": True,
        "hostname": "app.heim.lan",
    }


def test_route_checks_skips_route_not_accepted_or_not_resolved():
    from app.collectors.autochecks import route_checks
    from app.state import ClusterState

    st = ClusterState()
    st.gateways = {"nginx-gateway/gw1": _ac_gateway()}
    st.services = {"nginx-gateway/gw1-nginx": _ac_service()}

    st.http_routes = {"home/route1": _ac_route(accepted=False)}
    assert route_checks(st) == {}

    st.http_routes = {"home/route1": _ac_route(resolved_refs=False)}
    assert route_checks(st) == {}


def test_route_checks_skips_when_gateway_unknown():
    from app.collectors.autochecks import route_checks
    from app.state import ClusterState

    st = ClusterState()
    st.http_routes = {"home/route1": _ac_route()}
    assert route_checks(st) == {}


def test_route_checks_skips_when_no_service_shares_the_gateway_address():
    """No Service's external_ips intersects the Gateway's own addresses (e.g. the
    Gateway isn't backed by a LoadBalancer-type Service yet, or none at all exists)
    -- can't reach it without an IP, so this route just doesn't get auto-checked."""
    from app.collectors.autochecks import route_checks
    from app.state import ClusterState

    st = ClusterState()
    st.gateways = {"nginx-gateway/gw1": _ac_gateway()}
    st.http_routes = {"home/route1": _ac_route()}
    assert route_checks(st) == {}

    # a Service exists but its external_ips don't overlap the Gateway's addresses
    st.services = {"nginx-gateway/gw1-nginx": _ac_service(external_ips=("192.168.1.99",))}
    assert route_checks(st) == {}


def test_route_checks_falls_back_to_hostname_less_listener():
    from app.collectors.autochecks import route_checks
    from app.state import ClusterState

    st = ClusterState()
    st.gateways = {
        "nginx-gateway/gw1": _ac_gateway(
            listeners=[{"name": "web", "hostname": None, "port": 8080, "protocol": "HTTP"}]
        )
    }
    st.http_routes = {"home/route1": _ac_route(hostnames=("anything.example.com",))}
    st.services = {"nginx-gateway/gw1-nginx": _ac_service()}

    checks = route_checks(st)
    assert checks["anything.example.com"]["port"] == 8080
    assert checks["anything.example.com"]["tls"] is False


def test_service_checks_builds_tcp_check_per_port():
    from app.collectors.autochecks import service_checks
    from app.state import ClusterState

    st = ClusterState()
    st.services = {
        "home/mosquitto-mqtt": _ac_service(
            namespace="home", name="mosquitto-mqtt", cluster_ip="10.43.9.2",
            ports=[{"port": 1883, "protocol": "TCP", "name": "mqtt"}],
        )
    }
    checks = service_checks(st)
    assert checks["home/mosquitto-mqtt:1883"] == {
        "kind": "service",
        "cluster_ip": "10.43.9.2",
        "port": 1883,
        "service_ref": "home/mosquitto-mqtt",
    }


def test_service_checks_skips_service_without_cluster_ip():
    from app.collectors.autochecks import service_checks
    from app.state import ClusterState

    st = ClusterState()
    st.services = {"home/svc": _ac_service(cluster_ip=None)}
    assert service_checks(st) == {}


class _FakeAcWriter:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _FakeAcReader:
    def __init__(self, line: bytes):
        self._line = line

    async def readline(self):
        return self._line


def test_probe_route_https_sends_correct_sni_and_host_header(monkeypatch):
    from app.collectors import autochecks

    captured = {}
    writer = _FakeAcWriter()

    async def fake_open_connection(host, port, **kwargs):
        captured["host"] = host
        captured["port"] = port
        captured["kwargs"] = kwargs
        return _FakeAcReader(b"HTTP/1.1 200 OK\r\n"), writer

    monkeypatch.setattr(autochecks.asyncio, "open_connection", fake_open_connection)

    async def scenario():
        return await autochecks._probe_route("10.43.9.1", 443, "app.heim.lan", tls=True)

    ok, ms, detail = asyncio.run(scenario())
    assert ok is True
    assert detail == "HTTP 200"
    assert ms is not None
    assert captured["host"] == "10.43.9.1"
    assert captured["port"] == 443
    assert captured["kwargs"]["server_hostname"] == "app.heim.lan"
    assert isinstance(captured["kwargs"]["ssl"], ssl.SSLContext)
    assert b"Host: app.heim.lan\r\n" in writer.written
    assert writer.closed is True


def test_probe_route_http_skips_tls_kwargs(monkeypatch):
    from app.collectors import autochecks

    captured = {}
    writer = _FakeAcWriter()

    async def fake_open_connection(host, port, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeAcReader(b"HTTP/1.1 404 Not Found\r\n"), writer

    monkeypatch.setattr(autochecks.asyncio, "open_connection", fake_open_connection)

    async def scenario():
        return await autochecks._probe_route("10.43.9.1", 8080, "app.heim.lan", tls=False)

    ok, _ms, detail = asyncio.run(scenario())
    # a 404 still proves the backend is up and speaking HTTP -- e.g. an API/MCP server with
    # no root-path route. Only a 5xx or a failed connection counts as actually down.
    assert ok is True
    assert detail == "HTTP 404"
    assert "ssl" not in captured["kwargs"]
    assert "server_hostname" not in captured["kwargs"]


def test_probe_route_5xx_reports_not_ok(monkeypatch):
    from app.collectors import autochecks

    writer = _FakeAcWriter()

    async def fake_open_connection(host, port, **kwargs):
        return _FakeAcReader(b"HTTP/1.1 502 Bad Gateway\r\n"), writer

    monkeypatch.setattr(autochecks.asyncio, "open_connection", fake_open_connection)

    async def scenario():
        return await autochecks._probe_route("10.43.9.1", 8080, "app.heim.lan", tls=False)

    ok, _ms, detail = asyncio.run(scenario())
    assert ok is False
    assert detail == "HTTP 502"


def test_probe_route_connection_error_reports_exception_type(monkeypatch):
    from app.collectors import autochecks

    async def fake_open_connection(host, port, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(autochecks.asyncio, "open_connection", fake_open_connection)

    async def scenario():
        return await autochecks._probe_route("10.43.9.1", 443, "app.heim.lan", tls=True)

    ok, ms, detail = asyncio.run(scenario())
    assert ok is False
    assert ms is None
    assert detail == "ConnectionRefusedError"


def test_probe_route_malformed_status_line_reports_no_response(monkeypatch):
    from app.collectors import autochecks

    writer = _FakeAcWriter()

    async def fake_open_connection(host, port, **kwargs):
        return _FakeAcReader(b""), writer

    monkeypatch.setattr(autochecks.asyncio, "open_connection", fake_open_connection)

    async def scenario():
        return await autochecks._probe_route("10.43.9.1", 443, "app.heim.lan", tls=False)

    ok, _ms, detail = asyncio.run(scenario())
    assert ok is False
    assert detail == "no response"


def test_probe_service_reports_tcp_open_against_a_real_local_server():
    """Note: deliberately doesn't await server.wait_closed() -- with a
    connection handler that never explicitly closes its writer, wait_closed()
    blocks on that still-open connection instead of just the listening socket.
    close() alone already stops accepting immediately, which is all this
    test needs."""
    from app.collectors import autochecks

    async def handle(_reader, writer):
        writer.close()

    async def scenario():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await autochecks._probe_service("127.0.0.1", port)
        finally:
            server.close()

    ok, ms, detail = asyncio.run(scenario())
    assert ok is True
    assert detail == "TCP open"
    assert ms is not None


def test_probe_service_connection_refused_reports_not_ok():
    from app.collectors import autochecks

    async def scenario():
        # Bind then immediately close, to get a genuinely refused local port.
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        return await autochecks._probe_service("127.0.0.1", port)

    ok, ms, _detail = asyncio.run(scenario())
    assert ok is False
    assert ms is None


def test_autochecks_run_disabled_without_env_var_returns_immediately(monkeypatch):
    from app.collectors import autochecks
    from app.state import ClusterState

    monkeypatch.delenv("PIWATCH_AUTO_HEALTHCHECKS", raising=False)
    st = ClusterState()
    asyncio.run(autochecks.run(st))  # must return promptly, not hang
    assert st.healthchecks == {}


def test_autochecks_run_starts_and_removes_checks_as_state_changes(monkeypatch):
    """Integration-style, real (short) timers: enabling the feature and seeding
    one Service target lets its check run at least once; removing the target
    then makes it disappear from state.healthchecks -- the dynamic (not
    load-once) lifecycle this collector exists for."""
    from app.collectors import autochecks
    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_AUTO_HEALTHCHECKS", "1")
    monkeypatch.setattr(autochecks, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(autochecks, "CHECK_INTERVAL", 0.01)

    async def fake_probe_service(cluster_ip, port):
        return True, 1.0, "TCP open"

    monkeypatch.setattr(autochecks, "_probe_service", fake_probe_service)

    st = ClusterState()
    st.services = {
        "home/svc": _ac_service(namespace="home", name="svc", cluster_ip="10.0.0.1",
                                 ports=[{"port": 80, "protocol": "TCP", "name": "http"}])
    }

    async def scenario():
        task = asyncio.create_task(autochecks.run(st))
        await asyncio.sleep(0.1)
        assert "home/svc:80" in st.healthchecks

        st.services = {}
        await asyncio.sleep(0.1)
        assert "home/svc:80" not in st.healthchecks

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


# ==================================================================
# app.collectors.history
# ==================================================================


def test_history_disabled_without_env_var(monkeypatch):
    from app.collectors import history

    monkeypatch.delenv("PIWATCH_HISTORY_DB", raising=False)
    assert history._enabled() is False


def test_history_enabled_with_env_var(monkeypatch, tmp_path):
    from app.collectors import history

    monkeypatch.setenv("PIWATCH_HISTORY_DB", str(tmp_path / "history.db"))
    assert history._enabled() is True


def test_history_connect_creates_schema_and_is_reopenable(tmp_path):
    from app.collectors import history

    path = str(tmp_path / "sub" / "history.db")  # dir doesn't exist yet -> must be created
    conn = history._connect(path)
    history._insert(conn, "pi-1", {"t": 1000.0, "cpu_pct": 12.5, "mem_pct": 40.0})
    conn.close()

    reopened = history._connect(path)
    row = reopened.execute("SELECT node, t, cpu_pct FROM node_samples").fetchone()
    reopened.close()
    assert row == ("pi-1", 1000.0, 12.5)


def test_history_prune_removes_only_rows_older_than_retention(tmp_path):
    from app.collectors import history

    conn = history._connect(str(tmp_path / "history.db"))
    now = time.time()
    history._insert(conn, "pi-1", {"t": now - 1000})  # recent -- kept
    history._insert(conn, "pi-1", {"t": now - 100_000})  # old -- pruned

    removed = history.prune(conn, retention_seconds=10_000)
    assert removed == 1
    remaining = conn.execute("SELECT COUNT(*) FROM node_samples").fetchone()[0]
    assert remaining == 1
    conn.close()


def test_load_startup_history_noop_when_disabled(monkeypatch):
    from app.collectors import history
    from app.state import ClusterState

    monkeypatch.delenv("PIWATCH_HISTORY_DB", raising=False)
    st = ClusterState()
    history.load_startup_history(st)
    assert st.node_history == {}


def test_load_startup_history_reloads_bounded_recent_samples_per_node(monkeypatch, tmp_path):
    from app.collectors import history
    from app.state import ClusterState

    path = str(tmp_path / "history.db")
    monkeypatch.setenv("PIWATCH_HISTORY_DB", path)
    monkeypatch.setattr(history, "HISTORY_LEN", 3)  # small cap, easy to exceed in a test

    conn = history._connect(path)
    now = time.time()
    for i in range(5):  # more than the (monkeypatched) cap
        history._insert(conn, "pi-1", {"t": now + i, "cpu_pct": float(i)})
    conn.close()

    st = ClusterState()
    history.load_startup_history(st)
    hist = list(st.node_history["pi-1"])
    assert len(hist) == 3  # capped, not all 5
    # the 3 most recent, in chronological (oldest-first) order
    assert [h["cpu_pct"] for h in hist] == [2.0, 3.0, 4.0]


def test_load_startup_history_missing_file_starts_empty(monkeypatch, tmp_path):
    """A DB that doesn't exist yet (first-ever startup with the feature just enabled) is
    not a failure -- just nothing to reload."""
    from app.collectors import history
    from app.state import ClusterState

    monkeypatch.setenv("PIWATCH_HISTORY_DB", str(tmp_path / "does-not-exist" / "history.db"))
    st = ClusterState()
    history.load_startup_history(st)
    assert st.node_history == {}


def test_history_run_disabled_without_env_var_returns_immediately(monkeypatch):
    from app.collectors import history
    from app.state import ClusterState

    monkeypatch.delenv("PIWATCH_HISTORY_DB", raising=False)
    st = ClusterState()
    asyncio.run(history.run(st))  # must return promptly, not hang


def test_history_run_persists_node_metrics_and_prunes_periodically(monkeypatch, tmp_path):
    from app.collectors import history
    from app.state import ClusterState

    path = str(tmp_path / "history.db")
    monkeypatch.setenv("PIWATCH_HISTORY_DB", path)
    monkeypatch.setenv("PIWATCH_HISTORY_RETENTION_DAYS", "7")
    monkeypatch.setattr(history, "PRUNE_INTERVAL", 0)  # prune on every loop iteration

    st = ClusterState()

    async def scenario():
        task = asyncio.create_task(history.run(st))
        await asyncio.sleep(0.05)  # let run() subscribe before the first sample fires
        st.record_node_sample("pi-1", {"cpu_pct": 55.0})
        await asyncio.sleep(0.2)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    conn = history._connect(path)
    row = conn.execute("SELECT node, cpu_pct FROM node_samples").fetchone()
    conn.close()
    assert row == ("pi-1", 55.0)


# ==================================================================
# app.collectors.dns_check
# ==================================================================


def test_dns_check_resolve_success_reports_ok_with_latency(monkeypatch):
    from app.collectors import dns_check

    async def fake_getaddrinfo(host, port):
        assert host == dns_check.HOSTNAME
        return [(2, 1, 6, "", ("10.43.0.1", 0))]

    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        return await dns_check._resolve(dns_check.HOSTNAME)

    ok, ms, detail = asyncio.run(scenario())
    assert ok is True
    assert ms is not None
    assert detail == "resolved"


def test_dns_check_resolve_failure_reports_exception_type(monkeypatch):
    from app.collectors import dns_check

    async def fake_getaddrinfo(host, port):
        raise OSError("Name or service not known")

    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        return await dns_check._resolve(dns_check.HOSTNAME)

    ok, ms, detail = asyncio.run(scenario())
    assert ok is False
    assert ms is None
    assert detail == "OSError"


def test_dns_check_run_records_a_check_with_dns_url_config(monkeypatch):
    from app.collectors import dns_check
    from app.state import ClusterState

    async def fake_resolve(hostname):
        return True, 3.2, "resolved"

    async def fake_sleep(seconds):
        raise asyncio.CancelledError()

    monkeypatch.setattr(dns_check, "_resolve", fake_resolve)
    monkeypatch.setattr(dns_check.asyncio, "sleep", fake_sleep)

    st = ClusterState()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dns_check.run(st))

    entry = st.healthchecks[dns_check.CHECK_NAME]
    assert entry["config"] == {
        "name": "coredns", "type": "dns", "url": f"dns://{dns_check.HOSTNAME}",
    }
    assert entry["last"]["ok"] is True
    assert entry["last"]["ms"] == 3.2


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
    monkeypatch.setattr(main_mod.pvc, "run", fake_collector)
    monkeypatch.setattr(main_mod.gateway, "run", fake_collector)
    monkeypatch.setattr(main_mod.autochecks, "run", fake_collector)
    monkeypatch.setattr(main_mod.history, "run", fake_collector)
    monkeypatch.setattr(main_mod.dns_check, "run", fake_collector)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        # not demo mode and no nodes seeded yet -> not ready
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json() == {"ready": False}
    assert len(started) == 9  # k8s_watch, metrics, hardware, flux, pvc, gateway, autochecks, history, dns_check


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
