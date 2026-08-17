"""EWMA anomaly baseline: exponentially-weighted mean and variance, so more
recent points count more than a flat rolling window -- reacts faster to a
genuine level change while still smoothing out noise. See zscore.py for the
"why a baseline at all" rationale; this one differs from it only in how the
local mean/std are estimated (exponential decay vs. a flat trailing window),
so the two make a useful pair to compare against each other, not just against
the fancier methods later.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def score(series: pd.Series, span: int = 30) -> pd.Series:
    ewm_mean = series.ewm(span=span, adjust=False).mean()
    # Exponentially-weighted variance of the residual (deviation from the
    # moving mean), not of the raw series -- matches the textbook EWMA
    # control-chart construction.
    resid = series - ewm_mean
    ewm_var = (resid**2).ewm(span=span, adjust=False).mean()
    ewm_std = np.sqrt(ewm_var)
    z = resid.abs() / ewm_std.replace(0, np.nan)
    return z.fillna(0.0)


def flag(scores: pd.Series, threshold: float = 3.0) -> np.ndarray:
    return (scores >= threshold).to_numpy()
