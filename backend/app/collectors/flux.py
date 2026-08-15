"""Polls Flux CD's Kustomization custom resources (kustomize.toolkit.fluxcd.io)
for GitOps sync status.

Optional: Flux is not a hard dependency of PiWatch. If the CRD isn't
installed (or RBAC for it is missing), list_cluster_custom_object() fails
the same way every poll -- logged once, then retried quietly on the same
backoff as the other collectors, instead of warning forever for what is a
permanent, expected state for most clusters.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from ..state import ClusterState

log = logging.getLogger("piwatch.flux")

POLL_INTERVAL = 15
GROUP = "kustomize.toolkit.fluxcd.io"
VERSION = "v1"
PLURAL = "kustomizations"

_GO_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")
_GO_DURATION_UNIT_SECONDS = {
    "ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600,
}


def _parse_go_duration(s: str | None) -> float | None:
    """spec.interval is a Go time.Duration string (e.g. "5m", "1h30m") -- not
    ISO 8601. Sums each (number, unit) pair found; None if nothing matched."""
    if not s:
        return None
    matches = _GO_DURATION_RE.findall(s)
    if not matches:
        return None
    return sum(float(value) * _GO_DURATION_UNIT_SECONDS[unit] for value, unit in matches)


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _map_kustomization(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    conditions = status.get("conditions", [])
    ready_cond = next((c for c in conditions if c.get("type") == "Ready"), {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")

    # history[0].lastReconciled updates on every successful reconcile attempt (even when
    # nothing changed); the Ready condition's lastTransitionTime only updates when the
    # Ready status itself flips, which under-counts reconciles that stayed healthy.
    history = status.get("history") or []
    last_reconciled = history[0].get("lastReconciled") if history else ready_cond.get("lastTransitionTime")
    last_reconciled_t = _parse_iso(last_reconciled)
    interval_s = _parse_go_duration(spec.get("interval"))
    next_reconcile_t = (
        last_reconciled_t + interval_s if last_reconciled_t is not None and interval_s is not None else None
    )

    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "reason": ready_cond.get("reason"),
        "message": ready_cond.get("message"),
        "last_applied_revision": status.get("lastAppliedRevision"),
        "last_transition_time": ready_cond.get("lastTransitionTime"),
        "next_reconcile_t": next_reconcile_t,
    }


async def run(state: ClusterState):
    from kubernetes_asyncio import client

    warned = False
    while True:
        try:
            async with client.ApiClient() as api_client:
                custom = client.CustomObjectsApi(api_client)
                while True:
                    result = await custom.list_cluster_custom_object(GROUP, VERSION, PLURAL)
                    items = {}
                    for item in result.get("items", []):
                        mapped = _map_kustomization(item)
                        items[mapped["key"]] = mapped
                    state.set_flux_kustomizations(items)
                    warned = False
                    await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not warned:
                log.info(
                    "Flux Kustomizations unavailable (%s) -- not installed, or RBAC "
                    "missing; this is expected if you don't run Flux. Retrying quietly.",
                    exc,
                )
                warned = True
            await asyncio.sleep(30)
