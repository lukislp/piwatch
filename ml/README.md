# piwatch ML: time-series anomaly detection (research project)

A from-scratch ML side-project built on top of piwatch's own metrics, separate from
the shipped dashboard: PyTorch model, reproducible training pipeline, and an honest
comparison against classical baselines (Z-score/EWMA/Isolation Forest) rather than
just a single "it works" result.

This directory is **not** part of the deployed app -- the production Dockerfile only
copies `backend/` and `frontend/dist`, so nothing here ever reaches the Pi cluster's
image or its resource budget. Training runs on a dev machine; only a future serving
step (see the Stages table below) would run lightweight inference against the
cluster.

## Data source

piwatch persists per-node CPU/RAM/temperature/network metrics to a local SQLite file
when `PIWATCH_HISTORY_DB` is set (see `backend/app/collectors/history.py`), pruned to
a rolling retention window (`PIWATCH_HISTORY_RETENTION_DAYS`, 7 days in prod). Both
replicas record metrics for *every* node, not just the one they happen to run on, so
either replica's file is a complete (if occasionally gappy) source on its own.

## Stages

| Stage | Status | What |
|---|---|---|
| S1: data export + exploration | in progress | `data/fetch_history_db.py`, `data/export_to_parquet.py` |
| S2: baselines (Z-score/EWMA/Isolation Forest) | not started | mandatory before the model -- see root README's ML feature discussion |
| S3: PyTorch model + training pipeline | not started | |
| S4: model vs. baseline evaluation | not started | |
| S5: serving + Home Assistant alerting + writeup | not started | |

## Usage (S1)

Requires `kubectl` on PATH and `KUBECONFIG` pointing at the target cluster (no
credentials are stored here -- point it at whatever cluster you want to pull from).

```bash
pip install -r ml/requirements.txt

# Pull a fresh snapshot from every piwatch pod (run this periodically -- each run
# adds a new timestamped dump under ml/data/raw/, nothing is overwritten)
python ml/data/fetch_history_db.py

# Merge every raw dump collected so far into one deduplicated Parquet file
python ml/data/export_to_parquet.py
```

`ml/data/raw/` and `ml/data/parquet/` are gitignored -- this is cluster telemetry,
not something to commit to a public repo.
