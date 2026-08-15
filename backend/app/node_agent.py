"""Tiny per-node hardware agent, run as a DaemonSet on every Pi.

Exposes GET /metrics with CPU temperature, load, memory, disk, uptime, NVMe
health and the Pi firmware's under-voltage flag. Reads the host's /sys and
/proc, which are mounted read-only into the pod (hostPath). Runs from the
same container image as the backend:

    uvicorn app.node_agent:app --host 0.0.0.0 --port 9101

NVMe SMART data (wear level, power-on hours, ...) additionally needs
`nvme-cli` and raw access to the NVMe admin character device, which the
kernel only grants to a privileged container -- see the securityContext
and /dev mount in deploy/daemonset-node-agent.yaml. Everything else here
(temperature, model, capacity, under-voltage) works from unprivileged
sysfs reads alone and degrades to None when unavailable (e.g. no PoE+
M.2 HAT, or running locally on a dev machine).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time

from fastapi import FastAPI

# Paths can be remapped when /proc & /sys of the HOST are mounted elsewhere.
SYS = os.environ.get("PIWATCH_SYS", "/sys")
PROC = os.environ.get("PIWATCH_PROC", "/proc")
DISK_PATH = os.environ.get("PIWATCH_DISK_PATH", "/")
NODE_NAME = os.environ.get("NODE_NAME", os.uname().nodename)
NVME_DEVICE = os.environ.get("PIWATCH_NVME_DEVICE", "/dev/nvme0")

NVME_SMART_FIELDS = (
    "percent_used",
    "avail_spare",
    "spare_thresh",
    "power_on_hours",
    "unsafe_shutdowns",
    "media_errors",
    "critical_warning",
    "power_cycles",
    "data_units_read",
    "data_units_written",
    "host_read_commands",
    "host_write_commands",
    "controller_busy_time",
    "warning_temp_time",
    "critical_comp_time",
    "num_err_log_entries",
)

# NVMe "data units" are 512000-byte units per the NVMe spec (not 512-byte sectors).
NVME_DATA_UNIT_BYTES = 512_000

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
    # IndexError included like in read_load(): a malformed line with nothing after the
    # colon must degrade to a partial/empty dict, not 500 the /metrics endpoint.
    except (OSError, ValueError, IndexError):
        pass
    return out


def read_uptime_s() -> int | None:
    try:
        with open(f"{PROC}/uptime") as f:
            return int(float(f.read().split()[0]))
    # IndexError included like in read_load(): an empty /proc/uptime must degrade to
    # None, not 500 the /metrics endpoint.
    except (OSError, ValueError, IndexError):
        return None


def _find_hwmon(driver_name: str) -> str | None:
    """Path of the /sys/class/hwmon/hwmonN device whose driver matches
    `driver_name` (e.g. "nvme", "rpi_volt"), or None if not present.
    hwmon numbering is not stable across boots/kernels, so this always
    resolves by name rather than assuming e.g. hwmon0 == the CPU sensor.
    """
    for path in sorted(glob.glob(f"{SYS}/class/hwmon/hwmon*")):
        try:
            with open(f"{path}/name") as f:
                if f.read().strip() == driver_name:
                    return path
        except OSError:
            continue
    return None


def read_nvme_temp_c() -> float | None:
    """NVMe die temperature via its own hwmon device (millidegrees)."""
    hwmon = _find_hwmon("nvme")
    if not hwmon:
        return None
    try:
        with open(f"{hwmon}/temp1_input") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_nvme_info() -> dict:
    """Model + capacity of the first NVMe block device, from sysfs."""
    out: dict = {}
    for block in sorted(glob.glob(f"{SYS}/block/nvme*n1")):
        try:
            with open(f"{block}/device/model") as f:
                out["nvme_model"] = f.read().strip()
        except OSError:
            pass
        try:
            with open(f"{block}/size") as f:
                out["nvme_capacity_bytes"] = int(f.read().strip()) * 512  # sysfs "size" is in 512B sectors
        except (OSError, ValueError):
            pass
        break  # only the first NVMe drive is monitored
    return out


def read_undervoltage() -> bool | None:
    """Raspberry Pi firmware under-voltage flag (bad PSU/PoE splitter),
    exposed by the rpi_volt hwmon driver. None if that driver isn't loaded
    (e.g. not running on a Pi, or the kernel lacks the overlay)."""
    hwmon = _find_hwmon("rpi_volt")
    if not hwmon:
        return None
    try:
        with open(f"{hwmon}/in0_lcrit_alarm") as f:
            return f.read().strip() == "1"
    except OSError:
        return None


def read_nvme_smart() -> dict:
    """Full SMART log via `nvme-cli`. Requires a privileged container with
    access to the NVMe admin character device (NVME_DEVICE) -- the default,
    unprivileged securityContext gets EPERM on that ioctl regardless of file
    permissions, so this deliberately degrades to {} (no extra keys) rather
    than raising, keeping /metrics usable without the elevated DaemonSet.
    """
    try:
        result = subprocess.run(
            ["nvme", "smart-log", NVME_DEVICE, "-o", "json"],
            capture_output=True,
            timeout=5,
            check=True,
            text=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    return {f"nvme_{key}": data[key] for key in NVME_SMART_FIELDS if key in data}


def read_nvme_ctrl_info() -> dict:
    """Firmware revision + serial number via `nvme id-ctrl`. Model/capacity
    come from sysfs already (read_nvme_info(), unprivileged) -- this only
    adds the two fields sysfs doesn't expose. Same privilege requirement
    and failure handling as read_nvme_smart()."""
    try:
        result = subprocess.run(
            ["nvme", "id-ctrl", NVME_DEVICE, "-o", "json"],
            capture_output=True,
            timeout=5,
            check=True,
            text=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    out: dict = {}
    if "fr" in data:
        out["nvme_firmware"] = str(data["fr"]).strip()
    if "sn" in data:
        out["nvme_serial"] = str(data["sn"]).strip()
    return out


# Previous (timestamp, data_units_read, data_units_written) sample, used to derive a
# read/write bytes-per-second rate from the cumulative SMART counters below. A node-agent
# process monitors exactly one NVMe device, so one process-wide sample is enough.
_last_nvme_io: tuple[float, int, int] | None = None


def _nvme_io_rate(units_read: int | None, units_written: int | None) -> dict:
    """Bytes/s read+write since the previous /metrics call. Empty on the
    first call (no prior sample yet) or when the SMART counters themselves
    are unavailable (unprivileged node-agent)."""
    global _last_nvme_io
    if units_read is None or units_written is None:
        return {}
    now = time.monotonic()
    prev = _last_nvme_io
    _last_nvme_io = (now, units_read, units_written)
    if prev is None:
        return {}
    prev_t, prev_read, prev_written = prev
    elapsed = now - prev_t
    if elapsed <= 0:
        return {}
    return {
        "nvme_read_bytes_per_s": round(max(0, units_read - prev_read) * NVME_DATA_UNIT_BYTES / elapsed),
        "nvme_write_bytes_per_s": round(max(0, units_written - prev_written) * NVME_DATA_UNIT_BYTES / elapsed),
    }


@app.get("/metrics")
def metrics() -> dict:
    load = read_load()
    mem = read_meminfo()
    try:
        disk = shutil.disk_usage(DISK_PATH)
        disk_used_pct = round(100 * disk.used / disk.total, 1)
    except OSError:
        disk_used_pct = None
    payload = {
        "node": NODE_NAME,
        "temp_c": read_temp_c(),
        "load1": load[0] if load else None,
        "load5": load[1] if load else None,
        "mem_total_kb": mem.get("MemTotal"),
        "mem_available_kb": mem.get("MemAvailable"),
        "disk_used_pct": disk_used_pct,
        "uptime_s": read_uptime_s(),
        "nvme_temp_c": read_nvme_temp_c(),
        "undervoltage": read_undervoltage(),
    }
    payload.update(read_nvme_info())
    smart = read_nvme_smart()
    payload.update(smart)
    payload.update(read_nvme_ctrl_info())
    payload.update(_nvme_io_rate(smart.get("nvme_data_units_read"), smart.get("nvme_data_units_written")))
    return payload


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
