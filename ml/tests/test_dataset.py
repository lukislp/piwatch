from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.dataset import (
    FEATURE_COLUMNS,
    Scaler,
    build_windows,
    prepare_node_windows,
    time_based_split,
)


def test_scaler_fit_transform_round_trips():
    x = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])

    scaler = Scaler.fit(x)
    transformed = scaler.transform(x)

    assert np.allclose(transformed.mean(axis=0), 0.0, atol=1e-9)
    assert np.allclose(scaler.inverse_transform(transformed), x)


def test_scaler_handles_constant_column_without_dividing_by_zero():
    x = np.array([[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]])

    scaler = Scaler.fit(x)
    transformed = scaler.transform(x)

    assert np.all(np.isfinite(transformed))
    assert np.allclose(transformed[:, 0], 0.0)


def test_scaler_serialization_round_trips():
    x = np.array([[1.0, 2.0], [3.0, 4.0]])
    scaler = Scaler.fit(x)

    restored = Scaler.from_dict(scaler.to_dict())

    assert np.allclose(restored.mean, scaler.mean)
    assert np.allclose(restored.std, scaler.std)


def test_build_windows_shape_and_content():
    values = np.arange(20).reshape(10, 2).astype(float)

    windows = build_windows(values, window_size=4, stride=2)

    assert windows.shape == (4, 4, 2)  # starts at 0, 2, 4, 6 -> 4 windows
    assert np.array_equal(windows[0], values[0:4])
    assert np.array_equal(windows[1], values[2:6])


def test_build_windows_returns_empty_when_too_short():
    values = np.zeros((3, 2))

    windows = build_windows(values, window_size=5)

    assert windows.shape == (0, 5, 2)


def test_time_based_split_keeps_chronological_order():
    df = pd.DataFrame({"t": range(10)})

    train, val = time_based_split(df, val_fraction=0.3)

    assert len(train) == 7
    assert len(val) == 3
    assert train["t"].max() < val["t"].min()


def test_prepare_node_windows_fills_gaps_so_scaler_never_sees_nan():
    rng = np.random.default_rng(1)
    g = pd.DataFrame({c: rng.normal(size=100) for c in FEATURE_COLUMNS})
    g.loc[0, "temp_c"] = np.nan  # leading NaN -- only bfill can recover this one
    g.loc[50, "net_rx_bytes_per_s"] = np.nan

    train_w, val_w, scaler = prepare_node_windows(g, window_size=10, stride=5, val_fraction=0.2)

    assert np.all(np.isfinite(scaler.mean))
    assert np.all(np.isfinite(scaler.std))
    assert np.all(np.isfinite(train_w))
    assert np.all(np.isfinite(val_w))


def test_prepare_node_windows_returns_train_val_and_scaler():
    rng = np.random.default_rng(0)
    g = pd.DataFrame({c: rng.normal(size=100) for c in FEATURE_COLUMNS})

    train_w, val_w, scaler = prepare_node_windows(g, window_size=10, stride=5, val_fraction=0.2)

    assert train_w.shape[1:] == (10, len(FEATURE_COLUMNS))
    assert val_w.shape[1:] == (10, len(FEATURE_COLUMNS))
    assert len(train_w) > 0
    assert len(val_w) > 0
    assert scaler.mean.shape == (len(FEATURE_COLUMNS),)
