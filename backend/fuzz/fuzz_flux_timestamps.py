"""Atheris fuzz harness for the Flux status parsers in app.collectors.flux.

Contract under test: _parse_go_duration() and _parse_iso() never raise - they return a float
for input they understand and None otherwise, because the collector feeds them raw strings
from Flux objects without any try/except of its own.

Run locally (Linux, needs the atheris wheel):
    cd backend && pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_flux_timestamps.py -max_total_time=60
"""

from __future__ import annotations

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with atheris.instrument_imports():
    from app.collectors.flux import _parse_go_duration, _parse_iso


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    value = fdp.ConsumeUnicodeNoSurrogates(64)
    for fn in (_parse_go_duration, _parse_iso):
        result = fn(value)
        if result is not None and not isinstance(result, float):
            raise AssertionError(f"{fn.__name__}({value!r}) returned {result!r}")


if __name__ == "__main__":
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()
