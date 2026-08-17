from __future__ import annotations

import os
import sys

import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.autoencoder import LSTMAutoencoder, reconstruction_error


def test_forward_reconstructs_same_shape_as_input():
    model = LSTMAutoencoder(n_features=3, hidden_size=8, latent_size=4)
    x = torch.randn(5, 12, 3)  # batch=5, window=12, features=3

    out = model(x)

    assert out.shape == x.shape


def test_reconstruction_error_is_zero_for_perfect_reconstruction():
    x = torch.randn(4, 6, 2)

    error = reconstruction_error(x, x.clone())

    assert torch.allclose(error, torch.zeros(4))


def test_reconstruction_error_returns_one_nonnegative_score_per_window():
    x = torch.randn(4, 6, 2)
    x_hat = torch.randn(4, 6, 2)

    error = reconstruction_error(x, x_hat)

    assert error.shape == (4,)
    assert (error >= 0).all()
