"""Scores a full time series with a trained autoencoder checkpoint: for each
timestep, builds the window of `window_size` samples ending at that point and
scores it via reconstruction error.

Causal/online by construction -- the window ending at t only uses data up to
and including t, matching how this would actually run in real-time serving
(the eventual S5 endpoint): "how anomalous does the most recent window look
right now", never a centered/hindsight window that would leak future data
into a live score.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .autoencoder import LSTMAutoencoder, reconstruction_error
from .dataset import FEATURE_COLUMNS, Scaler, fill_gaps


def load_checkpoint(node_dir: Path) -> tuple[LSTMAutoencoder, Scaler, dict]:
    """node_dir: e.g. ml/model/checkpoints/pinode01 -- config.json lives one
    level up (shared across every node trained in the same run, see
    train.py's main())."""
    node_dir = Path(node_dir)
    config = json.loads((node_dir.parent / "config.json").read_text())
    scaler = Scaler.from_dict(json.loads((node_dir / "scaler.json").read_text()))
    model = LSTMAutoencoder(
        n_features=len(FEATURE_COLUMNS), hidden_size=config["hidden_size"],
        latent_size=config["latent_size"], num_layers=config["num_layers"],
    )
    model.load_state_dict(torch.load(node_dir / "model.pt", weights_only=True))
    model.eval()
    return model, scaler, config


def score_series(g: pd.DataFrame, model: LSTMAutoencoder, scaler: Scaler, window_size: int) -> np.ndarray:
    """One score per row of g (already time-sorted). The first
    window_size - 1 rows have no complete preceding window and score 0
    (meaning "not enough history yet to judge", not "confirmed normal") --
    keeps the output the same length as the input for evaluate.py's per-row
    comparison, at the cost of never flagging an anomaly in a series' very
    first window_size samples.
    """
    g = fill_gaps(g)
    values = scaler.transform(g[FEATURE_COLUMNS].to_numpy())
    n = len(values)
    scores = np.zeros(n)
    if n < window_size:
        return scores

    windows = np.stack([values[i - window_size : i] for i in range(window_size, n + 1)])
    x = torch.as_tensor(windows, dtype=torch.float32)
    with torch.no_grad():
        recon = model(x)
        errors = reconstruction_error(x, recon).numpy()
    scores[window_size - 1 :] = errors
    return scores


def flag(scores: np.ndarray, threshold: float | None = None, contamination: float = 0.02) -> np.ndarray:
    """Same convention as ../baselines/isolation_forest.py: reconstruction
    error isn't on an interpretable fixed scale like z-score's "N standard
    deviations", so without an explicit threshold this falls back to a
    percentile cutoff derived from an assumed contamination rate."""
    if threshold is not None:
        return scores >= threshold
    cutoff = np.quantile(scores, 1 - contamination)
    return scores >= cutoff
