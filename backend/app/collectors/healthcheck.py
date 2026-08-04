"""HTTP/TCP healthchecks for arbitrary services in the stack (Home Assistant,
MQTT, internal apps, ...). Configured via a YAML file mounted from a
ConfigMap; results (uptime %, latency) live in the state's ring buffers.

Config example (deploy/configmap-healthchecks.yaml):

checks:
  - name: Home Assistant
    type: http
    url: http://homeassistant.home.svc:8123
    interval: 30        # seconds, default 30
    timeout: 5          # seconds, default 5
    expected_status: 200
  - name: MQTT Broker
    type: tcp
    host: mosquitto.home.svc
    port: 1883
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
import yaml

from ..state import ClusterState

log = logging.getLogger("piwatch.checks")

CHECKS_FILE = os.environ.get("PIWATCH_CHECKS_FILE", "/config/healthchecks.yaml")

DEMO_CHECKS = [
    {"name": "Home Assistant", "type": "http", "url": "http://demo.invalid", "interval": 15, "demo_ok": 0.98},
    {"name": "MQTT Broker", "type": "tcp", "host": "demo.invalid", "port": 1883, "interval": 15, "demo_ok": 0.995},
    {"name": "Node-RED", "type": "http", "url": "http://demo.invalid:1880", "interval": 15, "demo_ok": 0.9},
]


def load_checks(demo: bool) -> list[dict]:
    if demo:
        return DEMO_CHECKS
    try:
        with open(CHECKS_FILE) as f:
            data = yaml.safe_load(f) or {}
        checks = data.get("checks", [])
        log.info("%d healthchecks loaded from %s", len(checks), CHECKS_FILE)
        return checks
    except FileNotFoundError:
        log.info("No healthcheck configuration (%s) -- skipping", CHECKS_FILE)
        return []
    except Exception as exc:
        log.error("Healthcheck configuration unreadable: %s", exc)
        return []


async def _run_http(check: dict, client: httpx.AsyncClient) -> tuple[bool, float | None, str]:
    start = time.perf_counter()
    try:
        resp = await client.get(check["url"], timeout=check.get("timeout", 5))
        ms = (time.perf_counter() - start) * 1000
        expected = check.get("expected_status")
        ok = resp.status_code == expected if expected else resp.status_code < 400
        return ok, round(ms, 1), f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, None, type(exc).__name__


async def _run_tcp(check: dict) -> tuple[bool, float | None, str]:
    start = time.perf_counter()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(check["host"], check["port"]),
            timeout=check.get("timeout", 5),
        )
        writer.close()
        await writer.wait_closed()
        ms = (time.perf_counter() - start) * 1000
        return True, round(ms, 1), "TCP open"
    except Exception as exc:
        return False, None, type(exc).__name__


async def _run_demo(check: dict) -> tuple[bool, float | None, str]:
    import random

    await asyncio.sleep(random.uniform(0.01, 0.1))
    ok = random.random() < check.get("demo_ok", 0.97)
    return ok, round(random.uniform(5, 120), 1) if ok else None, "Demo"


async def _check_loop(state: ClusterState, check: dict, demo: bool):
    interval = check.get("interval", 30)
    async with httpx.AsyncClient(follow_redirects=True, verify=False) as client:
        while True:
            if demo:
                ok, ms, detail = await _run_demo(check)
            elif check.get("type", "http") == "tcp":
                ok, ms, detail = await _run_tcp(check)
            else:
                ok, ms, detail = await _run_http(check, client)
            public_cfg = {k: v for k, v in check.items() if k != "demo_ok"}
            state.record_check(check["name"], public_cfg, ok, ms, detail)
            await asyncio.sleep(interval)


async def run(state: ClusterState):
    checks = load_checks(state.demo_mode)
    if not checks:
        return
    await asyncio.gather(
        *(_check_loop(state, c, state.demo_mode) for c in checks)
    )
