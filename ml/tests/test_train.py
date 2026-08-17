from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.dataset import FEATURE_COLUMNS
from model.train import TrainConfig, set_seed, train_one_node


def test_train_one_node_runs_and_writes_checkpoint(tmp_path):
    rng = np.random.default_rng(0)
    g = pd.DataFrame({c: rng.normal(size=300) for c in FEATURE_COLUMNS})
    cfg = TrainConfig(window_size=10, stride=5, epochs=2, hidden_size=4, latent_size=2, batch_size=8)
    set_seed(cfg.seed)

    result = train_one_node("test-node", g, cfg, tmp_path)

    assert np.isfinite(result["final_train_loss"])
    assert np.isfinite(result["final_val_loss"])
    assert (tmp_path / "test-node" / "model.pt").exists()
    assert (tmp_path / "test-node" / "scaler.json").exists()
    history = json.loads((tmp_path / "test-node" / "history.json").read_text())
    assert len(history) == cfg.epochs


def test_train_raises_when_not_enough_rows_for_window(tmp_path):
    g = pd.DataFrame({c: [0.0] * 5 for c in FEATURE_COLUMNS})
    cfg = TrainConfig(window_size=30, epochs=1)

    with pytest.raises(SystemExit):
        train_one_node("tiny-node", g, cfg, tmp_path)
