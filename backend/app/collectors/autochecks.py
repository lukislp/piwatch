"""Auto-generates healthchecks from cluster resources PiWatch already discovered
(Gateway API HTTPRoutes, LoadBalancer Services) -- opt-in via PIWATCH_AUTO_HEALTHCHECKS,
since unlike every other collector this one actively probes network targets instead of
just displaying what the Kubernetes API already told it.

Additive: runs alongside collectors/healthcheck.py's YAML-configured checks, not instead
of them -- both write into the same state.healthchecks via state.record_check(), keyed by
check name. A manually-configured check that happens to share a name with an
auto-discovered one will have its result overwritten by whichever last reported; harmless
in practice (the same target, checked two different ways, rarely disagrees for long) and
not worth extra bookkeeping to prevent.

Route checks connect directly to the parent Gateway's Service ClusterIP -- never the
route's public hostname, which usually isn't reachable from inside the cluster's own pod
network (NAT hairpin / internal split-horizon DNS the cluster's own resolver doesn't know
about). The Service is found by matching its externally-assigned address against the
Gateway's own status.addresses -- both reflect the same LoadBalancer-assigned IP, confirmed
live against a real cluster. NOT matched by name/namespace: that was the first approach
here, and it's wrong in practice -- NGINX Gateway Fabric names the Service
"<gateway-name>-<gatewayclass-name>", not the Gateway's own name (verified live: Gateway
"studylife-gateway" backed by Service "studylife-gateway-nginx", same namespace, different
name), and other Gateway API implementations use yet other conventions. Address-matching
works regardless of which implementation named what. The correct per-listener port and TLS
SNI hostname come from the Gateway's own listener list (collectors/gateway.py's `listeners`
field).

Service checks are plain TCP reachability probes against every LoadBalancer-type Service's
ClusterIP:port -- always reachable from inside the cluster regardless of whether the
LoadBalancer controller has assigned an external address yet.

Both check sets are re-computed every POLL_INTERVAL from the live state (already fed by
k8s_watch.py/gateway.py), and each check's own probe loop is started/stopped to match --
new routes/Services get checked within one poll cycle, removed ones stop and disappear
from the UI, no restart needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import time

from ..state import ClusterState

log = logging.getLogger("piwatch.autochecks")

POLL_INTERVAL = 15  # matches gateway.py's own poll cadence
CHECK_INTERVAL = 30
TIMEOUT = 5


def _enabled() -> bool:
    return os.environ.get("PIWATCH_AUTO_HEALTHCHECKS", "") in ("1", "true", "yes")


def _find_listener(gateway: dict, hostname: str) -> dict | None:
    """The listener whose own hostname matches the route's, or -- if a listener declares
    no hostname at all (a single catch-all listener) -- the first HTTP/HTTPS one."""
    listeners = gateway.get("listeners") or []
    for listener in listeners:
        if listener.get("hostname") == hostname and listener.get("protocol") in ("HTTP", "HTTPS"):
            return listener
    for listener in listeners:
        if not listener.get("hostname") and listener.get("protocol") in ("HTTP", "HTTPS"):
            return listener
    return None


def _gateway_cluster_ip(gateway: dict, services: dict[str, dict]) -> str | None:
    """The Service fronting this Gateway, found by matching externally-assigned addresses
    -- NOT name/namespace (see module docstring for why that doesn't work in practice)."""
    gw_addresses = set(gateway.get("addresses") or [])
    if not gw_addresses:
        return None
    for svc in services.values():
        if gw_addresses & set(svc.get("external_ips") or []):
            return svc.get("cluster_ip")
    return None


def route_checks(state: ClusterState) -> dict[str, dict]:
    """Pure function of the current state -- easy to test without a running collector."""
    checks: dict[str, dict] = {}
    for route in state.http_routes.values():
        if not (route.get("accepted") and route.get("resolved_refs")):
            continue
        for parent_name, parent_namespace in zip(
            route.get("parent_names") or [], route.get("parent_namespaces") or []
        ):
            gateway = state.gateways.get(f"{parent_namespace}/{parent_name}")
            if not gateway:
                continue
            cluster_ip = _gateway_cluster_ip(gateway, state.services)
            if not cluster_ip:
                continue
            for hostname in route.get("hostnames") or []:
                listener = _find_listener(gateway, hostname)
                if not listener or not listener.get("port"):
                    continue
                checks[hostname] = {
                    "kind": "route",
                    "cluster_ip": cluster_ip,
                    "port": listener["port"],
                    "tls": listener.get("protocol") == "HTTPS",
                    "hostname": hostname,
                }
    return checks


def service_checks(state: ClusterState) -> dict[str, dict]:
    checks: dict[str, dict] = {}
    for svc in state.services.values():
        if not svc.get("cluster_ip"):
            continue
        for port in svc.get("ports") or []:
            if not port.get("port"):
                continue
            name = f"{svc['namespace']}/{svc['name']}:{port['port']}"
            checks[name] = {
                "kind": "service",
                "cluster_ip": svc["cluster_ip"],
                "port": port["port"],
                "service_ref": f"{svc['namespace']}/{svc['name']}",
            }
    return checks


async def _probe_route(cluster_ip: str, port: int, hostname: str, tls: bool) -> tuple[bool, float | None, str]:
    start = time.perf_counter()
    writer = None
    try:
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cluster_ip, port, ssl=ctx, server_hostname=hostname),
                timeout=TIMEOUT,
            )
        else:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cluster_ip, port), timeout=TIMEOUT
            )
        writer.write(f"GET / HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        status_line = await asyncio.wait_for(_reader.readline(), timeout=TIMEOUT)
        ms = (time.perf_counter() - start) * 1000
        parts = status_line.decode(errors="replace").split(maxsplit=2)
        code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        ok = code is not None and code < 400
        return ok, round(ms, 1), (f"HTTP {code}" if code else "no response")
    except Exception as exc:
        return False, None, type(exc).__name__
    finally:
        if writer is not None:
            writer.close()
            with __import__("contextlib").suppress(Exception):
                await writer.wait_closed()


async def _probe_service(cluster_ip: str, port: int) -> tuple[bool, float | None, str]:
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(cluster_ip, port), timeout=TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        ms = (time.perf_counter() - start) * 1000
        return True, round(ms, 1), "TCP open"
    except Exception as exc:
        return False, None, type(exc).__name__


async def _check_loop(state: ClusterState, name: str, checks: dict[str, dict]):
    """`checks` is the same dict object run() mutates in place every poll cycle -- reading
    checks.get(name) here always sees the latest definition (port/IP changed, or the check
    disappeared entirely and this loop is about to be cancelled from run())."""
    while True:
        check = checks.get(name)
        if check is None:
            return
        if check["kind"] == "route":
            ok, ms, detail = await _probe_route(check["cluster_ip"], check["port"], check["hostname"], check["tls"])
            config = {"name": name, "type": "route", "url": f"{'https' if check['tls'] else 'http'}://{name}"}
        else:
            ok, ms, detail = await _probe_service(check["cluster_ip"], check["port"])
            config = {"name": name, "type": "service", "host": check["service_ref"], "port": check["port"]}
        state.record_check(name, config, ok, ms, detail)
        await asyncio.sleep(CHECK_INTERVAL)


async def run(state: ClusterState):
    if not _enabled():
        return

    tasks: dict[str, asyncio.Task] = {}
    checks: dict[str, dict] = {}
    try:
        while True:
            new_checks = {**route_checks(state), **service_checks(state)}
            checks.clear()
            checks.update(new_checks)

            for name in list(tasks):
                if name not in checks:
                    tasks.pop(name).cancel()
                    state.remove_check(name)
            for name in checks:
                if name not in tasks:
                    tasks[name] = asyncio.create_task(_check_loop(state, name, checks))

            await asyncio.sleep(POLL_INTERVAL)
    finally:
        for task in tasks.values():
            task.cancel()
