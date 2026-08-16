from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from baselines import ewma


def test_score_is_near_zero_for_flat_noisy_series():
    rng = np.random.default_rng(0)
    s = pd.Series(10.0 + rng.normal(0, 0.5, 200))

    scores = ewma.score(s, span=20)

    assert (scores.iloc[30:] < 3.0).mean() > 0.9

def test_score_spikes_at_an_injected_point_anomaly():
    rng = np.random.default_rng(1)
    s = pd.Series(10.0 + rng.normal(0, 0.3, 200))
    s.iloc[100] += 10.0

    scores = ewma.score(s, span=20)

    # Lower bar than zscore's equivalent test on purpose: EWMA's own mean update
    # at the spike's own index already absorbs part of it (adjust=False includes
    # the current point), so a single-sample spike registers as elevated but not
    # as sharply as a flat trailing-window baseline would score the same point.
    assert scores.iloc[100] > 3.0
    assert scores.iloc[100] == scores.iloc[80:120].max()


def test_reacts_faster_than_zscore_to_a_genuine_level_shift():
    """EWMA weights recent points more -- after a sustained step change it
    should settle (stop flagging) sooner than a flat trailing-window z-score
    would, since its own mean estimate catches up to the new level faster."""
    from baselines import zscore

    rng = np.random.default_rng(2)
    s = pd.Series(10.0 + rng.normal(0, 0.2, 300))
    s.iloc[150:] += 5.0  # a step change that becomes the new normal, not an anomaly

    ewma_scores = ewma.score(s, span=20)
    zscore_scores = zscore.score(s, window=60)

    # 40 samples after the step, EWMA has re-settled closer to "normal" than
    # the flat-window z-score, which is still dragging the pre-step mean along
    assert ewma_scores.iloc[190] < zscore_scores.iloc[190]
