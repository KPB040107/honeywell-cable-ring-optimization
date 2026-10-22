"""Unit tests for CableRingEnv (Agent 1)."""

import pytest
import numpy as np

from rl.dataset import ResPlanDatasetManager
from rl.curriculum import CurriculumScheduler
from rl.environment import CableRingEnv
from rl.observations import OBSERVATION_DIM


@pytest.fixture(scope="module")
def dataset_mgr():
    return ResPlanDatasetManager()


def test_env_reset_and_step(dataset_mgr):
    """Test environment reset, step, observation dimensions, and validity."""
    env = CableRingEnv(
        dataset_manager=dataset_mgr,
        plan_ids=[14433],
        max_steps_per_episode=5,
        random_seed=42,
    )

    obs, info = env.reset(options={"plan_id": 14433})
    assert obs.shape == (OBSERVATION_DIM,)
    assert obs.dtype == np.float32
    assert info["plan_id"] == "14433" or info["plan_id"] == 14433
    assert info["incumbent_H"] >= 1
    assert info["incumbent_J"] > 0

    total_reward = 0.0
    for step_i in range(5):
        action = step_i % 8  # Try different operators
        obs, reward, terminated, truncated, info = env.step(action)
        assert obs.shape == (OBSERVATION_DIM,)
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert info["is_valid"] is True
        total_reward += reward

    assert terminated is True

    # Validate that incumbent result passes FloorPlanChecker
    res = env.get_incumbent_optimizer_result()
    assert res.is_valid is True
    assert res.h_count == info["incumbent_H"]
    assert abs(res.total_cost_J - info["incumbent_J"]) < 1e-3
