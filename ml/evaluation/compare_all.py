"""S4: fair model-vs-baseline evaluation. All four methods (Z-score, EWMA,
Isolation Forest, and the trained LSTM autoencoder) are evaluated against the
SAME injected-anomaly ground truth on the SAME held-out validation slice of
each node's data -- the exact slice the autoencoder was validated on during
training (see ../model/train.py), so this isn't testing the model on data it
saw while training. That consistency is what makes "beats/loses to baseline"
a real result instead of an artifact of different methods getting easier or
harder data to work with.

Ground truth is the union across all 5 feature columns the model itself was
trained on (cpu_pct, mem_pct, temp_c, net_rx/tx_bytes_per_s) -- Z-score/EWMA
are univariate and structurally can only ever catch anomalies injected into
the one column they're scored on (cpu_pct). That's a real, expected
limitation, not something papered over by giving them an easier, cpu_pct-only
ground truth to match: the evaluation numbers below should show it up as a
visible recall gap, not hide it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from baselines import ewma, isolation_forest, zscore
from baselines.evaluate import evaluate
from data.inject_anomalies import (
    events_to_label_mask,
    inject_contextual_anomalies,
    inject_drift,
    inject_point_anomalies,
)
from model.dataset import FEATURE_COLUMNS, fill_gaps, time_based_split
from model.score import flag as flag_autoencoder
from model.score import load_checkpoint, score_series

INJECTIONS = [
    (inject_point_anomalies, {"n": 8, "magnitude": 4.0}),
    (inject_contextual_anomalies, {"n": 5, "duration": 12, "magnitude": 2.5}),
    (inject_drift, {"n": 3, "duration": 60, "total_shift": 3.0}),
]


def inject_all_columns(clean: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, np.ndarray]:
    """Injects the same point/contextual/drift mix independently into every
    feature column; a row counts as anomalous in the ground truth (the label
    union) if ANY column was perturbed there."""
    perturbed = clean.copy()
    mask = np.zeros(len(clean), dtype=bool)
    for column in FEATURE_COLUMNS:
        series = perturbed[column]
        for inject_fn, kwargs in INJECTIONS:
            series, events = inject_fn(series, rng=rng, **kwargs)
            mask |= events_to_label_mask(events, length=len(clean))
        perturbed[column] = series
    return perturbed, mask


def evaluate_node(node: str, g: pd.DataFrame, checkpoints_dir: Path, seed: int) -> list[dict]:
    node_dir = checkpoints_dir / node
    if not node_dir.exists():
        print(f"skipping {node}: no checkpoint at {node_dir} -- run model/train.py first")
        return []

    model, scaler, config = load_checkpoint(node_dir)
    g = fill_gaps(g)
    _, val_df = time_based_split(g, config["val_fraction"])
    val_df = val_df.reset_index(drop=True)

    rng = np.random.default_rng(seed)
    perturbed, actual = inject_all_columns(val_df[FEATURE_COLUMNS], rng)
    span_seconds = float(val_df["t"].max() - val_df["t"].min())

    candidates = [
        ("zscore", zscore.flag(zscore.score(perturbed["cpu_pct"]))),
        ("ewma", ewma.flag(ewma.score(perturbed["cpu_pct"]))),
        ("isolation_forest", isolation_forest.flag(isolation_forest.score(perturbed, FEATURE_COLUMNS, random_state=seed))),
        ("autoencoder", flag_autoencoder(score_series(perturbed, model, scaler, config["window_size"]))),
    ]

    return [
        {"node": node, "method": name, **vars(evaluate(predicted, actual, span_seconds))}
        for name, predicted in candidates
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--checkpoints-dir", default="ml/model/checkpoints", type=Path)
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    all_results = []
    for node, g in df.groupby("node"):
        g = g.sort_values("t").reset_index(drop=True)
        all_results.extend(evaluate_node(node, g, args.checkpoints_dir, args.seed))

    if not all_results:
        raise SystemExit("no results -- train a model first (model/train.py)")

    out = pd.DataFrame(all_results).sort_values(["node", "f1"], ascending=[True, False])
    with pd.option_context("display.width", 130):
        print(out.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
