"""Watches Kubernetes resources (nodes, pods, deployments, events) via the
Watch API and feeds slim, JSON-friendly objects into the ClusterState.

Runs in-cluster (ServiceAccount) or against a local kubeconfig.
Each watch runs in its own task and reconnects with backoff -- resource
version expiry (410 Gone) simply triggers a fresh list+watch.
"""
from __future__ import annotations

import asyncio
import logging
import os

from ..state import ClusterState

log = logging.getLogger("piwatch.k8s")


async def load_config():
    from kubernetes_asyncio import config

    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
        log.info("Kubernetes: in-cluster config loaded")
    else:
        await config.load_kube_config()
        log.info("Kubernetes: kubeconfig loaded")


# ---------------- mappers: k8s object -> slim dict ----------------

def map_node(n) -> dict:
    conditions = {c.type: c.status for c in (n.status.conditions or [])}
    labels = n.metadata.labels or {}
    roles = [
        k.split("/", 1)[1]
        for k in labels
        if k.startswith("node-role.kubernetes.io/")
    ] or ["worker"]
    addrs = {a.type: a.address for a in (n.status.addresses or [])}
    return {
        "name": n.metadata.name,
        "ready": conditions.get("Ready") == "True",
        "conditions": conditions,
        "roles": roles,
        "arch": (n.status.node_info.architecture if n.status.node_info else None),
        "kubelet": (n.status.node_info.kubelet_version if n.status.node_info else None),
        "os_image": (n.status.node_info.os_image if n.status.node_info else None),
        "internal_ip": addrs.get("InternalIP"),
        "cpu_capacity": (n.status.capacity or {}).get("cpu"),
        "mem_capacity": (n.status.capacity or {}).get("memory"),
        "unschedulable": bool(n.spec.unschedulable),
        "created": n.metadata.creation_timestamp.timestamp() if n.metadata.creation_timestamp else None,
    }


def _terminated_reason(state) -> str | None:
    return state.terminated.reason if state and state.terminated else None


def _last_termination(state, last_state) -> tuple[str | None, int | None]:
    """(reason, exit_code) for whichever is more current: a container that's
    terminated RIGHT NOW (e.g. a finished one-shot container) takes priority
    over last_state (kubelet already restarted it back to Running, but
    last_state still holds the forensic info about that crash)."""
    term = (state.terminated if state and state.terminated else None) or (
        last_state.terminated if last_state and last_state.terminated else None
    )
    return (term.reason, term.exit_code) if term else (None, None)


def map_pod(p) -> dict:
    statuses = p.status.container_statuses or []
    restarts = sum(s.restart_count for s in statuses)
    ready = sum(1 for s in statuses if s.ready)
    waiting_reason = None
    # OOMKilled containers usually aren't still OOMKilled *right now* -- kubelet restarts
    # them and they go back to Running. last_state is what still shows the OOM after that,
    # so both current and last state need checking to not miss a since-recovered kill.
    oom_killed = False
    last_exit_reason = None
    last_exit_code = None
    for s in statuses:
        if s.state and s.state.waiting:
            waiting_reason = s.state.waiting.reason
        if "OOMKilled" in (_terminated_reason(s.state), _terminated_reason(s.last_state)):
            oom_killed = True
        if last_exit_reason is None:
            last_exit_reason, last_exit_code = _last_termination(s.state, s.last_state)
    return {
        "key": f"{p.metadata.namespace}/{p.metadata.name}",
        "name": p.metadata.name,
        "namespace": p.metadata.namespace,
        "node": p.spec.node_name,
        "phase": p.status.phase,
        "reason": waiting_reason or p.status.reason,
        "ready": f"{ready}/{len(statuses)}" if statuses else "0/0",
        "restarts": restarts,
        "containers": [c.name for c in (p.spec.containers or [])],
        "images": [c.image for c in (p.spec.containers or [])],
        "oom_killed": oom_killed,
        "last_exit_reason": last_exit_reason,
        "last_exit_code": last_exit_code,
        "created": p.metadata.creation_timestamp.timestamp() if p.metadata.creation_timestamp else None,
    }


def map_deployment(d) -> dict:
    return {
        "key": f"{d.metadata.namespace}/{d.metadata.name}",
        "name": d.metadata.name,
        "namespace": d.metadata.namespace,
        "replicas": d.spec.replicas or 0,
        "ready": d.status.ready_replicas or 0,
        "available": d.status.available_replicas or 0,
        "updated": d.status.updated_replicas or 0,
        "images": [c.image for c in d.spec.template.spec.containers],
    }


def map_statefulset(s) -> dict:
    return {
        "key": f"{s.metadata.namespace}/{s.metadata.name}",
        "name": s.metadata.name,
        "namespace": s.metadata.namespace,
        "replicas": s.spec.replicas or 0,
        "ready": s.status.ready_replicas or 0,
        "updated": s.status.updated_replicas or 0,
        "images": [c.image for c in s.spec.template.spec.containers],
    }


def map_daemonset(d) -> dict:
    return {
        "key": f"{d.metadata.namespace}/{d.metadata.name}",
        "name": d.metadata.name,
        "namespace": d.metadata.namespace,
        "desired": d.status.desired_number_scheduled or 0,
        "ready": d.status.number_ready or 0,
        "updated": d.status.updated_number_scheduled or 0,
        "images": [c.image for c in d.spec.template.spec.containers],
    }


def map_service(s) -> dict:
    lb = s.status.load_balancer if s.status else None
    ingress = (lb.ingress if lb else None) or []
    external_ips = [addr for addr in (i.ip or i.hostname for i in ingress) if addr]
    return {
        "key": f"{s.metadata.namespace}/{s.metadata.name}",
        "name": s.metadata.name,
        "namespace": s.metadata.namespace,
        "type": s.spec.type,
        "cluster_ip": s.spec.cluster_ip,
        "external_ips": external_ips,
        "ports": [
            {"port": p.port, "protocol": p.protocol, "name": p.name}
            for p in (s.spec.ports or [])
        ],
    }


def map_hpa(h) -> dict:
    target = h.spec.scale_target_ref
    conditions = {c.type: c.status for c in (h.status.conditions or [])}
    # Only Resource-type metrics (CPU/memory utilization) are summarized -- by far the
    # common case, and the only one with a simple scalar percentage to show. Pods/Object/
    # External metrics exist on the real API but don't have a universal "target %" shape
    # to render generically; an HPA using only those just shows an empty metrics list here.
    metrics = [
        {"name": m.resource.name, "target_pct": m.resource.target.average_utilization}
        for m in (h.spec.metrics or [])
        if m.type == "Resource" and m.resource and m.resource.target
    ]
    current_metrics = [
        {"name": m.resource.name, "current_pct": m.resource.current.average_utilization}
        for m in (h.status.current_metrics or [])
        if m.type == "Resource" and m.resource and m.resource.current
    ]
    return {
        "key": f"{h.metadata.namespace}/{h.metadata.name}",
        "name": h.metadata.name,
        "namespace": h.metadata.namespace,
        "target_kind": target.kind if target else None,
        "target_name": target.name if target else None,
        "min_replicas": h.spec.min_replicas,
        "max_replicas": h.spec.max_replicas,
        "current_replicas": h.status.current_replicas or 0,
        "desired_replicas": h.status.desired_replicas or 0,
        "metrics": metrics,
        "current_metrics": current_metrics,
        "able_to_scale": conditions.get("AbleToScale"),
        "scaling_active": conditions.get("ScalingActive"),
        "scaling_limited": conditions.get("ScalingLimited"),
    }


def map_pv(v) -> dict:
    claim_ref = v.spec.claim_ref
    return {
        "key": v.metadata.name,
        "name": v.metadata.name,
        "phase": v.status.phase if v.status else None,
        "capacity": (v.spec.capacity or {}).get("storage"),
        "storage_class": v.spec.storage_class_name,
        "reclaim_policy": v.spec.persistent_volume_reclaim_policy,
        "claim_namespace": claim_ref.namespace if claim_ref else None,
        "claim_name": claim_ref.name if claim_ref else None,
    }


def map_event(e) -> dict:
    ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
    return {
        "uid": e.metadata.uid,
        "type": e.type,
        "reason": e.reason,
        "message": e.message,
        "object": f"{e.involved_object.kind}/{e.involved_object.name}",
        "namespace": e.involved_object.namespace,
        "count": e.count or 1,
        "t": ts.timestamp() if ts else None,
    }


# ---------------- watch loops ----------------

async def _watch_loop(state: ClusterState, kind: str):
    """Generic list+watch loop with reconnect/backoff for one resource kind."""
    from kubernetes_asyncio import client, watch

    backoff = 1
    while True:
        try:
            async with client.ApiClient() as api_client:
                v1 = client.CoreV1Api(api_client)
                apps = client.AppsV1Api(api_client)
                autoscaling = client.AutoscalingV2Api(api_client)

                if kind == "nodes":
                    lister, mapper = v1.list_node, map_node
                elif kind == "pods":
                    lister, mapper = v1.list_pod_for_all_namespaces, map_pod
                elif kind == "deployments":
                    lister, mapper = apps.list_deployment_for_all_namespaces, map_deployment
                elif kind == "statefulsets":
                    lister, mapper = apps.list_stateful_set_for_all_namespaces, map_statefulset
                elif kind == "daemonsets":
                    lister, mapper = apps.list_daemon_set_for_all_namespaces, map_daemonset
                elif kind == "services":
                    lister, mapper = v1.list_service_for_all_namespaces, map_service
                elif kind == "hpas":
                    lister, mapper = autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces, map_hpa
                elif kind == "persistentvolumes":
                    lister, mapper = v1.list_persistent_volume, map_pv
                elif kind == "events":
                    lister, mapper = v1.list_event_for_all_namespaces, map_event
                else:
                    raise ValueError(kind)

                # Initial list -> seed state
                initial = await lister()
                rv = initial.metadata.resource_version
                for item in initial.items:
                    _apply(state, kind, "ADDED", mapper(item))
                backoff = 1

                # Watch for deltas
                w = watch.Watch()
                async with w.stream(lister, resource_version=rv, timeout_seconds=300) as stream:
                    async for event in stream:
                        _apply(state, kind, event["type"], mapper(event["object"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 410 Gone, network blips, apiserver restart
            log.warning("Watch %s aborted (%s) -- reconnecting in %ss", kind, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _apply(state: ClusterState, kind: str, ev_type: str, obj: dict) -> None:
    deleted = ev_type == "DELETED"
    if kind == "nodes":
        state.remove_node(obj["name"]) if deleted else state.upsert_node(obj["name"], obj)
    elif kind == "pods":
        state.remove_pod(obj["key"]) if deleted else state.upsert_pod(obj["key"], obj)
    elif kind == "deployments":
        state.remove_deployment(obj["key"]) if deleted else state.upsert_deployment(obj["key"], obj)
    elif kind == "statefulsets":
        state.remove_statefulset(obj["key"]) if deleted else state.upsert_statefulset(obj["key"], obj)
    elif kind == "daemonsets":
        state.remove_daemonset(obj["key"]) if deleted else state.upsert_daemonset(obj["key"], obj)
    elif kind == "services":
        # Only LoadBalancer-type services are worth surfacing (ClusterIP/NodePort don't
        # have an external-address-pending state to watch for) -- a Service that stops
        # being LoadBalancer (rare, but possible on edit) must be actively removed, not
        # just left stale, since it'd otherwise never get another upsert to replace it.
        if deleted or obj["type"] != "LoadBalancer":
            state.remove_service(obj["key"])
        else:
            state.upsert_service(obj["key"], obj)
    elif kind == "hpas":
        state.remove_hpa(obj["key"]) if deleted else state.upsert_hpa(obj["key"], obj)
    elif kind == "persistentvolumes":
        # Only "Released"/"Failed" PVs are orphaned/need-attention -- a PV that gets
        # rebound (rare, but possible) must be actively removed, not left stale, since
        # it'd otherwise never get another upsert to replace it.
        if deleted or obj["phase"] not in ("Released", "Failed"):
            state.remove_orphaned_pv(obj["key"])
        else:
            state.upsert_orphaned_pv(obj["key"], obj)
    elif kind == "events" and not deleted:
        state.add_event(obj)


async def run(state: ClusterState):
    """Entry point: start all watch loops (called from main.py lifespan)."""
    await load_config()
    await asyncio.gather(
        _watch_loop(state, "nodes"),
        _watch_loop(state, "pods"),
        _watch_loop(state, "deployments"),
        _watch_loop(state, "statefulsets"),
        _watch_loop(state, "daemonsets"),
        _watch_loop(state, "services"),
        _watch_loop(state, "hpas"),
        _watch_loop(state, "persistentvolumes"),
        _watch_loop(state, "events"),
    )
