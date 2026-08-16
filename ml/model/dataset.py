"""Windowing + normalization for training the LSTM autoencoder
(../model/autoencoder.py) on piwatch's exported node metrics.

Windows, not raw rows: the autoencoder needs to see a short sequence to have
any chance of learning temporal structure -- a contextual/drift anomaly is
only visible across several consecutive samples, not from one row alone (see
../data/inject_anomalies.py's reasoning for why those injection types exist
in the first place).

Time-based train/val split (not a random shuffle): shuffling a time series
before splitting leaks future information into training (a training window
could sit chronologically after a validation window it's supposed to be
independent of), which would make validation loss look better than the model
actually generalizes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

FEATURE_COLUMNS = ["cpu_pct", "mem_pct", "temp_c", "net_rx_bytes_per_s", "net_tx_bytes_per_s"]


@dataclass
class Scaler:
    """Per-feature mean/std, fit once on the training split and reused
    everywhere else (validation, and later S5 live serving) -- fitting
    separately per split would mean the model is evaluated (and eventually
    scores live data) against a different notion of "normal" than it was
    trained on.
    """

    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, x: np.ndarray) -> Scaler:
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std = np.where(std == 0, 1.0, std)  # a constant column would otherwise divide by zero
        return cls(mean=mean, std=std)

    def transform(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def inverse_transform(self, x: np.ndarray) -> np.ndarray:
        return x * self.std + self.mean

    def to_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> Scaler:
        return cls(mean=np.array(d["mean"]), std=np.array(d["std"]))


def build_windows(values: np.ndarray, window_size: int, stride: int = 1) -> np.ndarray:
    """(N, T, F) sliding windows over a (rows, F) array."""
    n_rows = len(values)
    if n_rows < window_size:
        return np.empty((0, window_size, values.shape[1]))
    starts = range(0, n_rows - window_size + 1, stride)
    return np.stack([values[s : s + window_size] for s in starts])


def time_based_split(df: pd.DataFrame, val_fraction: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """First (1 - val_fraction) rows (already time-sorted by the caller) as
    train, the rest as validation -- see module docstring for why not a
    random shuffle."""
    split_at = int(len(df) * (1 - val_fraction))
    return df.iloc[:split_at], df.iloc[split_at:]


def _fill_gaps(g: pd.DataFrame) -> pd.DataFrame:
    """A handful of history rows have missing values in some feature columns
    -- record_node_sample() merges samples from separate collectors (CPU/RAM
    from metrics.py, temp/network from the node-agent's hardware push), so a
    row written before the hardware side has reported yet is genuinely
    missing those fields. Even one NaN would otherwise poison Scaler.fit's
    mean/std for the whole column (turning every window's normalized value
    to NaN, not just that one row's). Forward-fill, not drop: dropping rows
    would splice together two non-consecutive samples as if they were
    adjacent, corrupting exactly the temporal structure windowing is trying
    to capture. bfill covers the rare case of a leading NaN with nothing
    before it to forward-fill from.
    """
    g = g.copy()
    g[FEATURE_COLUMNS] = g[FEATURE_COLUMNS].ffill().bfill()
    return g


def prepare_node_windows(
    g: pd.DataFrame, window_size: int, stride: int, val_fraction: float,
) -> tuple[np.ndarray, np.ndarray, Scaler]:
    """One node's time-sorted rows -> (train_windows, val_windows, scaler)."""
    g = _fill_gaps(g)
    train_df, val_df = time_based_split(g, val_fraction)
    scaler = Scaler.fit(train_df[FEATURE_COLUMNS].to_numpy())
    train_windows = build_windows(scaler.transform(train_df[FEATURE_COLUMNS].to_numpy()), window_size, stride)
    val_windows = build_windows(scaler.transform(val_df[FEATURE_COLUMNS].to_numpy()), window_size, stride)
    return train_windows, val_windows, scaler


class WindowDataset(Dataset):
    def __init__(self, windows: np.ndarray):
        self.windows = torch.as_tensor(windows, dtype=torch.float32)

    def __len__(self) -> int:
        return len(self.windows)

    def __getitem__(self, idx: int) -> torch.Tensor:
        return self.windows[idx]
