"""Tiny per-node hardware agent, run as a DaemonSet on every Pi.

Exposes GET /metrics with CPU temperature, load, memory, disk and uptime.
Reads the host's /sys and /proc, which are mounted read-only into the pod
(hostPath). Runs from the same container image as the backend:

    uvicorn app.node_agent:app --host 0.0.0.0 --port 9101
"""
from __future__ import annotations

import glob
import os
import shutil

from fastapi import FastAPI

# Paths can be remapped when /proc & /sys of the HOST are mounted elsewhere.
SYS = os.environ.get("PIWATCH_SYS", "/sys")
PROC = os.environ.get("PIWATCH_PROC", "/proc")
DISK_PATH = os.environ.get("PIWATCH_DISK_PATH", "/")
NODE_NAME = os.environ.get("NODE_NAME", os.uname().nodename)

app = FastAPI(title="piwatch node-agent")


def read_temp_c() -> float | None:
    """CPU temperature; on the Pi this is thermal_zone0 (millidegrees)."""
    for zone in sorted(glob.glob(f"{SYS}/class/thermal/thermal_zone*/temp")):
        try:
            with open(zone) as f:
                return round(int(f.read().strip()) / 1000, 1)
        except (OSError, ValueError):
            continue
    return None


def read_load() -> tuple[float, float, float] | None:
    try:
        with open(f"{PROC}/loadavg") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return None


def read_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open(f"{PROC}/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    out[key] = int(rest.strip().split()[0])  # kB
    except (OSError, ValueError):
        pass
    return out


def read_uptime_s() -> int | None:
    try:
        with open(f"{PROC}/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


@app.get("/metrics")
def metrics() -> dict:
    load = read_load()
    mem = read_meminfo()
    try:
        disk = shutil.disk_usage(DISK_PATH)
        disk_used_pct = round(100 * disk.used / disk.total, 1)
    except OSError:
        disk_used_pct = None
    return {
        "node": NODE_NAME,
        "temp_c": read_temp_c(),
        "load1": load[0] if load else None,
        "load5": load[1] if load else None,
        "mem_total_kb": mem.get("MemTotal"),
        "mem_available_kb": mem.get("MemAvailable"),
        "disk_used_pct": disk_used_pct,
        "uptime_s": read_uptime_s(),
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
