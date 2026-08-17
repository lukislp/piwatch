"""LSTM sequence autoencoder: encodes a window of node metrics into a fixed-
size latent vector, then reconstructs the window from it. Reconstruction
error (MSE between input and output) is the anomaly score -- a window
unlike anything the model learned to reconstruct well scores high, a normal
one scores low. This is the model half of the project's "should beat the S2
baselines" comparison (see ../baselines/), specifically expected to have an
edge on the contextual/drift injection types (../data/inject_anomalies.py),
which a pointwise Z-score/EWMA baseline structurally can't catch as well.
"""
from __future__ import annotations

import torch
from torch import nn


class LSTMAutoencoder(nn.Module):
    def __init__(self, n_features: int, hidden_size: int = 32, latent_size: int = 16, num_layers: int = 1):
        super().__init__()
        self.encoder_lstm = nn.LSTM(n_features, hidden_size, num_layers=num_layers, batch_first=True)
        self.to_latent = nn.Linear(hidden_size, latent_size)
        self.from_latent = nn.Linear(latent_size, hidden_size)
        self.decoder_lstm = nn.LSTM(hidden_size, hidden_size, num_layers=num_layers, batch_first=True)
        self.output_layer = nn.Linear(hidden_size, n_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, T, F) -> reconstruction (batch, T, F)."""
        _batch_size, window_size, _ = x.shape
        _, (h_n, _) = self.encoder_lstm(x)
        latent = self.to_latent(h_n[-1])  # last layer's final hidden state -> latent vector
        # Same latent vector fed into every decoder timestep -- the decoder LSTM's own
        # recurrence is what turns a single static vector back into a sequence, not a
        # per-timestep varying input (there isn't one; that's the whole "encode down to
        # one vector, reconstruct back out" point of an autoencoder).
        decoder_input = self.from_latent(latent).unsqueeze(1).repeat(1, window_size, 1)
        decoded, _ = self.decoder_lstm(decoder_input)
        return self.output_layer(decoded)


def reconstruction_error(x: torch.Tensor, x_hat: torch.Tensor) -> torch.Tensor:
    """Per-window MSE (mean over the time and feature dims) -- one anomaly
    score per window, the same "one score per unit of input" shape the S2
    baselines use, so S4 can plug this into the same evaluate.py harness."""
    return ((x - x_hat) ** 2).mean(dim=(1, 2))
