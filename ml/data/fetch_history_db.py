"""Pulls a consistent snapshot of piwatch's persisted node_history from every
running piwatch pod, via `kubectl exec` + a one-shot sqlite3 query dumped as CSV.

Deliberately NOT a raw `kubectl cp` of the .db file: history.py opens it with
`PRAGMA journal_mode=WAL`, so recent writes can sit in a separate -wal file that a
plain file copy would miss or catch mid-checkpoint. Querying live through sqlite3's
own connection (exactly what this script does, from inside the pod) reads a
correct, consistent view across the WAL automatically -- no need to also copy/manage
the -wal/-shm sidecar files.

Safe to run repeatedly: each run writes new timestamped files under out-dir rather
than overwriting anything, so accumulating snapshots over days/weeks (see the
project's S1 stage) just means running this on a cron/schedule and later merging
every dump with export_to_parquet.py.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

DEFAULT_NAMESPACE = "monitoring"
DEFAULT_LABEL = "app=piwatch"
CONTAINER_DB_PATH = "/data/history.db"

_DUMP_SCRIPT = (
    "import csv, sqlite3, sys\n"
    f"conn = sqlite3.connect({CONTAINER_DB_PATH!r})\n"
    "cur = conn.execute('SELECT node, t, cpu_pct, mem_pct, temp_c, "
    "nvme_read_bytes_per_s, nvme_write_bytes_per_s, net_rx_bytes_per_s, "
    "net_tx_bytes_per_s FROM node_samples ORDER BY t')\n"
    "w = csv.writer(sys.stdout)\n"
    "w.writerow([d[0] for d in cur.description])\n"
    "w.writerows(cur)\n"
)


def _pod_names(namespace: str, label: str) -> list[str]:
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-l", label, "-o", "jsonpath={.items[*].metadata.name}"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.split()


def fetch(namespace: str, label: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pods = _pod_names(namespace, label)
    if not pods:
        raise SystemExit(f"No pods found for -n {namespace} -l {label} -- check KUBECONFIG/context")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    saved = []
    for pod in pods:
        result = subprocess.run(
            ["kubectl", "exec", "-n", namespace, pod, "--", "python3", "-c", _DUMP_SCRIPT],
            check=True, capture_output=True, text=True,
        )
        dest = out_dir / f"{pod}-{stamp}.csv"
        dest.write_text(result.stdout, encoding="utf-8")
        row_count = max(result.stdout.count("\n") - 1, 0)  # minus the header row
        print(f"{pod}: {row_count} rows -> {dest}")
        saved.append(dest)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--out", default="ml/data/raw", type=Path)
    args = parser.parse_args()
    fetch(args.namespace, args.label, args.out)


if __name__ == "__main__":
    main()
