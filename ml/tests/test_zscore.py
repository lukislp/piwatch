from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from baselines import zscore


def test_score_is_near_zero_for_flat_noisy_series():
    rng = np.random.default_rng(0)
    s = pd.Series(10.0 + rng.normal(0, 0.5, 200))

    scores = zscore.score(s, window=30)

    assert (scores.iloc[30:] < 3.0).mean() > 0.95  # almost never flags plain noise


def test_score_spikes_at_an_injected_point_anomaly():
    rng = np.random.default_rng(1)
    s = pd.Series(10.0 + rng.normal(0, 0.3, 200))
    s.iloc[100] += 10.0  # a large, obvious spike

    scores = zscore.score(s, window=30)

    assert scores.iloc[100] > 5.0
    assert scores.iloc[100] == scores.iloc[80:120].max()


def test_flag_thresholds_the_score():
    scores = pd.Series([0.5, 2.9, 3.0, 3.1, 10.0])

    flags = zscore.flag(scores, threshold=3.0)

    assert list(flags) == [False, False, True, True, True]
