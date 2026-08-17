from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from baselines import isolation_forest


def _clean_frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "cpu_pct": 10.0 + rng.normal(0, 0.5, n),
        "mem_pct": 50.0 + rng.normal(0, 1.0, n),
        "temp_c": 40.0 + rng.normal(0, 0.3, n),
    })


def test_score_returns_one_value_per_row():
    df = _clean_frame()

    scores = isolation_forest.score(df, ["cpu_pct", "mem_pct", "temp_c"], random_state=1)

    assert len(scores) == len(df)


def test_score_ranks_a_joint_outlier_row_near_the_top():
    """A row that's individually plausible per column but never occurs
    together (CPU spiking while memory drops) is exactly the kind of
    combination only a multivariate method can see, not a per-column one."""
    df = _clean_frame(n=400, seed=2)
    outlier_idx = 200
    df.loc[outlier_idx, "cpu_pct"] = 10.0 + 3 * df["cpu_pct"].std()
    df.loc[outlier_idx, "mem_pct"] = 50.0 - 3 * df["mem_pct"].std()

    scores = isolation_forest.score(df, ["cpu_pct", "mem_pct", "temp_c"], random_state=3)

    rank = pd.Series(scores).rank(ascending=False)
    assert rank.iloc[outlier_idx] <= len(df) * 0.02  # top 2% most anomalous


def test_flag_without_threshold_falls_back_to_contamination_percentile():
    scores = np.concatenate([np.zeros(98), np.array([5.0, 6.0])])

    flags = isolation_forest.flag(scores, contamination=0.02)

    assert flags.sum() == 2
    assert flags[-1] and flags[-2]


def test_flag_with_explicit_threshold_ignores_contamination():
    scores = np.array([1.0, 2.0, 3.0, 4.0])

    flags = isolation_forest.flag(scores, threshold=2.5)

    assert list(flags) == [False, False, True, True]
