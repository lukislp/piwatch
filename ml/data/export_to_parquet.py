"""Merges every raw node_samples CSV dump collected so far (see
fetch_history_db.py) into a single deduplicated Parquet file for offline
exploration/training.

Dedup matters across *repeated* fetches of the same pod over time (each new
fetch re-dumps the pod's full history, which re-includes everything already
seen before) -- verified live that it does NOT matter within a single fetch
across the two replicas: each replica samples node metrics on its own
independent asyncio loop, so their (node, t) floats essentially never collide
even though both cover every node. That's a feature, not noise -- two
independently-timed replicas effectively double the sampling density for the
same underlying process, which is why both are still fetched rather than just
one. Safe to re-run any time new dumps have been fetched; this always rebuilds
the Parquet file from scratch off everything under --raw-dir, rather than
trying to incrementally append.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

COLUMNS = [
    "node", "t", "cpu_pct", "mem_pct", "temp_c",
    "nvme_read_bytes_per_s", "nvme_write_bytes_per_s",
    "net_rx_bytes_per_s", "net_tx_bytes_per_s",
]


def load_and_merge(raw_dir: Path) -> pd.DataFrame:
    csv_paths = sorted(Path(raw_dir).glob("*.csv"))
    if not csv_paths:
        raise SystemExit(f"No CSV dumps found in {raw_dir} -- run fetch_history_db.py first")

    frames = [pd.read_csv(p) for p in csv_paths]
    merged = pd.concat(frames, ignore_index=True)
    # Two dumps of the same (node, t) reading are assumed identical (both replicas
    # observed the same real sample) -- this can't detect the rare case where they
    # genuinely differ, but that would mean a bug elsewhere (Prometheus/kubelet
    # returning different node metrics to two independent watchers), not something
    # a dedup step should try to reconcile.
    merged = merged.drop_duplicates(subset=["node", "t"]).sort_values(["node", "t"])
    merged["datetime"] = pd.to_datetime(merged["t"], unit="s", utc=True)
    return merged.reset_index(drop=True)


def summarize(df: pd.DataFrame) -> str:
    lines = [f"rows: {len(df)}"]
    for node, g in df.groupby("node"):
        span_h = (g["t"].max() - g["t"].min()) / 3600
        lines.append(f"  {node}: {len(g)} rows, {span_h:.1f}h span ({g['datetime'].min()} .. {g['datetime'].max()})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="ml/data/raw", type=Path)
    parser.add_argument("--out", default="ml/data/parquet/node_samples.parquet", type=Path)
    args = parser.parse_args()

    df = load_and_merge(args.raw_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    print(summarize(df))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
