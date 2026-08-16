"""Synthetic anomaly injection for building a labeled evaluation set.

Real, confirmed incidents on a small homelab cluster are rare -- there's no way
to get a statistically meaningful precision/recall/F1 evaluation from real
anomalies alone (the standard problem with anomaly detection: the positive
class is scarce by definition). Injecting known perturbations into otherwise-
normal data with ground-truth labels is the usual workaround.

Three distinct types on purpose, not just point spikes: a baseline like
Z-score trivially catches an isolated spike (it's just "value far from the
rolling mean"), so an eval set made only of spikes would make even a naive
baseline look artificially strong. Contextual anomalies (a short plateau at an
unusual level) and drift (a slow ramp, no single sample looks wrong) are where
a sequence-aware model should actually have an edge over a pointwise baseline
-- keeping that distinction in the injected set is what makes the S4 "fair
comparison" stage meaningful instead of a foregone conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class InjectedAnomaly:
    start_idx: int
    end_idx: int  # exclusive
    kind: str  # "point" | "contextual" | "drift"
    column: str
    magnitude: float


def inject_point_anomalies(
    series: pd.Series, n: int, magnitude: float = 4.0, rng: np.random.Generator | None = None,
) -> tuple[pd.Series, list[InjectedAnomaly]]:
    """A handful of single-sample spikes, magnitude*std away from the local
    value -- the "easy" case, any reasonable baseline should catch these."""
    rng = rng if rng is not None else np.random.default_rng()
    out = series.copy()
    std = series.std() or 1.0
    events = []
    idxs = rng.choice(len(series), size=min(n, len(series)), replace=False)
    for i in idxs:
        sign = rng.choice([-1, 1])
        out.iloc[int(i)] += sign * magnitude * std
        events.append(InjectedAnomaly(int(i), int(i) + 1, "point", str(series.name), magnitude))
    return out, events


def inject_contextual_anomalies(
    series: pd.Series, n: int, duration: int = 12, magnitude: float = 2.5,
    rng: np.random.Generator | None = None,
) -> tuple[pd.Series, list[InjectedAnomaly]]:
    """A short plateau at an unusual level (e.g. CPU pinned at 70% for a few
    minutes) -- individually-plausible values that are only wrong in context,
    harder for a pointwise baseline to catch than an isolated spike."""
    rng = rng if rng is not None else np.random.default_rng()
    out = series.copy()
    std = series.std() or 1.0
    events = []
    max_start = max(len(series) - duration, 1)
    starts = rng.choice(max_start, size=min(n, max_start), replace=False)
    for s in starts:
        sign = rng.choice([-1, 1])
        end = min(int(s) + duration, len(series))
        out.iloc[int(s):end] += sign * magnitude * std
        events.append(InjectedAnomaly(int(s), end, "contextual", str(series.name), magnitude))
    return out, events


def inject_drift(
    series: pd.Series, n: int, duration: int = 60, total_shift: float = 3.0,
    rng: np.random.Generator | None = None,
) -> tuple[pd.Series, list[InjectedAnomaly]]:
    """A slow linear ramp to an unusual level over `duration` samples (e.g. a
    slow memory leak) -- no single sample looks wrong, only the trend does.
    The case a stateless, per-sample baseline (Z-score/EWMA) is weakest
    against, and where Isolation Forest/a sequence model should have the best
    shot at actually earning its complexity."""
    rng = rng if rng is not None else np.random.default_rng()
    out = series.copy()
    std = series.std() or 1.0
    events = []
    max_start = max(len(series) - duration, 1)
    starts = rng.choice(max_start, size=min(n, max_start), replace=False)
    for s in starts:
        sign = rng.choice([-1, 1])
        end = min(int(s) + duration, len(series))
        ramp = np.linspace(0, sign * total_shift * std, end - int(s))
        out.iloc[int(s):end] = out.iloc[int(s):end].to_numpy() + ramp
        events.append(InjectedAnomaly(int(s), end, "drift", str(series.name), total_shift))
    return out, events


def events_to_label_mask(events: list[InjectedAnomaly], length: int) -> np.ndarray:
    """Boolean array, True wherever any injected anomaly is active -- the
    ground truth S2/S4's precision/recall/F1 evaluation gets computed against."""
    mask = np.zeros(length, dtype=bool)
    for e in events:
        mask[e.start_idx:e.end_idx] = True
    return mask
