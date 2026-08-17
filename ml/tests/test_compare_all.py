from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluation.compare_all import inject_all_columns
from model.dataset import FEATURE_COLUMNS


def test_inject_all_columns_returns_same_shape_and_a_nonempty_mask():
    rng = np.random.default_rng(0)
    clean = pd.DataFrame({c: rng.normal(size=500) for c in FEATURE_COLUMNS})

    perturbed, mask = inject_all_columns(clean, np.random.default_rng(1))

    assert perturbed.shape == clean.shape
    assert list(perturbed.columns) == FEATURE_COLUMNS
    assert mask.dtype == bool
    assert mask.sum() > 0
    assert mask.sum() < len(clean)  # not everything should be flagged


def test_inject_all_columns_is_reproducible_with_same_seed():
    rng_state = np.random.default_rng(0)
    clean = pd.DataFrame({c: rng_state.normal(size=200) for c in FEATURE_COLUMNS})

    perturbed_a, mask_a = inject_all_columns(clean, np.random.default_rng(42))
    perturbed_b, mask_b = inject_all_columns(clean, np.random.default_rng(42))

    pd.testing.assert_frame_equal(perturbed_a, perturbed_b)
    assert np.array_equal(mask_a, mask_b)
