from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.inject_anomalies import (
    InjectedAnomaly,
    events_to_label_mask,
    inject_contextual_anomalies,
    inject_drift,
    inject_point_anomalies,
)


def _flat_series(n=200, value=10.0, noise=0.1, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(value + rng.normal(0, noise, n), name="cpu_pct")


def test_inject_point_anomalies_perturbs_exactly_n_single_samples():
    s = _flat_series()
    out, events = inject_point_anomalies(s, n=5, magnitude=5.0, rng=np.random.default_rng(1))

    assert len(events) == 5
    assert all(e.kind == "point" and e.end_idx == e.start_idx + 1 for e in events)
    assert (out != s).sum() == 5


def test_inject_contextual_anomalies_perturbs_a_plateau_not_a_single_point():
    s = _flat_series()
    out, events = inject_contextual_anomalies(s, n=2, duration=10, rng=np.random.default_rng(2))

    assert len(events) == 2
    for e in events:
        assert e.kind == "contextual"
        assert e.end_idx - e.start_idx == 10
        assert (out.iloc[e.start_idx:e.end_idx] != s.iloc[e.start_idx:e.end_idx]).all()


def test_inject_drift_ramps_gradually_not_a_step():
    s = _flat_series(n=300)
    out, events = inject_drift(s, n=1, duration=60, total_shift=4.0, rng=np.random.default_rng(3))

    e = events[0]
    delta = (out.iloc[e.start_idx:e.end_idx] - s.iloc[e.start_idx:e.end_idx]).to_numpy()
    # a true ramp: magnitude grows monotonically, not a single-step jump to full size
    assert abs(delta[0]) < abs(delta[-1])
    assert np.all(np.diff(np.abs(delta)) >= -1e-9)


def test_events_to_label_mask_marks_only_injected_ranges():
    events = [
        InjectedAnomaly(5, 8, "point", "cpu_pct", 1.0),
        InjectedAnomaly(20, 22, "contextual", "cpu_pct", 1.0),
    ]

    mask = events_to_label_mask(events, length=30)

    assert mask.sum() == 5  # (8-5) + (22-20)
    assert mask[5] and mask[7] and not mask[8]
    assert mask[20] and mask[21] and not mask[22]
