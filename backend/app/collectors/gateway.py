"""Polls Gateway API resources (gateway.networking.k8s.io) for routing status:

- Gateway -- is the listener actually programmed/accepted, which address it got,
  how many routes attached per listener
- HTTPRoute -- is it accepted by its parent Gateway(s) and are its backend Service
  refs resolved (catches "route points at a Service that doesn't exist/match" --
  a failure mode invisible from the Deployment/Pod view alone)

Optional: Gateway API is not a hard dependency of PiWatch. Gateways and HTTPRoutes
are polled and degraded independently, same reasoning as collectors/flux.py.
"""
from __future__ import annotations

import asyncio
import logging

from ..state import ClusterState

log = logging.getLogger("piwatch.gateway")

POLL_INTERVAL = 15
RETRY_INTERVAL = 30
GROUP = "gateway.networking.k8s.io"
VERSION = "v1"


def _condition(conditions: list[dict], type_: str) -> dict:
    return next((c for c in conditions if c.get("type") == type_), {})


def _map_gateway(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")
    conditions = status.get("conditions") or []
    programmed = _condition(conditions, "Programmed")

    listeners_status = status.get("listeners") or []
    listeners_ready = sum(
        1 for listener in listeners_status
        if _condition(listener.get("conditions") or [], "Programmed").get("status") == "True"
    )
    attached_routes = sum(listener.get("attachedRoutes", 0) for listener in listeners_status)

    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "gateway_class_name": spec.get("gatewayClassName"),
        "ready": programmed.get("status") == "True",
        "reason": programmed.get("reason"),
        "message": programmed.get("message"),
        "addresses": [a.get("value") for a in (status.get("addresses") or []) if a.get("value")],
        "listener_count": len(spec.get("listeners") or []),
        "listeners_ready": listeners_ready,
        "attached_routes": attached_routes,
        # Per-listener hostname/port/protocol -- not surfaced in the UI (listener_count/
        # listeners_ready above is enough there), but collectors/autochecks.py needs it to
        # pick the right port+SNI hostname when probing a route through this Gateway.
        "listeners": [
            {
                "name": listener.get("name"),
                "hostname": listener.get("hostname"),
                "port": listener.get("port"),
                "protocol": listener.get("protocol"),
            }
            for listener in (spec.get("listeners") or [])
        ],
    }


def _map_http_route(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")

    parent_refs = [p for p in (spec.get("parentRefs") or []) if p.get("name")]
    parent_names = [p["name"] for p in parent_refs]
    # namespace is optional on a parentRef -- defaults to the route's own namespace per the
    # Gateway API spec (a cross-namespace reference always sets it explicitly; same-namespace
    # references usually don't bother, per the spec's own examples).
    parent_namespaces = [p.get("namespace") or namespace for p in parent_refs]
    backend_names = sorted({
        b.get("name")
        for rule in (spec.get("rules") or [])
        for b in (rule.get("backendRefs") or [])
        if b.get("name")
    })

    parents_status = status.get("parents") or []
    accepted = True
    resolved_refs = True
    reason = None
    message = None
    for parent in parents_status:
        conditions = parent.get("conditions") or []
        accepted_cond = _condition(conditions, "Accepted")
        resolved_cond = _condition(conditions, "ResolvedRefs")
        if accepted_cond.get("status") != "True":
            accepted = False
            reason = reason or accepted_cond.get("reason")
            message = message or accepted_cond.get("message")
        if resolved_cond.get("status") != "True":
            resolved_refs = False
            reason = reason or resolved_cond.get("reason")
            message = message or resolved_cond.get("message")
    if not parents_status:
        # No status yet (e.g. just created, or no matching Gateway found at all) --
        # can't claim it's accepted just because nothing said otherwise.
        accepted = False

    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "hostnames": spec.get("hostnames") or [],
        "parent_names": parent_names,
        "parent_namespaces": parent_namespaces,
        "backend_names": backend_names,
        "accepted": accepted,
        "resolved_refs": resolved_refs,
        "reason": reason,
        "message": message,
    }


async def _list(custom, plural: str) -> list[dict]:
    result = await custom.list_cluster_custom_object(GROUP, VERSION, plural)
    return result.get("items", [])


async def run(state: ClusterState):
    from kubernetes_asyncio import client

    warned: dict[str, bool] = {}

    def warn_once(kind: str, exc: Exception) -> None:
        if not warned.get(kind):
            log.info(
                "Gateway API %s unavailable (%s) -- not installed, or RBAC missing; this is "
                "expected if you don't use the Gateway API. Retrying quietly.",
                kind, exc,
            )
            warned[kind] = True

    while True:
        try:
            async with client.ApiClient() as api_client:
                custom = client.CustomObjectsApi(api_client)
                while True:
                    try:
                        items = await _list(custom, "gateways")
                        mapped = {}
                        for item in items:
                            m = _map_gateway(item)
                            mapped[m["key"]] = m
                        state.set_gateways(mapped)
                        warned["gateways"] = False
                    except Exception as exc:
                        warn_once("gateways", exc)

                    try:
                        items = await _list(custom, "httproutes")
                        mapped = {}
                        for item in items:
                            m = _map_http_route(item)
                            mapped[m["key"]] = m
                        state.set_http_routes(mapped)
                        warned["httproutes"] = False
                    except Exception as exc:
                        warn_once("httproutes", exc)

                    await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            warn_once("gateway-api", exc)
            await asyncio.sleep(RETRY_INTERVAL)
