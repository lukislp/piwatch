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

from model.dataset import FEATURE_COLUMNS
from model.score import flag, load_checkpoint, score_series
from model.train import TrainConfig, set_seed, train_one_node


@pytest.fixture
def trained_checkpoint(tmp_path):
    """Trains a tiny model for a few epochs and returns its checkpoint dir --
    mirrors test_train.py's smoke-test setup, reused here since score.py
    needs a real checkpoint on disk to load, not just an in-memory model."""
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlruns' / 'mlflow.db'}")
    (tmp_path / "mlruns").mkdir(parents=True, exist_ok=True)
    mlflow.set_experiment("test")

    rng = np.random.default_rng(0)
    g = pd.DataFrame({c: rng.normal(size=300) for c in FEATURE_COLUMNS})
    cfg = TrainConfig(window_size=10, stride=5, epochs=2, hidden_size=4, latent_size=2, batch_size=8)
    set_seed(cfg.seed)

    out_dir = tmp_path / "checkpoints"
    out_dir.mkdir()
    (out_dir / "config.json").write_text(json.dumps(asdict(cfg)))
    train_one_node("test-node", g, cfg, out_dir)
    return out_dir / "test-node", cfg


def test_load_checkpoint_restores_a_working_model(trained_checkpoint):
    node_dir, cfg = trained_checkpoint

    model, scaler, config = load_checkpoint(node_dir)

    assert config["window_size"] == cfg.window_size
    assert scaler.mean.shape == (len(FEATURE_COLUMNS),)
    # a restored model must actually run, not just deserialize
    import torch

    x = torch.randn(1, cfg.window_size, len(FEATURE_COLUMNS))
    out = model(x)
    assert out.shape == x.shape


def test_score_series_scores_every_row_and_zeros_the_warmup_period(trained_checkpoint):
    node_dir, cfg = trained_checkpoint
    model, scaler, _ = load_checkpoint(node_dir)
    rng = np.random.default_rng(1)
    g = pd.DataFrame({c: rng.normal(size=50) for c in FEATURE_COLUMNS})

    scores = score_series(g, model, scaler, cfg.window_size)

    assert len(scores) == len(g)
    assert np.all(scores[: cfg.window_size - 1] == 0.0)  # no complete window yet
    assert np.all(np.isfinite(scores))


def test_score_series_returns_all_zeros_when_shorter_than_window(trained_checkpoint):
    node_dir, cfg = trained_checkpoint
    model, scaler, _ = load_checkpoint(node_dir)
    g = pd.DataFrame({c: [0.0] * 3 for c in FEATURE_COLUMNS})

    scores = score_series(g, model, scaler, cfg.window_size)

    assert len(scores) == 3
    assert np.all(scores == 0.0)


def test_flag_without_threshold_uses_contamination_percentile():
    scores = np.concatenate([np.zeros(98), np.array([5.0, 6.0])])

    flags = flag(scores, contamination=0.02)

    assert flags.sum() == 2


def test_flag_with_explicit_threshold():
    scores = np.array([1.0, 2.0, 3.0])

    flags = flag(scores, threshold=2.5)

    assert list(flags) == [False, False, True]
