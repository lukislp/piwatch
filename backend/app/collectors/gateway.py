"""Polls Gateway API resources (gateway.networking.k8s.io) for routing status:

- Gateway -- is the listener actually programmed/accepted, which address it got,
  how many routes attached per listener
- HTTPRoute -- is it accepted by its parent Gateway(s) and are its backend Service
  refs resolved (catches "route points at a Service that doesn't exist/match" --
  a failure mode invisible from the Deployment/Pod view alone)
- RateLimitPolicy (gateway.nginx.org, NOT part of the standard Gateway API -- an NGINX
  Gateway Fabric extension CRD) -- configured limits per targeted HTTPRoute, and whether
  the policy was actually accepted

Optional: Gateway API is not a hard dependency of PiWatch. Each resource kind is polled
and degraded independently, same reasoning as collectors/flux.py -- RateLimitPolicy in
particular only exists at all if you're specifically on NGINX Gateway Fabric, not every
Gateway API implementation.
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
NGINX_GROUP = "gateway.nginx.org"
NGINX_VERSION = "v1alpha1"


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


def _map_rate_limit_policy(item: dict) -> dict:
    meta = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    namespace = meta.get("namespace", "")
    name = meta.get("name", "")

    targets = [
        f"{t.get('kind')}/{t.get('name')}" for t in (spec.get("targetRefs") or []) if t.get("name")
    ]
    rules = ((spec.get("rateLimit") or {}).get("local") or {}).get("rules") or []
    rule_summaries = [
        f"{r['rate']}" + (f" (burst {r['burst']})" if r.get("burst") else "")
        for r in rules
        if r.get("rate")
    ]

    # status.ancestors: one entry per Gateway that actually applies this policy (a policy
    # can target a route attached to more than one Gateway) -- accepted only if every
    # ancestor accepted it, same all-must-agree reasoning as _map_http_route's parents.
    ancestors = status.get("ancestors") or []
    accepted = True
    reason = None
    message = None
    for ancestor in ancestors:
        accepted_cond = _condition(ancestor.get("conditions") or [], "Accepted")
        if accepted_cond.get("status") != "True":
            accepted = False
            reason = reason or accepted_cond.get("reason")
            message = message or accepted_cond.get("message")
    if not ancestors:
        accepted = False

    return {
        "key": f"{namespace}/{name}",
        "name": name,
        "namespace": namespace,
        "targets": targets,
        "rules": rule_summaries,
        "reject_code": (spec.get("rateLimit") or {}).get("rejectCode"),
        "accepted": accepted,
        "reason": reason,
        "message": message,
    }


async def _list(custom, plural: str, group: str = GROUP, version: str = VERSION) -> list[dict]:
    result = await custom.list_cluster_custom_object(group, version, plural)
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

                    try:
                        items = await _list(custom, "ratelimitpolicies", NGINX_GROUP, NGINX_VERSION)
                        mapped = {}
                        for item in items:
                            m = _map_rate_limit_policy(item)
                            mapped[m["key"]] = m
                        state.set_rate_limit_policies(mapped)
                        warned["ratelimitpolicies"] = False
                    except Exception as exc:
                        warn_once("ratelimitpolicies", exc)

                    await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            warn_once("gateway-api", exc)
            await asyncio.sleep(RETRY_INTERVAL)
