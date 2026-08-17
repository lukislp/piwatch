"""Sanity-check exploration of the merged node_samples Parquet (see
../data/export_to_parquet.py): summary stats per node/metric, and a plot
showing what each injected anomaly type (../data/inject_anomalies.py) actually
looks like against real data. Run by hand while iterating on the project --
not part of any pipeline, no CI coverage expected.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless -- runs from a terminal, not a notebook kernel
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.inject_anomalies import (
    inject_contextual_anomalies,
    inject_drift,
    inject_point_anomalies,
)

METRIC_COLUMNS = [
    "cpu_pct", "mem_pct", "temp_c",
    "nvme_read_bytes_per_s", "nvme_write_bytes_per_s",
    "net_rx_bytes_per_s", "net_tx_bytes_per_s",
]


def summarize(df: pd.DataFrame) -> None:
    for node, g in df.groupby("node"):
        span_h = (g["t"].max() - g["t"].min()) / 3600
        print(f"\n=== {node} ({len(g)} rows, {span_h:.1f}h span) ===")
        print(g[METRIC_COLUMNS].describe().T[["mean", "std", "min", "max"]].round(2))


def plot_injection_examples(df: pd.DataFrame, node: str, column: str, out_path: Path) -> None:
    g = df[df["node"] == node].sort_values("t").reset_index(drop=True)
    clean = g[column]

    point_out, point_events = inject_point_anomalies(clean, n=3)
    ctx_out, ctx_events = inject_contextual_anomalies(clean, n=2)
    drift_out, drift_events = inject_drift(clean, n=1)

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    panels = [
        ("point", point_out, point_events),
        ("contextual", ctx_out, ctx_events),
        ("drift", drift_out, drift_events),
    ]
    for ax, (label, series, events) in zip(axes, panels, strict=True):
        ax.plot(g["datetime"], clean, color="tab:blue", alpha=0.4, label="original")
        ax.plot(g["datetime"], series, color="tab:red", alpha=0.8, label=f"with {label} anomalies")
        for e in events:
            end = min(e.end_idx, len(g) - 1)
            ax.axvspan(g["datetime"].iloc[e.start_idx], g["datetime"].iloc[end], color="red", alpha=0.15)
        ax.set_ylabel(column)
        ax.legend(loc="upper right", fontsize=8)
    axes[0].set_title(f"{node}: {column} with injected anomaly examples")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--out-dir", default="ml/exploration/figures", type=Path)
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    summarize(df)
    for node in sorted(df["node"].unique()):
        plot_injection_examples(df, node, "cpu_pct", args.out_dir / f"{node}_cpu_pct_injections.png")


if __name__ == "__main__":
    main()
