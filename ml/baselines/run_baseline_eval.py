"""Runs every S2 baseline (Z-score, EWMA) against the same injected-anomaly
labeled set and prints a precision/recall/F1/false-alarm-rate table per node
-- the "Messlatte" every later, more complex method (Isolation Forest, the
PyTorch model) has to clear.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from baselines import ewma, zscore
from baselines.evaluate import evaluate
from data.inject_anomalies import (
    events_to_label_mask,
    inject_contextual_anomalies,
    inject_drift,
    inject_point_anomalies,
)

METHODS = {"zscore": zscore, "ewma": ewma}

# One consistent injected-anomaly mix for every node/run: enough of each type
# (point/contextual/drift, see inject_anomalies.py) that no single kind
# dominates the eval set, otherwise the result mostly just measures how well
# a method handles whichever type happens to be most common.
INJECTIONS = [
    (inject_point_anomalies, {"n": 8, "magnitude": 4.0}),
    (inject_contextual_anomalies, {"n": 5, "duration": 12, "magnitude": 2.5}),
    (inject_drift, {"n": 3, "duration": 60, "total_shift": 3.0}),
]


def build_labeled_series(clean: pd.Series, rng: np.random.Generator) -> tuple[pd.Series, np.ndarray]:
    perturbed = clean.copy()
    events = []
    for inject_fn, kwargs in INJECTIONS:
        perturbed, new_events = inject_fn(perturbed, rng=rng, **kwargs)
        events.extend(new_events)
    mask = events_to_label_mask(events, length=len(clean))
    return perturbed, mask


def run(df: pd.DataFrame, column: str, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for node, g in df.groupby("node"):
        g = g.sort_values("t").reset_index(drop=True)
        clean = g[column]
        span_seconds = float(g["t"].max() - g["t"].min())
        perturbed, actual = build_labeled_series(clean, rng)

        for name, module in METHODS.items():
            scores = module.score(perturbed)
            predicted = module.flag(scores)
            result = evaluate(predicted, actual, span_seconds)
            rows.append({"node": node, "method": name, **vars(result)})

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--column", default="cpu_pct")
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    results = run(df, args.column, args.seed)
    with pd.option_context("display.width", 120):
        print(results.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
