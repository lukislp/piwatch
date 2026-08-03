"""Demo mode: simulates a small Raspberry-Pi k3s cluster with live-changing
metrics so the dashboard can be developed and verified without a cluster.

Activated via PIWATCH_DEMO=1 (or automatically when no cluster is reachable).
"""
from __future__ import annotations

import asyncio
import random
import time

from ..state import ClusterState

NODES = ["pi-master", "pi-worker-1", "pi-worker-2"]

PODS = [
    ("kube-system", "traefik-5d4f9", "pi-master"),
    ("kube-system", "coredns-6799f", "pi-master"),
    ("kube-system", "metrics-server-54dc7", "pi-worker-1"),
    ("monitoring", "piwatch-7c9d4-a", "pi-worker-1"),
    ("monitoring", "piwatch-7c9d4-b", "pi-worker-2"),
    ("home", "home-assistant-0", "pi-worker-1"),
    ("home", "mosquitto-6b8d2", "pi-worker-2"),
    ("home", "node-red-59fd7", "pi-worker-2"),
]

EVENT_SAMPLES = [
    ("Normal", "Pulled", "Container image already present on machine"),
    ("Normal", "Scheduled", "Successfully assigned pod to node"),
    ("Warning", "BackOff", "Back-off restarting failed container"),
    ("Normal", "Started", "Started container"),
]


class _Walker:
    """Random walk that stays inside [lo, hi]."""

    def __init__(self, value: float, lo: float, hi: float, step: float):
        self.value, self.lo, self.hi, self.step = value, lo, hi, step

    def next(self) -> float:
        self.value += random.uniform(-self.step, self.step)
        self.value = max(self.lo, min(self.hi, self.value))
        return round(self.value, 1)


async def run(state: ClusterState):
    state.demo_mode = True
    rng = random.Random()

    # --- seed nodes ---
    for i, name in enumerate(NODES):
        state.upsert_node(
            name,
            {
                "name": name,
                "ready": True,
                "conditions": {"Ready": "True"},
                "roles": ["control-plane"] if i == 0 else ["worker"],
                "arch": "arm64",
                "kubelet": "v1.29.4+k3s1",
                "os_image": "Debian GNU/Linux 12 (bookworm)",
                "internal_ip": f"192.168.1.{10 + i}",
                "cpu_capacity": "4",
                "mem_capacity": "8Gi",
                "unschedulable": False,
                "created": time.time() - 86400 * 30,
            },
        )

    # --- seed pods & deployments ---
    for ns, pod, node in PODS:
        state.upsert_pod(
            f"{ns}/{pod}",
            {
                "key": f"{ns}/{pod}",
                "name": pod,
                "namespace": ns,
                "node": node,
                "phase": "Running",
                "reason": None,
                "ready": "1/1",
                "restarts": rng.randint(0, 3),
                "containers": [pod.rsplit("-", 1)[0]],
                "created": time.time() - rng.randint(3600, 86400 * 7),
            },
        )
    for ns, name, replicas in [
        ("monitoring", "piwatch", 2),
        ("home", "home-assistant", 1),
        ("home", "node-red", 1),
        ("kube-system", "coredns", 1),
    ]:
        state.upsert_deployment(
            f"{ns}/{name}",
            {
                "key": f"{ns}/{name}",
                "name": name,
                "namespace": ns,
                "replicas": replicas,
                "ready": replicas,
                "available": replicas,
                "updated": replicas,
                "images": [f"registry.local/{name}:latest"],
            },
        )

    # --- walkers per node ---
    cpu = {n: _Walker(rng.uniform(15, 45), 2, 95, 6) for n in NODES}
    mem = {n: _Walker(rng.uniform(35, 60), 20, 90, 2) for n in NODES}
    temp = {n: _Walker(rng.uniform(45, 55), 35, 78, 1.5) for n in NODES}
    disk = {n: _Walker(rng.uniform(30, 55), 10, 95, 0.3) for n in NODES}

    tick = 0
    while True:
        for n in NODES:
            state.record_node_sample(
                n, {"cpu_pct": cpu[n].next(), "mem_pct": mem[n].next()}
            )
            state.record_hardware(
                n,
                {
                    "temp_c": temp[n].next(),
                    "disk_used_pct": disk[n].next(),
                    "load1": round(cpu[n].value / 25, 2),
                    "uptime_s": int(time.time() - state.started_at) + 86400 * 12,
                },
            )
        # occasionally emit an event / a pod restart
        if tick % 6 == 0:
            etype, reason, msg = rng.choice(EVENT_SAMPLES)
            ns, pod, _ = rng.choice(PODS)
            state.add_event(
                {
                    "uid": f"demo-{tick}",
                    "type": etype,
                    "reason": reason,
                    "message": msg,
                    "object": f"Pod/{pod}",
                    "namespace": ns,
                    "count": 1,
                    "t": time.time(),
                }
            )
        tick += 1
        await asyncio.sleep(5)


async def fake_logs(namespace: str, pod: str):
    """Async generator with plausible fake log lines for the log viewer."""
    levels = ["INFO", "INFO", "INFO", "DEBUG", "WARN"]
    msgs = [
        "request handled in %dms",
        "reconciling state",
        "heartbeat ok",
        "cache refresh complete",
        "connection from 10.42.0.%d",
    ]
    i = 0
    while True:
        lvl = random.choice(levels)
        msg = random.choice(msgs)
        if "%d" in msg:
            msg = msg % random.randint(1, 250)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        yield f"{ts} {lvl} [{pod}] {msg}"
        i += 1
        await asyncio.sleep(random.uniform(0.2, 1.5))
