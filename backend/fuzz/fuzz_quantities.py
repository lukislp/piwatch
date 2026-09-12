"""Atheris fuzz harness for the Kubernetes quantity parsers in app.collectors.metrics.

Contract under test: parse_cpu() and parse_mem() either return a finite float or raise
ValueError for input they do not understand. Any other exception, a NaN/inf result or a hang
is a bug - the metrics poller only catches ValueError around these calls.

Run locally (Linux, needs the atheris wheel):
    cd backend && pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_quantities.py -max_total_time=60
CI runs the same harness for a short, fixed time budget (see .github/workflows/ci-cd.yml).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with atheris.instrument_imports():
    from app.collectors.metrics import parse_cpu, parse_mem


def _check(fn, value: str) -> None:
    try:
        result = fn(value)
    except ValueError:
        return
    if not isinstance(result, float) or not math.isfinite(result):
        raise AssertionError(f"{fn.__name__}({value!r}) returned {result!r}, expected a finite float")


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    value = fdp.ConsumeUnicodeNoSurrogates(64)
    _check(parse_cpu, value)
    _check(parse_mem, value)


if __name__ == "__main__":
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
