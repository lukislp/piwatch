"""Tiny per-node hardware agent, run as a DaemonSet on every Pi.

Exposes GET /metrics with CPU temperature, load, memory, disk, uptime,
network throughput, NVMe health, SD/eMMC card info, and the Pi firmware's
under-voltage flag. Reads the host's /sys and /proc, which are mounted
read-only into the pod (hostPath). Runs from the same container image as
the backend:

    uvicorn app.node_agent:app --host 0.0.0.0 --port 9101

NVMe SMART data (wear level, power-on hours, ...) additionally needs
`nvme-cli` and raw access to the NVMe admin character device, which the
kernel only grants to a privileged container -- see the securityContext
and /dev mount in deploy/daemonset-node-agent.yaml. Everything else here
(temperature, model, capacity, under-voltage, SD card info) works from
unprivileged sysfs reads alone and degrades to None/{} when unavailable
(e.g. no PoE+ M.2 HAT, not booting from SD, or running locally on a dev
machine). SD cards implement neither ATA/SCSI SMART nor an NVMe-style
wear-level log at all, so unlike NVMe there is no privileged path that
would unlock a wear percentage for them -- see read_sd_info()/
read_sd_bytes()/read_root_readonly() for the best available proxies.
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


def _host_mounts_path() -> str:
    """Path to the HOST's real /proc/mounts, as seen through the bind-mounted
    PROC directory.

    Deliberately "1/mounts", not the plain "mounts" (== "self/mounts") every
    other read_*() in this module would reach for: /proc/mounts is always a
    magic symlink resolved for the READING process, so even through a
    bind-mounted host /proc it resolves to this container's own mount
    namespace (its overlayfs rootfs) rather than the host's -- confirmed live
    on a real SD-booted Pi, where this bug made SD-card detection silently
    find nothing at all. "1" (the host's init/systemd, always in the initial
    mount namespace) sidesteps that: procfs resolves a plain numeric PID
    within the procfs instance's own (here: the host's) PID namespace, no
    "self"-style remapping to the caller involved -- the same technique
    Prometheus node_exporter uses for its filesystem collector."""
    return f"{PROC}/1/mounts"


def _root_device() -> str | None:
    """Kernel device name backing the host's root filesystem (e.g. "mmcblk0p2",
    "nvme0n1p2", "sda1"), read from the host's real /proc/mounts (see
    _host_mounts_path()) -- used to auto-detect whether a node boots from an
    SD/eMMC card, NVMe, or something else, instead of hardcoding a device
    name that varies across Pi models/storage setups."""
    try:
        with open(_host_mounts_path()) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/":
                    return parts[0].removeprefix("/dev/")
    except OSError:
        pass
    return None


def read_root_readonly() -> bool | None:
    """True if the host's root filesystem is currently mounted read-only -- the
    classic "SD card is dying" symptom on a Pi: the kernel force-remounts root
    read-only after uncorrectable storage I/O errors, well before the node
    would otherwise show any other sign of trouble. Not SD-specific (any root
    device can hit this), but paired with the SD wear proxies below since SD
    cards are by far the most common trigger on a Pi. None if /proc/mounts or
    the root entry within it couldn't be read at all."""
    try:
        with open(_host_mounts_path()) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/":
                    return "ro" in parts[3].split(",")
    except OSError:
        pass
    return None


def _sd_block_device() -> str | None:
    """Base mmcblk device (e.g. "mmcblk0") backing the host's root filesystem,
    or None if root isn't on an SD/eMMC card at all (NVMe- or USB-booted
    nodes)."""
    dev = _root_device()
    if not dev or not dev.startswith("mmcblk"):
        return None
    # mmc partitions always use a literal "p" separator (mmcblk0p2), unlike
    # sd*/nvme naming, because the base device name itself ends in a digit.
    return dev.split("p")[0]


def read_sd_info() -> dict:
    """Identity of the SD/eMMC card backing root, from its sysfs mmc_card
    attributes -- unprivileged, and (unlike NVMe SMART/wear-level logs) works
    for genuine SD cards too: SD/MMC cards do not implement ATA/SCSI SMART or
    an NVMe-style wear-level log at all, so there is no percent-used figure to
    read here. "sd_manufacture_date" (the card's mm/yyyy manufacturing date)
    is the closest thing to an age/wear proxy this hardware exposes on its
    own -- combine with read_sd_bytes()'s cumulative write volume and
    read_root_readonly()'s failure signal for a fuller picture."""
    dev = _sd_block_device()
    if not dev:
        return {}
    out: dict = {}
    base = f"{SYS}/block/{dev}/device"
    for attr, key in (
        ("name", "sd_model"),
        ("serial", "sd_serial"),
        ("date", "sd_manufacture_date"),
        ("type", "sd_type"),
    ):
        try:
            with open(f"{base}/{attr}") as f:
                out[key] = f.read().strip()
        except OSError:
            continue
    try:
        with open(f"{SYS}/block/{dev}/size") as f:
            out["sd_capacity_bytes"] = int(f.read().strip()) * 512  # sysfs "size" is in 512B sectors
    except (OSError, ValueError):
        pass
    return out


def read_sd_bytes() -> tuple[int, int] | None:
    """Cumulative (bytes_read, bytes_written) for the SD/eMMC device backing
    root, from /sys/block/<dev>/stat (kernel block-layer stat format -- the
    sector unit there is always 512 bytes regardless of the device's real
    block size, see https://www.kernel.org/doc/Documentation/block/stat.txt).
    None if root isn't on an SD/eMMC card, or the sysfs file isn't readable.
    Total bytes written over time is the closest available wear proxy for a
    device with no wear-level register of its own."""
    dev = _sd_block_device()
    if not dev:
        return None
    try:
        with open(f"{SYS}/block/{dev}/stat") as f:
            fields = f.read().split()
        return int(fields[2]) * 512, int(fields[6]) * 512
    except (OSError, ValueError, IndexError):
        return None


# Previous (timestamp, bytes_read, bytes_written) sample -- same derive-a-rate-from-a-
# cumulative-counter approach as _net_io_rate()/_nvme_io_rate(), see their comments for
# why a single process-wide variable is enough (one node-agent monitors one root device).
_last_sd_io: tuple[float, int, int] | None = None


def _sd_io_rate(read_bytes: int | None, write_bytes: int | None) -> dict:
    """Bytes/s read+written since the previous /metrics call. Empty on the
    first call, or when read_sd_bytes() found nothing to sample."""
    global _last_sd_io
    if read_bytes is None or write_bytes is None:
        return {}
    now_ = time.monotonic()
    prev = _last_sd_io
    _last_sd_io = (now_, read_bytes, write_bytes)
    if prev is None:
        return {}
    prev_t, prev_read, prev_write = prev
    elapsed = now_ - prev_t
    if elapsed <= 0:
        return {}
    return {
        "sd_read_bytes_per_s": round(max(0, read_bytes - prev_read) / elapsed),
        "sd_write_bytes_per_s": round(max(0, write_bytes - prev_write) / elapsed),
    }


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


def _physical_net_interfaces() -> list[str]:
    """Real NICs only -- a backing sysfs "device" link is how the kernel
    distinguishes them from virtual interfaces (loopback, and the veth/cni0/
    flannel.1 pairs k3s creates per pod), which would otherwise double-count
    traffic that never actually leaves the node."""
    out = []
    for path in sorted(glob.glob(f"{SYS}/class/net/*")):
        iface = os.path.basename(path)
        if iface == "lo":
            continue
        if os.path.exists(f"{path}/device"):
            out.append(iface)
    return out


def read_net_bytes() -> tuple[int, int] | None:
    """Cumulative (rx_bytes, tx_bytes), summed across all physical interfaces.
    None if there are none, or none of them were readable."""
    ifaces = _physical_net_interfaces()
    rx = tx = 0
    ok = False
    for iface in ifaces:
        try:
            with open(f"{SYS}/class/net/{iface}/statistics/rx_bytes") as f:
                rx += int(f.read().strip())
            with open(f"{SYS}/class/net/{iface}/statistics/tx_bytes") as f:
                tx += int(f.read().strip())
            ok = True
        except (OSError, ValueError):
            continue
    return (rx, tx) if ok else None


# Previous (timestamp, rx_bytes, tx_bytes) sample -- same derive-a-rate-from-a-cumulative-
# counter approach as _nvme_io_rate(), see its comment for why this is a single process-wide
# variable rather than something keyed per-interface.
_last_net_io: tuple[float, int, int] | None = None


def _net_io_rate(rx_bytes: int | None, tx_bytes: int | None) -> dict:
    """Bytes/s in+out since the previous /metrics call. Empty on the first
    call, or when read_net_bytes() found nothing to sample."""
    global _last_net_io
    if rx_bytes is None or tx_bytes is None:
        return {}
    now_ = time.monotonic()
    prev = _last_net_io
    _last_net_io = (now_, rx_bytes, tx_bytes)
    if prev is None:
        return {}
    prev_t, prev_rx, prev_tx = prev
    elapsed = now_ - prev_t
    if elapsed <= 0:
        return {}
    return {
        "net_rx_bytes_per_s": round(max(0, rx_bytes - prev_rx) / elapsed),
        "net_tx_bytes_per_s": round(max(0, tx_bytes - prev_tx) / elapsed),
    }


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
        "root_readonly": read_root_readonly(),
    }
    payload.update(read_nvme_info())
    smart = read_nvme_smart()
    payload.update(smart)
    payload.update(read_nvme_ctrl_info())
    payload.update(_nvme_io_rate(smart.get("nvme_data_units_read"), smart.get("nvme_data_units_written")))
    payload.update(read_sd_info())
    payload.update(_sd_io_rate(*(read_sd_bytes() or (None, None))))
    net = read_net_bytes()
    payload.update(_net_io_rate(*(net or (None, None))))
    return payload


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
