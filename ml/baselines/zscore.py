"""Z-score anomaly baseline: how many rolling standard deviations each point
is from its own recent trailing mean. The simplest possible baseline,
deliberately -- see the project README's S2 stage: everything else (EWMA,
Isolation Forest, the eventual PyTorch model) has to beat this to justify its
own added complexity.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def score(series: pd.Series, window: int = 60, min_periods: int = 10) -> pd.Series:
    """Absolute z-score against a trailing rolling window, not the whole
    series' mean/std -- the window shifts along with any ordinary trend in
    the data instead of flagging one as anomalous by itself.

    This is also this baseline's known, deliberate blind spot: a slow drift
    (see ../data/inject_anomalies.py's inject_drift) shifts the rolling
    window's own mean right along with it, so a trailing-window z-score
    mostly won't flag it. That's not a bug to paper over -- it's exactly the
    gap a sequence-aware model (S3) needs to actually close to earn its
    complexity, and the fair-comparison stage (S4) is where that gets
    measured, not assumed.
    """
    roll_mean = series.rolling(window, min_periods=min_periods).mean()
    roll_std = series.rolling(window, min_periods=min_periods).std()
    z = (series - roll_mean).abs() / roll_std.replace(0, np.nan)
    return z.fillna(0.0)


def flag(scores: pd.Series, threshold: float = 3.0) -> np.ndarray:
    return (scores >= threshold).to_numpy()
