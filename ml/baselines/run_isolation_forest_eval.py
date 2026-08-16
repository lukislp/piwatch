"""Runs the Isolation Forest baseline with the same injected-anomaly
methodology as run_baseline_eval.py, but multivariate: anomalies are injected
independently into three core node metrics (cpu_pct, mem_pct, temp_c) and
scored jointly, showing what a method that looks at combinations of metrics
together can catch that a single-column baseline (Z-score/EWMA) can't by
construction.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from baselines import isolation_forest
from baselines.evaluate import evaluate
from data.inject_anomalies import (
    events_to_label_mask,
    inject_contextual_anomalies,
    inject_drift,
    inject_point_anomalies,
)

FEATURE_COLUMNS = ["cpu_pct", "mem_pct", "temp_c"]

INJECTIONS = [
    (inject_point_anomalies, {"n": 6, "magnitude": 4.0}),
    (inject_contextual_anomalies, {"n": 4, "duration": 12, "magnitude": 2.5}),
    (inject_drift, {"n": 2, "duration": 60, "total_shift": 3.0}),
]


def build_labeled_frame(clean: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, np.ndarray]:
    """Injects anomalies independently per feature column -- a row counts as
    anomalous in the ground truth (the label union) if ANY of its columns was
    perturbed at that index, matching "a real incident might only show up
    distinctly in one metric but the row is still something's-wrong"."""
    perturbed = clean.copy()
    mask = np.zeros(len(clean), dtype=bool)
    for column in FEATURE_COLUMNS:
        series = perturbed[column]
        for inject_fn, kwargs in INJECTIONS:
            series, events = inject_fn(series, rng=rng, **kwargs)
            mask |= events_to_label_mask(events, length=len(clean))
        perturbed[column] = series
    return perturbed, mask


def run(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for node, g in df.groupby("node"):
        g = g.sort_values("t").reset_index(drop=True)
        clean = g[FEATURE_COLUMNS]
        span_seconds = float(g["t"].max() - g["t"].min())
        perturbed, actual = build_labeled_frame(clean, rng)

        scores = isolation_forest.score(perturbed, FEATURE_COLUMNS, random_state=seed)
        predicted = isolation_forest.flag(scores)
        result = evaluate(predicted, actual, span_seconds)
        rows.append({"node": node, "method": "isolation_forest", **vars(result)})

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    results = run(df, args.seed)
    with pd.option_context("display.width", 120):
        print(results.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
