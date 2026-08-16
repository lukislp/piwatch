"""Isolation Forest anomaly baseline (sklearn): unlike Z-score/EWMA (each of
which looks at one metric column in isolation), this is multivariate -- it
scores each timestep against the *joint* distribution of every feature column
together, so it can catch an anomaly that only shows up as an unusual
*combination* of metrics (e.g. CPU spiking while temperature stays flat,
something no single-column baseline could ever flag by construction).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

DEFAULT_CONTAMINATION = 0.02


def score(df: pd.DataFrame, columns: list[str], random_state: int = 0, **kwargs) -> np.ndarray:
    """Fits fresh on the given data and scores that same data (unsupervised --
    matches how zscore.py/ewma.py compute their own baseline stats straight
    from the series being scored, not a separately held-out "known normal"
    set). Higher = more anomalous; sklearn's own score_samples convention is
    the opposite, so this negates it to match the other baselines' interface.
    """
    X = df[columns].to_numpy()
    clf = IsolationForest(random_state=random_state, **kwargs)
    clf.fit(X)
    return -clf.score_samples(X)


def flag(scores: np.ndarray, threshold: float | None = None, contamination: float = DEFAULT_CONTAMINATION) -> np.ndarray:
    """threshold: an absolute score cutoff, if one's already known (e.g. from
    tuning in S4). Without one, falls back to a percentile cutoff derived
    from `contamination` -- Isolation Forest's score isn't on an
    interpretable fixed scale the way z-score's "N standard deviations" is,
    so a single fixed default threshold (like zscore.py/ewma.py use) wouldn't
    mean the same thing across different data.
    """
    if threshold is not None:
        return scores >= threshold
    cutoff = np.quantile(scores, 1 - contamination)
    return scores >= cutoff
