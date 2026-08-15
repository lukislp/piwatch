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

# (namespace, pod name, node, container image)
PODS = [
    ("kube-system", "traefik-5d4f9", "pi-master", "traefik:v3.0"),
    ("kube-system", "coredns-6799f", "pi-master", "coredns/coredns:1.11.1"),
    ("kube-system", "metrics-server-54dc7", "pi-worker-1", "metrics-server:v0.7.0"),
    ("monitoring", "piwatch-7c9d4-a", "pi-worker-1", "ghcr.io/lukislp/piwatch:1.5.1"),
    # deliberately one version behind its sibling -- showcases the Workloads
    # tab's rollout-drift indicator (replicas of the same Deployment on
    # different image tags) without needing a real in-progress rollout.
    ("monitoring", "piwatch-7c9d4-b", "pi-worker-2", "ghcr.io/lukislp/piwatch:1.5.0"),
    ("home", "home-assistant-0", "pi-worker-1", "ghcr.io/home-assistant/home-assistant:2024.1"),
    ("home", "mosquitto-6b8d2", "pi-worker-2", "eclipse-mosquitto:2"),
    ("home", "node-red-59fd7", "pi-worker-2", "nodered/node-red:3"),
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
    for ns, pod, node, image in PODS:
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
                "images": [image],
                "created": time.time() - rng.randint(3600, 86400 * 7),
            },
        )
    # "updated" < replicas for piwatch matches the pods above (one replica still on
    # the previous version) -- both rollout-drift signals agree, like a real slow rollout.
    for ns, name, replicas, updated, images in [
        ("monitoring", "piwatch", 2, 1, ["ghcr.io/lukislp/piwatch:1.5.1"]),
        ("home", "home-assistant", 1, 1, ["ghcr.io/home-assistant/home-assistant:2024.1"]),
        ("home", "node-red", 1, 1, ["nodered/node-red:3"]),
        ("kube-system", "coredns", 1, 1, ["coredns/coredns:1.11.1"]),
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
                "updated": updated,
                "images": images,
            },
        )

    # --- walkers per node ---
    cpu = {n: _Walker(rng.uniform(15, 45), 2, 95, 6) for n in NODES}
    mem = {n: _Walker(rng.uniform(35, 60), 20, 90, 2) for n in NODES}
    temp = {n: _Walker(rng.uniform(45, 55), 35, 78, 1.5) for n in NODES}
    disk = {n: _Walker(rng.uniform(30, 55), 10, 95, 0.3) for n in NODES}

    # --- NVMe: static identity + slow-drifting health, per node ---
    nvme_static = {
        n: {
            "nvme_model": "Demo NVMe 256GB",
            "nvme_firmware": "DEMO1.0",
            "nvme_serial": f"DEMOSN{i:04d}",
            "nvme_capacity_bytes": 256 * 1000**3,
            "nvme_spare_thresh": 10,
            "nvme_unsafe_shutdowns": rng.randint(1, 8),
            "nvme_power_cycles": rng.randint(5, 40),
            "nvme_media_errors": 0,
            "nvme_critical_warning": 0,
            "nvme_num_err_log_entries": 0,
            "nvme_warning_temp_time": 0,
            "nvme_critical_comp_time": 0,
        }
        for i, n in enumerate(NODES)
    }
    nvme_temp = {n: _Walker(rng.uniform(32, 40), 25, 55, 1.0) for n in NODES}
    nvme_wear = {n: _Walker(rng.uniform(1, 4), 0, 100, 0.02) for n in NODES}  # drifts up very slowly
    nvme_read_rate = {n: _Walker(rng.uniform(0.2, 2) * 1024**2, 0, 40 * 1024**2, 4 * 1024**2) for n in NODES}
    nvme_write_rate = {n: _Walker(rng.uniform(0.1, 1) * 1024**2, 0, 20 * 1024**2, 2 * 1024**2) for n in NODES}
    nvme_reads = {n: rng.randint(500_000, 2_000_000) for n in NODES}
    nvme_writes = {n: rng.randint(500_000, 2_000_000) for n in NODES}

    # --- walkers per pod (small, plausible per-container CPU/RAM usage) ---
    pod_cpu = {f"{ns}/{pod}": _Walker(rng.uniform(0.01, 0.15), 0.005, 0.6, 0.03) for ns, pod, _, _ in PODS}
    pod_mem = {
        f"{ns}/{pod}": _Walker(rng.uniform(30, 150) * 1024**2, 16 * 1024**2, 400 * 1024**2, 8 * 1024**2)
        for ns, pod, _, _ in PODS
    }

    tick = 0
    while True:
        for i, n in enumerate(NODES):
            state.record_node_sample(
                n, {"cpu_pct": cpu[n].next(), "mem_pct": mem[n].next()}
            )
            read_bps = nvme_read_rate[n].next()
            write_bps = nvme_write_rate[n].next()
            nvme_reads[n] += int(read_bps * 5 / 512_000)  # 5s tick, bytes -> "data units"
            nvme_writes[n] += int(write_bps * 5 / 512_000)
            wear = nvme_wear[n].next()
            state.record_hardware(
                n,
                {
                    "temp_c": temp[n].next(),
                    "disk_used_pct": disk[n].next(),
                    "load1": round(cpu[n].value / 25, 2),
                    "uptime_s": int(time.time() - state.started_at) + 86400 * 12,
                    "nvme_temp_c": nvme_temp[n].next(),
                    "nvme_percent_used": wear,
                    "nvme_avail_spare": round(max(0, 100 - wear * 0.6), 1),
                    "nvme_power_on_hours": int((time.time() - state.started_at) / 3600) + 300 + i * 120,
                    "nvme_data_units_read": nvme_reads[n],
                    "nvme_data_units_written": nvme_writes[n],
                    "nvme_host_read_commands": nvme_reads[n] * 8,
                    "nvme_host_write_commands": nvme_writes[n] * 8,
                    "nvme_controller_busy_time": int((time.time() - state.started_at) / 60),
                    "nvme_read_bytes_per_s": int(read_bps),
                    "nvme_write_bytes_per_s": int(write_bps),
                    "undervoltage": False,
                    **nvme_static[n],
                },
            )
        for ns, pod, _, _ in PODS:
            key = f"{ns}/{pod}"
            state.record_pod_sample(
                key, {"cpu_cores": round(pod_cpu[key].next(), 3), "mem_bytes": int(pod_mem[key].next())}
            )
        # occasionally emit an event / a pod restart
        if tick % 6 == 0:
            etype, reason, msg = rng.choice(EVENT_SAMPLES)
            ns, pod, _, _ = rng.choice(PODS)
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
