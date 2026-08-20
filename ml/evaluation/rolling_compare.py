"""S4 extension: rolling-origin evaluation across multiple held-out windows,
not just one.

compare_all.py's single validation-slice evaluation turned out to be too
noisy to draw a reliable "model beats/loses baseline" conclusion from: reruns
with progressively more collected data swung wildly (the autoencoder's F1 on
one node went 0.03 -> 0.14 -> 0.04 -> 0.13 across four separate data exports)
because a single window's result is dominated by whatever happens to be in
it -- one real network-traffic spike landing in the validation slice shifted
an entire run's numbers. Splitting the held-out region into several
independent folds and reporting mean +/- std per method answers "how
confident are we in this comparison", not just "what number did this one run
happen to produce".

The model is trained once (reuses the existing checkpoint, does NOT retrain
per fold) -- only the evaluation windows are multiplied. That's deliberate
and cheap: it targets exactly the diagnosed noise source (variance from
evaluating on a single window), not variance from training itself, without
needing K times the training cost.
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


def scaled_injections(fold_len: int, reference_len: int) -> list:
    """Keeps injected-anomaly density roughly constant regardless of fold
    size, scaled against `reference_len` (the full held-out region's length
    -- what a single-fold/K=1 run would use). K=1 exactly reproduces
    compare_all.py's original counts; smaller folds get proportionally fewer
    injected events instead of an inflated density relative to fold length.
    """
    scale = fold_len / reference_len
    return [
        (inject_point_anomalies, {"n": max(1, round(8 * scale)), "magnitude": 4.0}),
        (inject_contextual_anomalies, {"n": max(1, round(5 * scale)), "duration": 12, "magnitude": 2.5}),
        (inject_drift, {"n": max(1, round(3 * scale)), "duration": 60, "total_shift": 3.0}),
    ]


def inject_all_columns(
    clean: pd.DataFrame, injections: list, rng: np.random.Generator,
) -> tuple[pd.DataFrame, np.ndarray]:
    perturbed = clean.copy()
    mask = np.zeros(len(clean), dtype=bool)
    for column in FEATURE_COLUMNS:
        series = perturbed[column]
        for inject_fn, kwargs in injections:
            series, events = inject_fn(series, rng=rng, **kwargs)
            mask |= events_to_label_mask(events, length=len(clean))
        perturbed[column] = series
    return perturbed, mask


def evaluate_fold(
    fold_df: pd.DataFrame, fold_idx: int, node: str, model, scaler, config: dict, injections: list, seed: int,
) -> list[dict]:
    rng = np.random.default_rng(seed)
    perturbed, actual = inject_all_columns(fold_df[FEATURE_COLUMNS], injections, rng)
    span_seconds = float(fold_df["t"].max() - fold_df["t"].min())

    candidates = [
        ("zscore", zscore.flag(zscore.score(perturbed["cpu_pct"]))),
        ("ewma", ewma.flag(ewma.score(perturbed["cpu_pct"]))),
        ("isolation_forest", isolation_forest.flag(isolation_forest.score(perturbed, FEATURE_COLUMNS, random_state=seed))),
        ("autoencoder", flag_autoencoder(score_series(perturbed, model, scaler, config["window_size"]))),
    ]

    return [
        {"node": node, "fold": fold_idx, "method": name, **vars(evaluate(predicted, actual, span_seconds))}
        for name, predicted in candidates
    ]


def evaluate_node(node: str, g: pd.DataFrame, checkpoints_dir: Path, k_folds: int, seed: int) -> list[dict]:
    node_dir = checkpoints_dir / node
    if not node_dir.exists():
        print(f"skipping {node}: no checkpoint at {node_dir} -- run model/train.py first")
        return []

    model, scaler, config = load_checkpoint(node_dir)
    g = fill_gaps(g)
    _, val_df = time_based_split(g, config["val_fraction"])
    val_df = val_df.reset_index(drop=True)

    fold_bounds = np.linspace(0, len(val_df), k_folds + 1, dtype=int)
    results = []
    for i in range(k_folds):
        fold_df = val_df.iloc[fold_bounds[i]:fold_bounds[i + 1]].reset_index(drop=True)
        # Each fold needs enough rows for a complete autoencoder window plus
        # some margin for the injections (drift alone needs ~60+ rows) --
        # too-short folds are skipped rather than padded/faked.
        if len(fold_df) < config["window_size"] * 2:
            print(f"skipping {node} fold {i}: too short ({len(fold_df)} rows)")
            continue
        injections = scaled_injections(len(fold_df), len(val_df))
        results.extend(evaluate_fold(fold_df, i, node, model, scaler, config, injections, seed + i))
    return results


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    agg = df.groupby(["node", "method"]).agg(
        f1_mean=("f1", "mean"), f1_std=("f1", "std"),
        precision_mean=("precision", "mean"), recall_mean=("recall", "mean"),
        false_alarms_per_day_mean=("false_alarms_per_day", "mean"),
        folds=("fold", "count"),
    ).reset_index()
    return agg.sort_values(["node", "f1_mean"], ascending=[True, False])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--checkpoints-dir", default="ml/model/checkpoints", type=Path)
    parser.add_argument("--k-folds", default=5, type=int)
    parser.add_argument("--seed", default=42, type=int)
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    all_results = []
    for node, g in df.groupby("node"):
        g = g.sort_values("t").reset_index(drop=True)
        all_results.extend(evaluate_node(node, g, args.checkpoints_dir, args.k_folds, args.seed))

    if not all_results:
        raise SystemExit("no results -- train a model first (model/train.py)")

    detail = pd.DataFrame(all_results)
    summary = summarize(detail)
    with pd.option_context("display.width", 140):
        print(summary.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
