from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from baselines.evaluate import evaluate


def test_perfect_prediction_scores_1_0_everywhere():
    actual = np.array([False, True, True, False, False])
    predicted = actual.copy()

    result = evaluate(predicted, actual, span_seconds=86400)

    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.false_positives == 0


def test_false_positives_reduce_precision_and_count_toward_false_alarm_rate():
    actual = np.array([False, False, False, False])
    predicted = np.array([True, False, True, False])

    result = evaluate(predicted, actual, span_seconds=86400)  # exactly one day

    assert result.precision == 0.0
    assert result.recall == 0.0  # no actual positives at all -- recall undefined-as-zero, not NaN
    assert result.false_positives == 2
    assert result.false_alarms_per_day == pytest.approx(2.0)


def test_missed_anomaly_reduces_recall_not_precision():
    actual = np.array([True, True, False])
    predicted = np.array([True, False, False])  # caught one of two, no false alarms

    result = evaluate(predicted, actual, span_seconds=86400)

    assert result.precision == 1.0
    assert result.recall == pytest.approx(0.5)
    assert result.false_positives == 0


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        evaluate(np.array([True, False]), np.array([True]), span_seconds=86400)


def test_non_positive_span_raises():
    with pytest.raises(ValueError):
        evaluate(np.array([True]), np.array([True]), span_seconds=0)
