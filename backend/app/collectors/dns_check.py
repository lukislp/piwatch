"""Periodically resolves a well-known in-cluster DNS name using the pod's own resolver --
which is CoreDNS by default per Kubernetes' pod DNS policy -- to catch a classic, otherwise
invisible failure mode: cluster DNS being broken or slow. Every other collector that talks
to another Service already depends on this working; nothing else in PiWatch actually
verifies it.

Always on in real-cluster mode, unlike autochecks.py's opt-in auto-discovered checks: this
needs no RBAC (pure DNS resolution via the stdlib resolver, no Kubernetes API calls) and has
no discovery/probing surprise-factor -- it's one fixed, well-known target, not something that
actively reaches out to arbitrary discovered services.

Feeds into the same state.healthchecks as every other check (via state.record_check()), so
it shows up on the existing Checks page with no new frontend needed.
"""
from __future__ import annotations

import asyncio
import time

from ..state import ClusterState

CHECK_NAME = "coredns"
HOSTNAME = "kubernetes.default.svc.cluster.local"
POLL_INTERVAL = 30
TIMEOUT = 5


async def _resolve(hostname: str) -> tuple[bool, float | None, str]:
    start = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.getaddrinfo(hostname, None), timeout=TIMEOUT)
        ms = (time.perf_counter() - start) * 1000
        return True, round(ms, 1), "resolved"
    except Exception as exc:
        return False, None, type(exc).__name__


async def run(state: ClusterState):
    config = {"name": CHECK_NAME, "type": "dns", "url": f"dns://{HOSTNAME}"}
    while True:
        ok, ms, detail = await _resolve(HOSTNAME)
        state.record_check(CHECK_NAME, config, ok, ms, detail)
        await asyncio.sleep(POLL_INTERVAL)
