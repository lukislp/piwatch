"""Trains the LSTM autoencoder on piwatch's exported node metrics.

Reproducible on purpose: every hyperparameter lives in TrainConfig (nothing
hardcoded inline), and the seed controls numpy/torch/random together -- the
whole point of this stage's discipline (see the project's S2 baseline
comments on "fair comparison") is that a result should be re-runnable and
attributable to the model, not to whichever random init happened to work.

Deliberately NOT a real training run against the full history yet: data
collection is still short of a meaningful window (see ml/README.md's Stages
table -- training was explicitly deferred until there's more than ~24h of
collected data). Running this now is a pipeline smoke-test -- proving the
code is mechanically correct (it runs, loss is finite, checkpoints save/load)
-- not a result worth reporting on its own. Re-run for real once there's
substantially more history.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from model.autoencoder import LSTMAutoencoder, reconstruction_error
from model.dataset import FEATURE_COLUMNS, WindowDataset, prepare_node_windows


@dataclass
class TrainConfig:
    window_size: int = 30
    stride: int = 5
    val_fraction: float = 0.2
    hidden_size: int = 32
    latent_size: int = 16
    num_layers: int = 1
    batch_size: int = 64
    epochs: int = 10
    lr: float = 1e-3
    seed: int = 42


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def train_one_node(node: str, g: pd.DataFrame, cfg: TrainConfig, out_dir: Path) -> dict:
    train_w, val_w, scaler = prepare_node_windows(g, cfg.window_size, cfg.stride, cfg.val_fraction)
    if len(train_w) == 0 or len(val_w) == 0:
        raise SystemExit(
            f"{node}: not enough rows for window_size={cfg.window_size} (got {len(g)} rows) "
            f"-- lower --window-size or collect more data first"
        )

    train_loader = DataLoader(WindowDataset(train_w), batch_size=cfg.batch_size, shuffle=True)
    val_loader = DataLoader(WindowDataset(val_w), batch_size=cfg.batch_size, shuffle=False)

    model = LSTMAutoencoder(
        n_features=len(FEATURE_COLUMNS), hidden_size=cfg.hidden_size,
        latent_size=cfg.latent_size, num_layers=cfg.num_layers,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)

    history = []
    for epoch in range(cfg.epochs):
        model.train()
        train_losses = []
        for batch in train_loader:
            optimizer.zero_grad()
            recon = model(batch)
            loss = reconstruction_error(batch, recon).mean()
            loss.backward()
            optimizer.step()
            train_losses.append(loss.item())

        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch in val_loader:
                recon = model(batch)
                val_losses.append(reconstruction_error(batch, recon).mean().item())

        train_loss = float(np.mean(train_losses))
        val_loss = float(np.mean(val_losses))
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        print(f"  [{node}] epoch {epoch + 1}/{cfg.epochs}: train_loss={train_loss:.4f} val_loss={val_loss:.4f}")

    node_dir = out_dir / node
    node_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), node_dir / "model.pt")
    (node_dir / "scaler.json").write_text(json.dumps(scaler.to_dict()))
    (node_dir / "history.json").write_text(json.dumps(history))
    return {"node": node, "final_train_loss": history[-1]["train_loss"], "final_val_loss": history[-1]["val_loss"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", default="ml/data/parquet/node_samples.parquet", type=Path)
    parser.add_argument("--out-dir", default="ml/model/checkpoints", type=Path)
    parser.add_argument("--window-size", type=int, default=TrainConfig.window_size)
    parser.add_argument("--epochs", type=int, default=TrainConfig.epochs)
    parser.add_argument("--seed", type=int, default=TrainConfig.seed)
    args = parser.parse_args()

    cfg = TrainConfig(window_size=args.window_size, epochs=args.epochs, seed=args.seed)
    set_seed(cfg.seed)

    df = pd.read_parquet(args.parquet)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "config.json").write_text(json.dumps(asdict(cfg), indent=2))

    results = []
    for node, g in df.groupby("node"):
        g = g.sort_values("t").reset_index(drop=True)
        print(f"training {node} ({len(g)} rows)...")
        results.append(train_one_node(node, g, cfg, args.out_dir))

    print("\n" + pd.DataFrame(results).to_string(index=False))


if __name__ == "__main__":
    main()
