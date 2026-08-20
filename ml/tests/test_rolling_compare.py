from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import mlflow
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation.rolling_compare import evaluate_node, scaled_injections, summarize
from model.dataset import FEATURE_COLUMNS
from model.train import TrainConfig, set_seed, train_one_node


def test_scaled_injections_reproduces_originals_at_full_scale():
    injections = scaled_injections(fold_len=1000, reference_len=1000)

    counts = {fn.__name__: kwargs["n"] for fn, kwargs in injections}

    assert counts["inject_point_anomalies"] == 8
    assert counts["inject_contextual_anomalies"] == 5
    assert counts["inject_drift"] == 3


def test_scaled_injections_scales_down_proportionally_never_to_zero():
    injections = scaled_injections(fold_len=200, reference_len=1000)  # 1/5th

    counts = {fn.__name__: kwargs["n"] for fn, kwargs in injections}

    assert counts["inject_point_anomalies"] == max(1, round(8 * 0.2))
    assert all(n >= 1 for n in counts.values())


@pytest.fixture
def trained_checkpoint(tmp_path):
    """Trains a tiny model once and returns (checkpoints_dir, full_series) --
    rolling_compare.py never retrains, so every test reuses this one
    checkpoint against different fold counts."""
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlruns' / 'mlflow.db'}")
    (tmp_path / "mlruns").mkdir(parents=True, exist_ok=True)
    mlflow.set_experiment("test")

    rng = np.random.default_rng(0)
    g = pd.DataFrame({c: rng.normal(size=1000) for c in FEATURE_COLUMNS})
    g["t"] = np.arange(1000, dtype=float) * 10.0  # evaluate_fold needs real timestamps for span_seconds
    cfg = TrainConfig(window_size=10, stride=5, epochs=1, hidden_size=4, latent_size=2, batch_size=8, val_fraction=0.4)
    set_seed(cfg.seed)

    out_dir = tmp_path / "checkpoints"
    out_dir.mkdir()
    (out_dir / "config.json").write_text(json.dumps(asdict(cfg)))
    train_one_node("test-node", g, cfg, out_dir)
    return out_dir, g


def test_evaluate_node_covers_all_four_methods_across_folds(trained_checkpoint):
    checkpoints_dir, g = trained_checkpoint

    results = evaluate_node("test-node", g, checkpoints_dir, k_folds=4, seed=1)
    df = pd.DataFrame(results)

    assert set(df["method"]) == {"zscore", "ewma", "isolation_forest", "autoencoder"}
    assert 1 <= df["fold"].nunique() <= 4  # short folds may be skipped, never zero


def test_evaluate_node_reuses_one_checkpoint_not_retrained_per_fold(trained_checkpoint):
    checkpoints_dir, g = trained_checkpoint
    mtime_before = (checkpoints_dir / "test-node" / "model.pt").stat().st_mtime

    evaluate_node("test-node", g, checkpoints_dir, k_folds=3, seed=2)

    mtime_after = (checkpoints_dir / "test-node" / "model.pt").stat().st_mtime
    assert mtime_before == mtime_after


def test_summarize_returns_mean_std_and_fold_count_per_method(trained_checkpoint):
    checkpoints_dir, g = trained_checkpoint
    results = evaluate_node("test-node", g, checkpoints_dir, k_folds=3, seed=3)

    summary = summarize(pd.DataFrame(results))

    assert {"f1_mean", "f1_std", "folds"}.issubset(summary.columns)
    assert (summary["folds"] >= 1).all()
    assert len(summary) == 4  # one row per method
