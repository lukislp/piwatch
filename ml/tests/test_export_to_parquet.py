from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.export_to_parquet import load_and_merge

_ROW = {
    "cpu_pct": 10.0, "mem_pct": 20.0, "temp_c": 40.0,
    "nvme_read_bytes_per_s": 0, "nvme_write_bytes_per_s": 0,
    "net_rx_bytes_per_s": 0, "net_tx_bytes_per_s": 0,
}


def _write_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_load_and_merge_dedupes_rows_seen_in_an_earlier_fetch(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_csv(raw / "fetch-2026-08-15.csv", [
        {"node": "pi-1", "t": 100.0, **_ROW},
        {"node": "pi-1", "t": 110.0, **_ROW, "cpu_pct": 12.0},
    ])
    _write_csv(raw / "fetch-2026-08-16.csv", [
        # a re-fetch re-dumps the pod's whole history, so (pi-1, t=100) reappears
        # here identically -- exactly the case dedup needs to collapse
        {"node": "pi-1", "t": 100.0, **_ROW},
        {"node": "pi-2", "t": 105.0, **_ROW, "cpu_pct": 30.0},
    ])

    df = load_and_merge(raw)

    assert len(df) == 3  # the (pi-1, t=100) duplicate collapses into one row
    assert set(df["node"]) == {"pi-1", "pi-2"}
    assert list(df[df["node"] == "pi-1"]["t"]) == [100.0, 110.0]  # sorted ascending
    assert df["datetime"].dtype.kind == "M"  # a real datetime64 column, not left as unix seconds


def test_load_and_merge_raises_when_no_dumps_yet(tmp_path):
    with pytest.raises(SystemExit):
        load_and_merge(tmp_path)
