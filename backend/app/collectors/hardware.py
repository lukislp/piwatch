"""Polls the per-node hardware agents (DaemonSet) for Raspberry-Pi vitals
that Kubernetes itself does not expose: CPU temperature, load, disk, uptime.

The agents are discovered by resolving the headless Service DNS name
(PIWATCH_AGENT_SERVICE), which returns one A record per agent pod.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

import httpx

from ..state import ClusterState

log = logging.getLogger("piwatch.hardware")

AGENT_SERVICE = os.environ.get(
    "PIWATCH_AGENT_SERVICE", "piwatch-node-agent.monitoring.svc.cluster.local"
)
AGENT_PORT = int(os.environ.get("PIWATCH_AGENT_PORT", "9101"))
POLL_INTERVAL = 10


async def _resolve_agents() -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            AGENT_SERVICE, AGENT_PORT, type=socket.SOCK_STREAM
        )
        return sorted({info[4][0] for info in infos})
    except socket.gaierror:
        return []


async def run(state: ClusterState):
    async with httpx.AsyncClient(timeout=5) as client:
        misses = 0
        while True:
            ips = await _resolve_agents()
            if not ips:
                misses += 1
                if misses in (1, 30):  # log rarely, not every 10s
                    log.info("No node-agents resolvable at %s", AGENT_SERVICE)
            for ip in ips:
                try:
                    resp = await client.get(f"http://{ip}:{AGENT_PORT}/metrics")
                    data = resp.json()
                    node = data.pop("node", ip)
                    mem_total = data.get("mem_total_kb") or 0
                    mem_avail = data.get("mem_available_kb") or 0
                    if mem_total:
                        data["mem_pct"] = round(
                            100 * (mem_total - mem_avail) / mem_total, 1
                        )
                    state.record_hardware(node, data)
                except Exception as exc:
                    log.debug("Agent %s unreachable: %s", ip, exc)
            await asyncio.sleep(POLL_INTERVAL)
