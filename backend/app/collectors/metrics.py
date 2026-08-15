"""Polls the metrics-server (metrics.k8s.io, built into k3s) every 10s:
converts node CPU/memory usage into percentages of node capacity, and
records raw per-pod CPU/RAM usage (summed across each pod's containers).
"""
from __future__ import annotations

import asyncio
import logging

from ..state import ClusterState

log = logging.getLogger("piwatch.metrics")

POLL_INTERVAL = 10


def parse_cpu(v: str) -> float:
    """Kubernetes CPU quantity -> cores (e.g. '250m' -> 0.25, '1' -> 1.0)."""
    v = v.strip()
    if v.endswith("n"):
        return int(v[:-1]) / 1e9
    if v.endswith("u"):
        return int(v[:-1]) / 1e6
    if v.endswith("m"):
        return int(v[:-1]) / 1e3
    return float(v)


_MEM_FACTORS = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12,
}


def parse_mem(v: str) -> float:
    """Kubernetes memory quantity -> bytes (e.g. '512Mi', '8Gi', '1500K')."""
    v = v.strip()
    for suffix, factor in _MEM_FACTORS.items():
        if v.endswith(suffix):
            return float(v[: -len(suffix)]) * factor
    return float(v)


async def run(state: ClusterState):
    from kubernetes_asyncio import client

    while True:
        try:
            async with client.ApiClient() as api_client:
                custom = client.CustomObjectsApi(api_client)
                while True:
                    metrics = await custom.list_cluster_custom_object(
                        "metrics.k8s.io", "v1beta1", "nodes"
                    )
                    for item in metrics.get("items", []):
                        name = item["metadata"]["name"]
                        node = state.nodes.get(name, {})
                        try:
                            cpu_cores = parse_cpu(item["usage"]["cpu"])
                            mem_bytes = parse_mem(item["usage"]["memory"])
                            cpu_cap = float(node.get("cpu_capacity") or 4)
                            mem_cap = parse_mem(node.get("mem_capacity") or "8Gi")
                            state.record_node_sample(
                                name,
                                {
                                    "cpu_pct": round(100 * cpu_cores / cpu_cap, 1),
                                    "mem_pct": round(100 * mem_bytes / mem_cap, 1),
                                    "cpu_cores": round(cpu_cores, 2),
                                    "mem_bytes": int(mem_bytes),
                                },
                            )
                        except (KeyError, ValueError) as exc:
                            log.debug("Metrics for %s unreadable: %s", name, exc)

                    pod_metrics = await custom.list_cluster_custom_object(
                        "metrics.k8s.io", "v1beta1", "pods"
                    )
                    for item in pod_metrics.get("items", []):
                        namespace = item["metadata"]["namespace"]
                        pod_name = item["metadata"]["name"]
                        key = f"{namespace}/{pod_name}"
                        try:
                            containers = item["containers"]
                            cpu_cores = sum(parse_cpu(c["usage"]["cpu"]) for c in containers)
                            mem_bytes = sum(parse_mem(c["usage"]["memory"]) for c in containers)
                            state.record_pod_sample(
                                key,
                                {
                                    "cpu_cores": round(cpu_cores, 3),
                                    "mem_bytes": int(mem_bytes),
                                },
                            )
                        except (KeyError, ValueError) as exc:
                            log.debug("Pod metrics for %s unreadable: %s", key, exc)

                    await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("metrics-server unreachable (%s) -- retry in 30s", exc)
            await asyncio.sleep(30)
