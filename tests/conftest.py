"""Pytest configuration and session fixtures for Honeywell Cable Ring Optimization."""

from __future__ import annotations

import gzip
from pathlib import Path
import shutil
import pytest

from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository


@pytest.fixture(scope="session", autouse=True)
def ensure_dataset_ready() -> None:
    """Ensure ResPlan.pkl exists before running tests (auto-generating from CI fixture if absent)."""
    root = Path(__file__).resolve().parent.parent
    target = root / "ResPlan.pkl"
    source = root / "tests" / "fixtures" / "ci_dataset.pkl.gz"

    if not target.exists() and source.exists():
        with gzip.open(source, "rb") as f_in, open(target, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        DatasetRepository.reset_instance()
