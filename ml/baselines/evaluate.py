"""Shared precision/recall/F1/false-alarm-rate evaluation -- one implementation
every baseline (and later the PyTorch model, S4) gets scored through, so
"model beats baseline" is an actual apples-to-apples comparison and not an
artifact of two different scoring methodologies.

False-alarm-rate is reported per day, not as a raw count, since that's the
number that matters for a real alerting use case: "how many false alerts
would this wake me up for per day". Takes the real elapsed span (from
timestamps) rather than assuming a fixed sample interval -- piwatch's actual
sampling interval turned out to be irregular in practice (median ~1.2s in the
real exported data, not the nominal 10s metrics poll -- see the S2 branch
commit for why), so an assumed-fixed-interval calculation would have been
wrong.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class EvalResult:
    precision: float
    recall: float
    f1: float
    false_alarms_per_day: float
    true_positives: int
    false_positives: int
    false_negatives: int


def evaluate(predicted: np.ndarray, actual: np.ndarray, span_seconds: float) -> EvalResult:
    """span_seconds: real elapsed wall-clock time the predicted/actual arrays
    cover, e.g. `t.max() - t.min()` from the source data -- NOT derived from
    len(predicted) times an assumed interval, since real samples aren't evenly
    spaced (see module docstring)."""
    if len(predicted) != len(actual):
        raise ValueError(f"length mismatch: predicted={len(predicted)} actual={len(actual)}")
    if span_seconds <= 0:
        raise ValueError(f"span_seconds must be positive, got {span_seconds}")

    predicted = np.asarray(predicted, dtype=bool)
    actual = np.asarray(actual, dtype=bool)

    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    span_days = span_seconds / 86400
    false_alarms_per_day = fp / span_days

    return EvalResult(precision, recall, f1, false_alarms_per_day, tp, fp, fn)
