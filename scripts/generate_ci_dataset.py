#!/usr/bin/env python3
"""Generates a lightweight ResPlan.pkl from tests/fixtures/ci_dataset.pkl.gz for CI and headless testing."""

import gzip
from pathlib import Path
import shutil
import sys

def main() -> int:
    root = Path(__file__).resolve().parent.parent
    target = root / "ResPlan.pkl"
    source = root / "tests" / "fixtures" / "ci_dataset.pkl.gz"

    if target.exists():
        print(f"ResPlan.pkl already exists at {target} ({target.stat().st_size:,} bytes). Skipping generation.")
        return 0

    if not source.exists():
        print(f"Error: Fixture source not found at {source}", file=sys.stderr)
        return 1

    print(f"Decompressing {source} -> {target}...")
    with gzip.open(source, "rb") as f_in, open(target, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    print(f"Generated {target} successfully ({target.stat().st_size:,} bytes).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
