"""Polls PersistentVolumeClaims for capacity/binding metadata, and -- if
PIWATCH_PROMETHEUS_URL is set -- merges in actual usage from kubelet's
per-volume stats (kubelet_volume_stats_used_bytes/capacity_bytes), the same
metrics Grafana's own PVC dashboards use.

PVC metadata always comes straight from the Kubernetes API (a core, always
watchable resource) -- Prometheus is a separate, optional data source layered
on top for the usage percentage, mirroring the pattern in collectors/flux.py:
no default guess at a Prometheus URL, and a Prometheus outage only blanks the
usage numbers, it never hides the PVC list itself.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from ..state import ClusterState
from .metrics import parse_mem

log = logging.getLogger("piwatch.pvc")

POLL_INTERVAL = 30
RETRY_INTERVAL = 30


def map_pvc(p) -> dict:
    meta = p.metadata
    spec = p.spec
    status = p.status
    namespace = meta.namespace
    name = meta.name
    requested = (spec.resources.requests or {}).get("storage") if spec.resources else None
    capacity = (status.capacity or {}).get("storage") if status.capacity else None
    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "phase": status.phase,
        "storage_class": spec.storage_class_name,
        "access_modes": spec.access_modes or [],
        "volume_name": spec.volume_name,
        "requested_bytes": parse_mem(requested) if requested else None,
        "capacity_bytes": parse_mem(capacity) if capacity else None,
        "usage_bytes": None,
        "usage_pct": None,
    }


async def _query_prometheus(http_client: httpx.AsyncClient, prom_url: str, query: str) -> dict[str, float]:
    """Instant PromQL query -> {"namespace/pvc": value}."""
    resp = await http_client.get(f"{prom_url.rstrip('/')}/api/v1/query", params={"query": query})
    resp.raise_for_status()
    data = resp.json()
    result: dict[str, float] = {}
    for r in data.get("data", {}).get("result", []):
        metric = r.get("metric", {})
        ns = metric.get("namespace")
        pvc = metric.get("persistentvolumeclaim")
        value = r.get("value")
        if not (ns and pvc and value and len(value) == 2):
            continue
        try:
            result[f"{ns}/{pvc}"] = float(value[1])
        except (TypeError, ValueError):
            continue
    return result


async def _merge_prometheus_usage(http_client: httpx.AsyncClient, prom_url: str, items: dict[str, dict]) -> None:
    used = await _query_prometheus(http_client, prom_url, "kubelet_volume_stats_used_bytes")
    capacity = await _query_prometheus(http_client, prom_url, "kubelet_volume_stats_capacity_bytes")
    for key, item in items.items():
        u = used.get(key)
        c = capacity.get(key)
        declared = item.get("capacity_bytes")
        if u is None or not c or not declared:
            continue
        # Provisioners without real per-volume quotas (e.g. local-path-provisioner, a common k3s
        # default) don't give kubelet a real filesystem boundary to measure -- it falls back to
        # statfs() on the underlying node disk, so kubelet_volume_stats_capacity_bytes ends up
        # reporting the WHOLE NODE's disk size, identically, for every PVC on that node, regardless
        # of what was actually requested. Caught live: a 256Mi PVC "using" 25-38GiB. Cross-check
        # against the PVC's own declared capacity (always trustworthy, straight from the K8s API)
        # and skip rather than show a number that's really "how full is the node", not "how full is
        # this PVC". Some slack above 1x for legitimate filesystem overhead/rounding.
        if c > declared * 1.5:
            continue
        item["usage_bytes"] = u
        item["usage_pct"] = round(100 * u / declared, 1)


async def run(state: ClusterState):
    from kubernetes_asyncio import client

    prom_url = os.environ.get("PIWATCH_PROMETHEUS_URL")
    prom_warned = False
    async with httpx.AsyncClient(timeout=10) as http_client:
        while True:
            try:
                async with client.ApiClient() as api_client:
                    v1 = client.CoreV1Api(api_client)
                    while True:
                        result = await v1.list_persistent_volume_claim_for_all_namespaces()
                        items = {}
                        for p in result.items:
                            mapped = map_pvc(p)
                            items[mapped["key"]] = mapped
                        if prom_url:
                            try:
                                await _merge_prometheus_usage(http_client, prom_url, items)
                                prom_warned = False
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                if not prom_warned:
                                    log.info(
                                        "Prometheus PVC usage unavailable (%s) -- showing capacity "
                                        "metadata only. Retrying quietly.",
                                        exc,
                                    )
                                    prom_warned = True
                        state.set_pvcs(items)
                        await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("PVC listing failed (%s) -- retry in %ss", exc, RETRY_INTERVAL)
                await asyncio.sleep(RETRY_INTERVAL)
