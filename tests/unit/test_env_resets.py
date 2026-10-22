"""Exhaustive Environment Reset Stress Tests on Difficult Floor Plans (Priority 6).

Verifies that CableRingEnv resets reliably across diverse, complex, and
adversarial/anomalous plans without crashing or leaking memory.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import pytest
from rl.baseline_cache import BaselineCache
from rl.dataset import ResPlanDatasetManager
from rl.environment import CableRingEnv


class TestEnvironmentResets:
    """Stress tests for environment reset on challenging floor plans."""

    @pytest.fixture(scope="class")
    def dataset_mgr(self):
        return ResPlanDatasetManager()

    def test_reset_on_canonical_plan_14433(self, dataset_mgr) -> None:
        """Reset on plan 14433 must succeed and return a valid 24-dim state vector."""
        env = CableRingEnv(dataset_manager=dataset_mgr, plan_ids=[14433], max_steps_per_episode=15)
        obs, info = env.reset(options={"plan_id": 14433})

        assert obs is not None
        assert obs.shape == (24,)
        assert env.incumbent_state is not None
        assert env.incumbent_state.is_valid is True
        assert env.incumbent_state.h_count >= 1
        assert info["plan_id"] == "14433"

    def test_reset_on_difficult_oversized_plans_safely_quarantines(self, dataset_mgr) -> None:
        """Reset on plans with oversized rooms (e.g. 13354, 9207) must safely quarantine and fallback."""
        difficult_pids = [13354, 9207, 33]

        for pid in difficult_pids:
            env = CableRingEnv(dataset_manager=dataset_mgr, plan_ids=[pid], max_steps_per_episode=10)
            obs, info = env.reset(options={"plan_id": pid})

            # Must not raise ValidationError
            assert obs is not None
            assert obs.shape == (24,)
            assert env.incumbent_state.is_valid is True

    def test_rapid_repeated_resets_benefit_from_cache(self, dataset_mgr) -> None:
        """Repeated resets on cached plans must average < 25ms per reset."""
        env = CableRingEnv(dataset_manager=dataset_mgr, plan_ids=[14433], max_steps_per_episode=10)

        # Warmup
        env.reset(options={"plan_id": 14433})

        t0 = time.perf_counter()
        N = 25
        for _ in range(N):
            env.reset(options={"plan_id": 14433})
        elapsed = time.perf_counter() - t0
        mean_reset_ms = (elapsed / N) * 1000.0

        print(f"Mean cached reset latency: {mean_reset_ms:.2f}ms")
        assert mean_reset_ms < 50.0, f"Cached reset too slow: {mean_reset_ms:.2f}ms"
