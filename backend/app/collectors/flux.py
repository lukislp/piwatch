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

from ..state import ClusterState

log = logging.getLogger("piwatch.flux")

POLL_INTERVAL = 15
GROUP = "kustomize.toolkit.fluxcd.io"
VERSION = "v1"
PLURAL = "kustomizations"


def _map_kustomization(item: dict) -> dict:
    meta = item.get("metadata", {})
    status = item.get("status", {})
    conditions = status.get("conditions", [])
    ready_cond = next((c for c in conditions if c.get("type") == "Ready"), {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "ready": ready_cond.get("status") == "True",
        "reason": ready_cond.get("reason"),
        "message": ready_cond.get("message"),
        "last_applied_revision": status.get("lastAppliedRevision"),
        "last_transition_time": ready_cond.get("lastTransitionTime"),
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
