"""Unit tests that push coverage on the modules test_backend.py barely
touches: the Pi node-agent, the hardware/healthcheck collectors, remaining
auth edge branches, state.py's pub/sub error handling, and demo.py's
fake_logs() generator.

Style matches test_backend.py: plain functions, `asyncio.run(scenario())`
for async code (no pytest-asyncio dependency), monkeypatch for env/attrs,
direct calls into module internals rather than spinning up full apps where
that is enough to exercise the target lines.
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import socket
import subprocess
import sys
import types

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --------------------------------------------------------------------------
# app.node_agent imports `os.uname().nodename` as the *default argument* to
# os.environ.get(), so it is evaluated unconditionally at import time --
# even when NODE_NAME is set. os.uname() does not exist on Windows, so a
# bare `import app.node_agent` would crash dev machines while working fine
# on Linux CI. Stub it explicitly, once, before the only import of that
# module, so behaviour is identical (and deterministic) on both platforms.
# --------------------------------------------------------------------------
if not hasattr(os, "uname"):
    _stub_uname = types.SimpleNamespace(nodename="stub-host")
    os.uname = lambda: _stub_uname  # type: ignore[attr-defined]

from app import node_agent
from app.collectors import demo, hardware, healthcheck
from app.state import ClusterState


class _StopLoop(Exception):
    """Sentinel used to break out of collectors' `while True` loops."""


async def _raise_stop(*_a, **_kw):
    raise _StopLoop()


async def _instant_sleep(*_a, **_kw):
    """Fast stand-in for asyncio.sleep() that does not actually wait."""
    return


# =========================== app.node_agent ===============================

def test_read_temp_c_returns_millidegrees_as_celsius(tmp_path, monkeypatch):
    zone = tmp_path / "sys" / "class" / "thermal" / "thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "temp").write_text("48250")
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "sys"))
    assert node_agent.read_temp_c() == 48.2


def test_read_temp_c_skips_unreadable_zone_and_uses_next(tmp_path, monkeypatch):
    thermal = tmp_path / "sys" / "class" / "thermal"
    zone0 = thermal / "thermal_zone0"
    zone1 = thermal / "thermal_zone1"
    zone0.mkdir(parents=True)
    zone1.mkdir(parents=True)
    (zone0 / "temp").write_text("not-a-number")  # triggers ValueError, skip
    (zone1 / "temp").write_text("52340")
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "sys"))
    assert node_agent.read_temp_c() == 52.3


def test_read_temp_c_returns_none_when_no_thermal_zones(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-such-sys"))
    assert node_agent.read_temp_c() is None


def test_read_load_parses_loadavg(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("0.11 0.22 0.33 1/222 3456\n")
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_load() == (0.11, 0.22, 0.33)


def test_read_load_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "PROC", str(tmp_path / "no-proc"))
    assert node_agent.read_load() is None


def test_read_load_returns_none_on_malformed_content(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("a b c\n")
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_load() is None


def test_read_meminfo_extracts_total_and_available(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         1000000 kB\n"
        "MemAvailable:    8000000 kB\n"
    )
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_meminfo() == {"MemTotal": 16384000, "MemAvailable": 8000000}


def test_read_meminfo_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "PROC", str(tmp_path / "no-proc"))
    assert node_agent.read_meminfo() == {}


def test_read_meminfo_malformed_line_degrades_to_empty_dict(tmp_path, monkeypatch):
    """A MemTotal/MemAvailable line with nothing after the colon makes
    `rest.strip().split()[0]` raise IndexError - which the except clause
    must swallow (like read_load() does for the same parsing pattern), so
    malformed host /proc/meminfo content degrades the /metrics payload
    instead of 500ing it. Regression test for a bug found by this suite.
    """
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemTotal:\n")
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_meminfo() == {}


def test_read_uptime_s_parses_seconds(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("54321.9 12345.6\n")
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_uptime_s() == 54321


def test_read_uptime_s_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "PROC", str(tmp_path / "no-proc"))
    assert node_agent.read_uptime_s() is None


def test_read_uptime_s_empty_file_degrades_to_none(tmp_path, monkeypatch):
    """An empty /proc/uptime makes `f.read().split()[0]` raise IndexError -
    which must be swallowed to None (like read_load() does for the same
    parsing pattern) instead of 500ing the /metrics endpoint. Regression
    test for a bug found by this suite.
    """
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("")
    monkeypatch.setattr(node_agent, "PROC", str(proc))
    assert node_agent.read_uptime_s() is None


def _write_hwmon(sys_dir, hwmon_id: str, driver_name: str, files: dict[str, str]):
    hwmon = sys_dir / "class" / "hwmon" / f"hwmon{hwmon_id}"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text(driver_name)
    for fname, content in files.items():
        (hwmon / fname).write_text(content)
    return hwmon


def test_find_hwmon_matches_by_driver_name(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_hwmon(sys_dir, "0", "cpu_thermal", {})
    nvme_hwmon = _write_hwmon(sys_dir, "1", "nvme", {})
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    # normpath: glob.glob() and pathlib's str() can mix "/" and "\\" for the
    # same path on Windows -- functionally identical, but not `==`-comparable
    # as raw strings. Never an issue on Linux (CI's actual target), where "/"
    # is the only separator either side ever produces.
    assert os.path.normpath(node_agent._find_hwmon("nvme")) == os.path.normpath(str(nvme_hwmon))
    assert node_agent._find_hwmon("no-such-driver") is None


def test_read_nvme_temp_c_reads_hwmon_temp1_input(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_hwmon(sys_dir, "1", "nvme", {"temp1_input": "36847"})
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    assert node_agent.read_nvme_temp_c() == 36.8


def test_read_nvme_temp_c_returns_none_without_nvme_hwmon(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-sys"))
    assert node_agent.read_nvme_temp_c() is None


def test_read_nvme_info_extracts_model_and_capacity(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    block = sys_dir / "block" / "nvme0n1" / "device"
    block.mkdir(parents=True)
    (block / "model").write_text("Intenso SSD                            \n")
    (sys_dir / "block" / "nvme0n1" / "size").write_text("488397168")
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    info = node_agent.read_nvme_info()
    assert info["nvme_model"] == "Intenso SSD"
    assert info["nvme_capacity_bytes"] == 488397168 * 512  # sysfs "size" is in 512B sectors


def test_read_nvme_info_returns_empty_dict_when_no_nvme_block_device(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-sys"))
    assert node_agent.read_nvme_info() == {}


def test_read_undervoltage_true_when_alarm_set(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_hwmon(sys_dir, "3", "rpi_volt", {"in0_lcrit_alarm": "1"})
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    assert node_agent.read_undervoltage() is True


def test_read_undervoltage_false_when_alarm_clear(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_hwmon(sys_dir, "3", "rpi_volt", {"in0_lcrit_alarm": "0"})
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    assert node_agent.read_undervoltage() is False


def test_read_undervoltage_none_without_rpi_volt_hwmon(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-sys"))
    assert node_agent.read_undervoltage() is None


def test_read_nvme_smart_parses_known_fields_and_drops_the_rest(monkeypatch):
    payload = {
        "percent_used": 3,
        "avail_spare": 100,
        "spare_thresh": 10,
        "power_on_hours": 696,
        "unsafe_shutdowns": 21,
        "media_errors": 0,
        "critical_warning": 0,
        "power_cycles": 23,
        "data_units_read": 1454046,
        "data_units_written": 1658016,
        "host_read_commands": 21916676,
        "host_write_commands": 34912549,
        "controller_busy_time": 3987,
        "warning_temp_time": 0,
        "critical_comp_time": 0,
        "num_err_log_entries": 0,
        "temperature": 307,  # deliberately ignored -- sysfs hwmon is the temperature source
        "endurance_grp_critical_warning_summary": 0,  # not in NVME_SMART_FIELDS -- must be dropped
    }

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["nvme", "smart-log"]
        assert cmd[2] == node_agent.NVME_DEVICE
        return types.SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_smart() == {
        "nvme_percent_used": 3,
        "nvme_avail_spare": 100,
        "nvme_spare_thresh": 10,
        "nvme_power_on_hours": 696,
        "nvme_unsafe_shutdowns": 21,
        "nvme_media_errors": 0,
        "nvme_critical_warning": 0,
        "nvme_power_cycles": 23,
        "nvme_data_units_read": 1454046,
        "nvme_data_units_written": 1658016,
        "nvme_host_read_commands": 21916676,
        "nvme_host_write_commands": 34912549,
        "nvme_controller_busy_time": 3987,
        "nvme_warning_temp_time": 0,
        "nvme_critical_comp_time": 0,
        "nvme_num_err_log_entries": 0,
    }


def test_read_nvme_smart_returns_empty_dict_when_nvme_cli_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("nvme: command not found")

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_smart() == {}


def test_read_nvme_smart_returns_empty_dict_when_unprivileged(monkeypatch):
    """Matches the real non-privileged-container behavior: nvme-cli exits
    non-zero (EPERM opening the NVMe admin device) -> CalledProcessError."""

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_smart() == {}


def test_read_nvme_smart_returns_empty_dict_on_malformed_json(monkeypatch):
    def fake_run(cmd, **kwargs):
        return types.SimpleNamespace(stdout="not json")

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_smart() == {}


def test_read_nvme_ctrl_info_parses_firmware_and_serial(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["nvme", "id-ctrl"]
        return types.SimpleNamespace(stdout=json.dumps({"fr": "VDV10184  ", "sn": "S6RLNJ0T123456", "mn": "ignored here"}))

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_ctrl_info() == {"nvme_firmware": "VDV10184", "nvme_serial": "S6RLNJ0T123456"}


def test_read_nvme_ctrl_info_returns_empty_dict_when_unprivileged(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    assert node_agent.read_nvme_ctrl_info() == {}


def test_nvme_io_rate_returns_empty_on_first_sample(monkeypatch):
    """No prior sample yet -- nothing to derive a rate from."""
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)
    assert node_agent._nvme_io_rate(1000, 2000) == {}


def test_nvme_io_rate_computes_bytes_per_second_between_samples(monkeypatch):
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)
    times = iter([100.0, 105.0])
    monkeypatch.setattr(node_agent, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    assert node_agent._nvme_io_rate(1000, 2000) == {}  # seeds the first sample
    # +50 units read, +100 units written over 5s -> bytes/s = units * 512000 / elapsed
    result = node_agent._nvme_io_rate(1050, 2100)
    assert result == {
        "nvme_read_bytes_per_s": round(50 * 512_000 / 5),
        "nvme_write_bytes_per_s": round(100 * 512_000 / 5),
    }


def test_nvme_io_rate_clamps_negative_deltas_to_zero(monkeypatch):
    """A counter reset (e.g. drive replaced) must not produce a negative rate."""
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)
    times = iter([100.0, 105.0])
    monkeypatch.setattr(node_agent, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    node_agent._nvme_io_rate(1000, 2000)
    result = node_agent._nvme_io_rate(500, 2100)
    assert result["nvme_read_bytes_per_s"] == 0
    assert result["nvme_write_bytes_per_s"] == round(100 * 512_000 / 5)


def test_nvme_io_rate_returns_empty_when_counters_unavailable(monkeypatch):
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)
    assert node_agent._nvme_io_rate(None, None) == {}


def _write_net_iface(sys_dir, name: str, physical: bool, rx: int | None = None, tx: int | None = None):
    iface = sys_dir / "class" / "net" / name
    stats = iface / "statistics"
    stats.mkdir(parents=True)
    if physical:
        (iface / "device").mkdir()  # presence alone is what _physical_net_interfaces checks
    if rx is not None:
        (stats / "rx_bytes").write_text(str(rx))
    if tx is not None:
        (stats / "tx_bytes").write_text(str(tx))
    return iface


def test_physical_net_interfaces_excludes_loopback_and_virtual_ifaces(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_net_iface(sys_dir, "lo", physical=False)
    _write_net_iface(sys_dir, "veth1234abcd", physical=False)  # k3s per-pod veth pair, no "device"
    _write_net_iface(sys_dir, "eth0", physical=True)
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    assert node_agent._physical_net_interfaces() == ["eth0"]


def test_read_net_bytes_sums_across_physical_interfaces(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    _write_net_iface(sys_dir, "eth0", physical=True, rx=1000, tx=200)
    _write_net_iface(sys_dir, "wlan0", physical=True, rx=500, tx=100)
    _write_net_iface(sys_dir, "lo", physical=False, rx=999999, tx=999999)  # must be excluded
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    assert node_agent.read_net_bytes() == (1500, 300)


def test_read_net_bytes_returns_none_when_no_physical_interfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-sys"))
    assert node_agent.read_net_bytes() is None


def test_net_io_rate_computes_bytes_per_second_between_samples(monkeypatch):
    monkeypatch.setattr(node_agent, "_last_net_io", None)
    times = iter([100.0, 110.0])
    monkeypatch.setattr(node_agent, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    assert node_agent._net_io_rate(10_000, 2_000) == {}  # seeds the first sample
    result = node_agent._net_io_rate(10_500, 2_400)  # +500 rx, +400 tx over 10s
    assert result == {"net_rx_bytes_per_s": 50, "net_tx_bytes_per_s": 40}


def test_net_io_rate_returns_empty_when_bytes_unavailable(monkeypatch):
    monkeypatch.setattr(node_agent, "_last_net_io", None)
    assert node_agent._net_io_rate(None, None) == {}


def _seed_full_node_agent_fs(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sys"
    proc_dir = tmp_path / "proc"
    zone = sys_dir / "class" / "thermal" / "thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "temp").write_text("48250")
    proc_dir.mkdir()
    (proc_dir / "loadavg").write_text("0.11 0.22 0.33 1/222 3456\n")
    (proc_dir / "meminfo").write_text(
        "MemTotal:  1000000 kB\nMemAvailable:  500000 kB\nOther: 1 kB\n"
    )
    (proc_dir / "uptime").write_text("54321.9 12345.6\n")
    monkeypatch.setattr(node_agent, "SYS", str(sys_dir))
    monkeypatch.setattr(node_agent, "PROC", str(proc_dir))
    monkeypatch.setattr(node_agent, "DISK_PATH", str(tmp_path))
    monkeypatch.setattr(node_agent, "NODE_NAME", "pi-test")


def test_metrics_endpoint_full_data(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    _seed_full_node_agent_fs(tmp_path, monkeypatch)
    with TestClient(node_agent.app) as client:
        body = client.get("/metrics").json()

    assert body["node"] == "pi-test"
    assert body["temp_c"] == 48.2
    assert body["load1"] == 0.11
    assert body["load5"] == 0.22
    assert body["mem_total_kb"] == 1000000
    assert body["mem_available_kb"] == 500000
    assert body["uptime_s"] == 54321
    assert 0.0 <= body["disk_used_pct"] <= 100.0


def test_metrics_endpoint_includes_nvme_and_power_data_when_present(tmp_path, monkeypatch):
    """End-to-end: hwmon (nvme + rpi_volt), the nvme0n1 block device, and mocked
    `nvme smart-log`/`id-ctrl` calls all merge into one /metrics payload. A
    second poll additionally exercises the read/write throughput rate, which
    needs two samples to derive anything from."""
    from fastapi.testclient import TestClient

    _seed_full_node_agent_fs(tmp_path, monkeypatch)
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)
    sys_dir = tmp_path / "sys"
    _write_hwmon(sys_dir, "1", "nvme", {"temp1_input": "36847"})
    _write_hwmon(sys_dir, "3", "rpi_volt", {"in0_lcrit_alarm": "1"})
    block = sys_dir / "block" / "nvme0n1" / "device"
    block.mkdir(parents=True)
    (block / "model").write_text("Intenso SSD")
    (sys_dir / "block" / "nvme0n1" / "size").write_text("488397168")

    data_units = {"read": 1_000_000, "written": 2_000_000}

    def fake_run(cmd, **kwargs):
        if cmd[1] == "smart-log":
            return types.SimpleNamespace(stdout=json.dumps({
                "percent_used": 3,
                "avail_spare": 100,
                "power_on_hours": 696,
                "data_units_read": data_units["read"],
                "data_units_written": data_units["written"],
            }))
        assert cmd[1] == "id-ctrl"
        return types.SimpleNamespace(stdout=json.dumps({"fr": "VDV10184", "sn": "S6RLNJ0T123456"}))

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)
    times = iter([100.0, 105.0])
    monkeypatch.setattr(node_agent, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    with TestClient(node_agent.app) as client:
        first = client.get("/metrics").json()
        data_units["read"] += 100  # +100 units over the 5s between the two mocked timestamps
        data_units["written"] += 200
        second = client.get("/metrics").json()

    assert first["nvme_temp_c"] == 36.8
    assert first["nvme_model"] == "Intenso SSD"
    assert first["nvme_capacity_bytes"] == 488397168 * 512
    assert first["undervoltage"] is True
    assert first["nvme_percent_used"] == 3
    assert first["nvme_avail_spare"] == 100
    assert first["nvme_power_on_hours"] == 696
    assert first["nvme_firmware"] == "VDV10184"
    assert first["nvme_serial"] == "S6RLNJ0T123456"
    assert "nvme_read_bytes_per_s" not in first  # first poll: no prior sample to diff against

    assert second["nvme_read_bytes_per_s"] == round(100 * 512_000 / 5)
    assert second["nvme_write_bytes_per_s"] == round(200 * 512_000 / 5)


def test_metrics_endpoint_includes_network_throughput_on_second_poll(tmp_path, monkeypatch):
    """Same two-poll shape as the NVMe throughput test: the rate only exists
    once there are two samples to diff. nvme-cli isn't mocked here and isn't
    installed on the test runner, so it degrades to {} on its own and never
    touches the shared `times` iterator (see _nvme_io_rate/_net_io_rate: both
    return before calling time.monotonic() when their input is None)."""
    from fastapi.testclient import TestClient

    _seed_full_node_agent_fs(tmp_path, monkeypatch)
    monkeypatch.setattr(node_agent, "_last_net_io", None)
    sys_dir = tmp_path / "sys"
    _write_net_iface(sys_dir, "eth0", physical=True, rx=100_000, tx=20_000)

    times = iter([100.0, 110.0])
    monkeypatch.setattr(node_agent, "time", types.SimpleNamespace(monotonic=lambda: next(times)))

    with TestClient(node_agent.app) as client:
        first = client.get("/metrics").json()
        assert "net_rx_bytes_per_s" not in first  # first poll: no prior sample to diff against

        (sys_dir / "class" / "net" / "eth0" / "statistics" / "rx_bytes").write_text("101_000".replace("_", ""))
        (sys_dir / "class" / "net" / "eth0" / "statistics" / "tx_bytes").write_text("20_500".replace("_", ""))
        second = client.get("/metrics").json()

    assert second["net_rx_bytes_per_s"] == round(1000 / 10)
    assert second["net_tx_bytes_per_s"] == round(500 / 10)


def test_metrics_endpoint_degrades_gracefully_when_everything_missing(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(node_agent, "SYS", str(tmp_path / "no-sys"))
    monkeypatch.setattr(node_agent, "PROC", str(tmp_path / "no-proc"))
    monkeypatch.setattr(node_agent, "DISK_PATH", str(tmp_path / "no-disk"))
    monkeypatch.setattr(node_agent, "NODE_NAME", "pi-empty")
    monkeypatch.setattr(node_agent, "_last_nvme_io", None)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("nvme: command not found")

    monkeypatch.setattr(node_agent.subprocess, "run", fake_run)

    with TestClient(node_agent.app) as client:
        body = client.get("/metrics").json()

    assert body == {
        "node": "pi-empty",
        "temp_c": None,
        "load1": None,
        "load5": None,
        "mem_total_kb": None,
        "mem_available_kb": None,
        "disk_used_pct": None,
        "uptime_s": None,
        "nvme_temp_c": None,
        "undervoltage": None,
    }


def test_node_agent_healthz():
    from fastapi.testclient import TestClient

    with TestClient(node_agent.app) as client:
        assert client.get("/healthz").json() == {"ok": True}


# ========================= collectors.hardware =============================

def test_resolve_agents_returns_sorted_unique_ips(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(host, port, type=None):
            assert host == hardware.AGENT_SERVICE
            assert port == hardware.AGENT_PORT
            return [
                (2, 1, 6, "", ("10.0.0.6", port)),
                (2, 1, 6, "", ("10.0.0.5", port)),
                (2, 1, 6, "", ("10.0.0.5", port)),  # duplicate must be deduped
            ]

        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        assert await hardware._resolve_agents() == ["10.0.0.5", "10.0.0.6"]

    asyncio.run(scenario())


def test_resolve_agents_returns_empty_list_on_dns_failure(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_a, **_kw):
            raise socket.gaierror("name or service not known")

        monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
        assert await hardware._resolve_agents() == []

    asyncio.run(scenario())


class _FakeAgentResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_hardware_run_records_a_sample_and_computes_mem_pct(monkeypatch):
    async def scenario():
        st = ClusterState()

        async def fake_resolve():
            return ["10.0.0.5"]

        async def fake_get(_self, _url, *_a, **_kw):
            return _FakeAgentResponse(
                {
                    "node": "pi-1",
                    "temp_c": 55.5,
                    "mem_total_kb": 8_000_000,
                    "mem_available_kb": 2_000_000,
                }
            )

        monkeypatch.setattr(hardware, "_resolve_agents", fake_resolve)
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        monkeypatch.setattr(asyncio, "sleep", _raise_stop)

        with pytest.raises(_StopLoop):
            await hardware.run(st)

        recorded = st.hardware["pi-1"]
        assert recorded["temp_c"] == 55.5
        assert recorded["mem_pct"] == pytest.approx(75.0)

    asyncio.run(scenario())


def test_hardware_run_swallows_unreachable_agent_errors(monkeypatch):
    async def scenario():
        st = ClusterState()

        async def fake_resolve():
            return ["10.0.0.9"]

        async def fake_get(_self, _url, *_a, **_kw):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(hardware, "_resolve_agents", fake_resolve)
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        monkeypatch.setattr(asyncio, "sleep", _raise_stop)

        with pytest.raises(_StopLoop):
            await hardware.run(st)

        assert st.hardware == {}

    asyncio.run(scenario())


def test_hardware_run_handles_no_agents_resolved(monkeypatch):
    async def scenario():
        st = ClusterState()

        async def fake_resolve():
            return []

        monkeypatch.setattr(hardware, "_resolve_agents", fake_resolve)
        monkeypatch.setattr(asyncio, "sleep", _raise_stop)

        with pytest.raises(_StopLoop):
            await hardware.run(st)

        assert st.hardware == {}

    asyncio.run(scenario())


# ======================== collectors.healthcheck ============================
# test_backend.py's SPA test indirectly runs healthcheck.run() in DEMO mode
# (via app.main's lifespan), which already covers load_checks(demo=True),
# _run_demo() and the demo branch of _check_loop(). What's still untested is
# the *real* (non-demo) path: reading the YAML config file, _run_http(),
# _run_tcp(), the tcp/http dispatch in _check_loop(), and run()'s early
# return when there is nothing to check.

def test_load_checks_demo_mode_returns_demo_checks():
    assert healthcheck.load_checks(True) == healthcheck.DEMO_CHECKS


def test_load_checks_reads_and_parses_yaml_file(tmp_path, monkeypatch):
    cfg = tmp_path / "healthchecks.yaml"
    cfg.write_text(
        "checks:\n"
        "  - name: svc\n"
        "    type: http\n"
        "    url: http://x\n"
    )
    monkeypatch.setattr(healthcheck, "CHECKS_FILE", str(cfg))
    assert healthcheck.load_checks(False) == [
        {"name": "svc", "type": "http", "url": "http://x"}
    ]


def test_load_checks_missing_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(healthcheck, "CHECKS_FILE", str(tmp_path / "nope.yaml"))
    assert healthcheck.load_checks(False) == []


def test_load_checks_invalid_yaml_returns_empty_list(tmp_path, monkeypatch):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("checks: [unterminated")  # triggers a yaml.YAMLError
    monkeypatch.setattr(healthcheck, "CHECKS_FILE", str(cfg))
    assert healthcheck.load_checks(False) == []


async def _start_raw_http_server(status: int = 200):
    """Minimal loopback HTTP/1.1 server -- no real network access needed."""

    async def handler(reader, writer):
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        except Exception:
            pass
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def test_run_http_success_without_expected_status(monkeypatch):
    async def scenario():
        server, port = await _start_raw_http_server(200)
        try:
            async with httpx.AsyncClient() as client:
                ok, ms, detail = await healthcheck._run_http(
                    {"url": f"http://127.0.0.1:{port}/"}, client
                )
            assert ok is True
            assert detail == "HTTP 200"
            assert ms is not None
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_run_http_status_mismatch_is_not_ok(monkeypatch):
    async def scenario():
        server, port = await _start_raw_http_server(500)
        try:
            async with httpx.AsyncClient() as client:
                ok, ms, detail = await healthcheck._run_http(
                    {"url": f"http://127.0.0.1:{port}/", "expected_status": 200}, client
                )
            assert ok is False
            assert detail == "HTTP 500"
            assert ms is not None  # request succeeded, just the wrong status
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_run_http_connection_refused(monkeypatch):
    async def scenario():
        # Bind then immediately close, so the port is guaranteed to refuse.
        server, port = await _start_raw_http_server(200)
        server.close()
        await server.wait_closed()

        async with httpx.AsyncClient() as client:
            ok, ms, detail = await healthcheck._run_http(
                {"url": f"http://127.0.0.1:{port}/"}, client
            )
        assert ok is False
        assert ms is None
        assert detail  # exact exception class name varies by platform

    asyncio.run(scenario())


def test_run_http_timeout(monkeypatch):
    # A genuine network timeout needs an unroutable target, which is flaky
    # in CI sandboxes. Simulate the same code path deterministically instead.
    async def scenario():
        async def fake_get(_self, _url, *_a, **_kw):
            raise httpx.ConnectTimeout("timed out")

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        async with httpx.AsyncClient() as client:
            ok, ms, detail = await healthcheck._run_http({"url": "http://x"}, client)
        assert ok is False
        assert ms is None
        assert detail == "ConnectTimeout"

    asyncio.run(scenario())


async def _start_raw_tcp_server():
    # The handler MUST close its own writer: since Python 3.12.1,
    # Server.wait_closed() genuinely waits for every connection handler to
    # finish and its transport to close - a handler that leaves the
    # server-side writer open makes the test's `await server.wait_closed()`
    # hang forever on 3.12 (observed as a stuck CI job; 3.13+ happened to
    # reap the transport on client disconnect before wait_closed looked).
    def handler(_reader, writer):
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


def test_run_tcp_success():
    async def scenario():
        server, port = await _start_raw_tcp_server()
        try:
            ok, ms, detail = await healthcheck._run_tcp(
                {"host": "127.0.0.1", "port": port}
            )
            assert ok is True
            assert detail == "TCP open"
            assert ms is not None
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_run_tcp_connection_refused():
    async def scenario():
        server, port = await _start_raw_tcp_server()
        server.close()
        await server.wait_closed()

        ok, ms, detail = await healthcheck._run_tcp({"host": "127.0.0.1", "port": port})
        assert ok is False
        assert ms is None
        assert detail

    asyncio.run(scenario())


def test_run_tcp_timeout(monkeypatch):
    async def scenario():
        async def hanging_open_connection(*_a, **_kw):
            await asyncio.sleep(10)

        monkeypatch.setattr(asyncio, "open_connection", hanging_open_connection)
        ok, ms, detail = await healthcheck._run_tcp(
            {"host": "127.0.0.1", "port": 1, "timeout": 0.05}
        )
        assert ok is False
        assert ms is None
        assert detail == "TimeoutError"

    asyncio.run(scenario())


def test_check_loop_dispatches_tcp_checks(monkeypatch):
    async def scenario():
        server, port = await _start_raw_tcp_server()
        try:
            st = ClusterState()
            check = {
                "name": "mqtt",
                "type": "tcp",
                "host": "127.0.0.1",
                "port": port,
                "interval": 0,
            }
            monkeypatch.setattr(asyncio, "sleep", _raise_stop)
            with pytest.raises(_StopLoop):
                await healthcheck._check_loop(st, check, demo=False)
            assert st.healthchecks["mqtt"]["last"]["ok"] is True
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_check_loop_dispatches_http_checks(monkeypatch):
    async def scenario():
        server, port = await _start_raw_http_server(200)
        try:
            st = ClusterState()
            check = {
                "name": "web",
                "type": "http",
                "url": f"http://127.0.0.1:{port}/",
                "interval": 0,
            }
            monkeypatch.setattr(asyncio, "sleep", _raise_stop)
            with pytest.raises(_StopLoop):
                await healthcheck._check_loop(st, check, demo=False)
            assert st.healthchecks["web"]["last"]["ok"] is True
            assert st.healthchecks["web"]["last"]["detail"] == "HTTP 200"
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_run_returns_immediately_when_no_checks_configured(monkeypatch):
    async def scenario():
        st = ClusterState()
        st.demo_mode = False
        monkeypatch.setattr(healthcheck, "load_checks", lambda demo: [])
        # Safety net: if run() ever stopped early-returning, this would hang.
        await asyncio.wait_for(healthcheck.run(st), timeout=2)

    asyncio.run(scenario())


# =============================== app.auth ===================================

def _reload_auth(monkeypatch, password: str | None):
    if password is None:
        monkeypatch.delenv("PIWATCH_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("PIWATCH_PASSWORD", password)
    monkeypatch.delenv("PIWATCH_SECRET", raising=False)
    import app.auth as auth_mod

    return importlib.reload(auth_mod)


def test_verify_token_rejects_payload_that_is_not_a_valid_expiry(monkeypatch):
    """Valid signature, but the signed payload does not decode to an int
    expiry -- exercises verify_token()'s `except Exception: return False`.
    """
    auth = _reload_auth(monkeypatch, "pw123")
    bad_payload = base64.urlsafe_b64encode(b"not-a-number").decode().rstrip("=")
    token = f"{bad_payload}.{auth._sign(bad_payload)}"
    assert not auth.verify_token(token)


def test_login_endpoint_returns_no_token_when_auth_disabled(monkeypatch):
    auth = _reload_auth(monkeypatch, None)
    result = auth.login(auth.LoginRequest(password="whatever"))
    assert result == {"token": "", "auth": False}


def test_login_endpoint_rejects_wrong_password(monkeypatch):
    auth = _reload_auth(monkeypatch, "correct-horse")
    with pytest.raises(auth.HTTPException) as exc_info:
        auth.login(auth.LoginRequest(password="wrong"))
    assert exc_info.value.status_code == 401


def test_login_endpoint_issues_token_for_correct_password(monkeypatch):
    auth = _reload_auth(monkeypatch, "correct-horse")
    result = auth.login(auth.LoginRequest(password="correct-horse"))
    assert result["auth"] is True
    assert result["ttl"] == auth.TOKEN_TTL
    assert auth.verify_token(result["token"])


def test_auth_info_reflects_whether_a_password_is_configured(monkeypatch):
    auth_off = _reload_auth(monkeypatch, None)
    assert auth_off.auth_info() == {"auth": False}

    auth_on = _reload_auth(monkeypatch, "pw")
    assert auth_on.auth_info() == {"auth": True}


def test_require_auth_passes_silently_when_auth_disabled(monkeypatch):
    auth = _reload_auth(monkeypatch, None)
    assert auth.require_auth(authorization=None) is None


def test_require_auth_rejects_missing_header_when_enabled(monkeypatch):
    auth = _reload_auth(monkeypatch, "pw123")
    with pytest.raises(auth.HTTPException) as exc_info:
        auth.require_auth(authorization=None)
    assert exc_info.value.status_code == 401


def test_require_auth_rejects_non_bearer_scheme(monkeypatch):
    auth = _reload_auth(monkeypatch, "pw123")
    with pytest.raises(auth.HTTPException):
        auth.require_auth(authorization="Basic dXNlcjpwYXNz")


def test_require_auth_accepts_valid_bearer_token(monkeypatch):
    auth = _reload_auth(monkeypatch, "pw123")
    token = auth.create_token()
    assert auth.require_auth(authorization=f"Bearer {token}") is None


def test_ws_token_ok(monkeypatch):
    auth = _reload_auth(monkeypatch, "pw123")
    token = auth.create_token()
    assert auth.ws_token_ok(token) is True
    assert auth.ws_token_ok("garbage") is False


# =============================== app.state ===================================

def test_publish_drops_oldest_message_when_subscriber_queue_is_full():
    async def scenario():
        st = ClusterState()
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"stale": True})
        st._subscribers.add(q)

        st.publish("node", {"name": "pi-1"})

        assert q.qsize() == 1
        msg = q.get_nowait()
        assert msg["type"] == "node"
        assert msg["data"] == {"name": "pi-1"}

    asyncio.run(scenario())


def test_publish_swallows_errors_from_a_misbehaving_subscriber():
    """A subscriber whose queue can neither accept the new message nor be
    drained must not stop other subscribers from getting theirs.
    """

    class BrokenQueue:
        def put_nowait(self, _item):
            raise asyncio.QueueFull()

        def get_nowait(self):
            raise RuntimeError("nothing to drop")

    async def scenario():
        st = ClusterState()
        st._subscribers.add(BrokenQueue())
        good_q = st.subscribe()

        st.publish("node", {"name": "pi-2"})  # must not raise

        msg = good_q.get_nowait()
        assert msg["data"]["name"] == "pi-2"

    asyncio.run(scenario())


def test_record_hardware_publishes_full_sample_not_just_the_chartable_fields():
    """Regression: record_hardware() used to forward only a small whitelist
    (temp_c, disk_used_pct, ...) into the published "node_metrics" WebSocket
    delta. Fields outside that whitelist (e.g. nvme_model) then only ever
    appeared once, in the initial full_state snapshot -- any live update
    afterwards silently dropped them, freezing that data on connected
    clients. The full sample must now be forwarded (the ring-buffer history
    still only picks out a few numeric fields regardless, see
    test_node_history_ring_buffer)."""
    from app.state import ClusterState

    async def scenario():
        st = ClusterState()
        q = st.subscribe()
        st.record_hardware("pi-1", {"temp_c": 50.0, "nvme_model": "Demo SSD", "undervoltage": False})
        msg = q.get_nowait()
        assert msg["type"] == "node_metrics"
        assert msg["data"]["nvme_model"] == "Demo SSD"
        assert msg["data"]["undervoltage"] is False
        assert st.node_metrics["pi-1"]["nvme_model"] == "Demo SSD"

    asyncio.run(scenario())


def test_set_flux_kustomizations_replaces_and_publishes():
    async def scenario():
        st = ClusterState()
        q = st.subscribe()
        st.set_flux_kustomizations({"flux-system/a": {"key": "flux-system/a", "ready": True}})
        msg = q.get_nowait()
        assert msg["type"] == "flux_kustomizations"
        assert "flux-system/a" in msg["data"]
        assert "flux-system/a" in st.flux_kustomizations
        assert "flux_kustomizations" in st.snapshot()

        # a second call fully replaces the set -- deletions self-heal without
        # needing a separate remove_* method
        st.set_flux_kustomizations({"flux-system/b": {"key": "flux-system/b", "ready": False}})
        assert "flux-system/a" not in st.flux_kustomizations
        assert "flux-system/b" in st.flux_kustomizations

    asyncio.run(scenario())


def test_set_pvcs_replaces_and_publishes():
    async def scenario():
        st = ClusterState()
        q = st.subscribe()
        st.set_pvcs({"home/data": {"key": "home/data", "phase": "Bound"}})
        msg = q.get_nowait()
        assert msg["type"] == "pvcs"
        assert "home/data" in msg["data"]
        assert "home/data" in st.pvcs
        assert "pvcs" in st.snapshot()

        st.set_pvcs({"home/other": {"key": "home/other", "phase": "Bound"}})
        assert "home/data" not in st.pvcs
        assert "home/other" in st.pvcs

    asyncio.run(scenario())


def test_record_pod_sample_and_remove_pod_clears_metrics():
    async def scenario():
        st = ClusterState()
        st.record_pod_sample("ns/pod-1", {"cpu_cores": 0.1, "mem_bytes": 1000})
        assert st.pod_metrics["ns/pod-1"]["cpu_cores"] == 0.1
        assert "pod_metrics" in st.snapshot()

        st.remove_pod("ns/pod-1")
        assert "ns/pod-1" not in st.pod_metrics

    asyncio.run(scenario())


def test_remove_node_pod_deployment_update_state_and_publish():
    async def scenario():
        st = ClusterState()
        st.upsert_node("pi-1", {"name": "pi-1"})
        st.upsert_pod("ns/pod-1", {"name": "pod-1"})
        st.upsert_deployment("ns/dep-1", {"name": "dep-1"})
        q = st.subscribe()  # subscribed after the upserts above

        st.remove_node("pi-1")
        st.remove_pod("ns/pod-1")
        st.remove_deployment("ns/dep-1")
        st.remove_node("does-not-exist")  # pop(..., None): must not raise

        assert "pi-1" not in st.nodes
        assert "ns/pod-1" not in st.pods
        assert "ns/dep-1" not in st.deployments

        msgs = [q.get_nowait() for _ in range(4)]
        assert [m["type"] for m in msgs] == [
            "node_deleted",
            "pod_deleted",
            "deployment_deleted",
            "node_deleted",
        ]
        assert msgs[0]["data"] == {"name": "pi-1"}
        assert msgs[1]["data"] == {"key": "ns/pod-1"}
        assert msgs[2]["data"] == {"key": "ns/dep-1"}

    asyncio.run(scenario())


def test_upsert_and_remove_statefulset_and_daemonset_update_state_and_publish():
    async def scenario():
        st = ClusterState()
        q = st.subscribe()

        st.upsert_statefulset("ns/set-1", {"key": "ns/set-1"})
        st.upsert_daemonset("ns/ds-1", {"key": "ns/ds-1"})
        assert "ns/set-1" in st.statefulsets
        assert "ns/ds-1" in st.daemonsets
        assert "statefulsets" in st.snapshot()
        assert "daemonsets" in st.snapshot()

        st.remove_statefulset("ns/set-1")
        st.remove_daemonset("ns/ds-1")
        st.remove_statefulset("does-not-exist")  # pop(..., None): must not raise
        assert "ns/set-1" not in st.statefulsets
        assert "ns/ds-1" not in st.daemonsets

        msgs = [q.get_nowait() for _ in range(4)]
        assert [m["type"] for m in msgs] == [
            "statefulset", "daemonset", "statefulset_deleted", "daemonset_deleted",
        ]

    asyncio.run(scenario())


# ============================ collectors.demo ================================

def test_fake_logs_yields_formatted_lines_and_handles_both_msg_shapes(monkeypatch):
    async def scenario():
        # Force deterministic level/message picks: first iteration uses the
        # "%d"-templated message (exercises the formatting branch), second
        # iteration uses a plain message (exercises the pass-through branch).
        choices = iter(["INFO", "request handled in %dms", "DEBUG", "heartbeat ok"])
        monkeypatch.setattr(demo.random, "choice", lambda _seq: next(choices))
        monkeypatch.setattr(demo.random, "randint", lambda _a, _b: 77)
        monkeypatch.setattr(demo.asyncio, "sleep", _instant_sleep)

        gen = demo.fake_logs("home", "mosquitto-6b8d2")
        try:
            line1 = await gen.__anext__()
            line2 = await gen.__anext__()
        finally:
            await gen.aclose()

        assert "[mosquitto-6b8d2]" in line1
        assert "INFO" in line1
        assert "request handled in 77ms" in line1

        assert "[mosquitto-6b8d2]" in line2
        assert "DEBUG" in line2
        assert "heartbeat ok" in line2

    asyncio.run(scenario())
