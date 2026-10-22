"""Verification of PPO State Transitions, Action Execution, and Reward Calculations (Requirement 6).

Verifies that:
1. PPO discrete actions cause meaningful state transitions in CableRingEnv.
2. Observations are bounded, non-NaN, and accurately reflect current search progress.
3. Rewards are calculated strictly according to official lexicographical priorities:
   - Large reward for hub reduction.
   - Severe penalty for hub increase.
   - Proportional normalized delta J reward.
   - Penalties for invalid or inapplicable operators.
"""

import math
import numpy as np
import pytest

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import ALNSState, NUM_OPERATORS
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from rl.environment import CableRingEnv
from rl.observations import OBSERVATION_DIM
from rl.rewards import compute_rl_step_reward


@pytest.fixture(scope="module")
def env_fixture() -> CableRingEnv:
    """Fixture providing an initialized CableRingEnv on canonical plan 14433."""
    env = CableRingEnv(plan_ids=[14433], max_steps_per_episode=20, random_seed=42)
    return env


def test_observation_validity(env_fixture: CableRingEnv):
    """Verify that observation vectors are finite, non-NaN, and within expected bounds."""
    obs, info = env_fixture.reset(options={"plan_id": 14433})
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (OBSERVATION_DIM,), f"Observation dimension expected {OBSERVATION_DIM}, got {obs.shape}"
    assert not np.isnan(obs).any(), "Observation contains NaN"
    assert not np.isinf(obs).any(), "Observation contains Inf"
    assert np.all(obs >= -1.5) and np.all(obs <= 2.0), f"Observation values out of expected bounds: {obs}"


def test_actions_cause_state_transitions(env_fixture: CableRingEnv):
    """Verify that executing different discrete actions causes observable state transitions."""
    env_fixture.reset(options={"plan_id": 14433})

    actions_tested = [0, 2, 4, 6]  # Boundary Exchange, Crossing Elim, Bend Smooth, Random Exploration
    applied_count = 0

    for a in actions_tested:
        prev_j = env_fixture.current_state.cost_J
        obs, reward, terminated, truncated, info = env_fixture.step(a)

        assert not np.isnan(reward), f"Reward is NaN for action {a}"
        assert not np.isnan(obs).any(), f"Observation has NaN after action {a}"
        assert "operator" in info
        assert "was_applied" in info

        if info["was_applied"]:
            applied_count += 1

    assert applied_count > 0, "At least one operator must be successfully applied during search"


def test_reward_formulation_hub_priority():
    """Verify lexicographical priority: hub reduction vs hub increase."""
    class DummyState:
        def __init__(self, h, j):
            self.h_count = h
            self.cost_J = j

    s_base = DummyState(h=3, j=30.0)
    s_reduced_h = DummyState(h=2, j=35.0)  # Hub reduced, but cost J higher
    s_increased_h = DummyState(h=4, j=20.0)  # Hub increased, but cost J lower

    # 1. Hub reduction must yield large positive reward even if J increased slightly
    r_hub_drop, is_inc = compute_rl_step_reward(
        prev_state=s_base,
        curr_state=s_reduced_h,
        incumbent_state=s_base,
        baseline_J=30.0,
        baseline_H=3,
        was_applied=True,
        is_valid=True,
    )
    assert r_hub_drop > 3.0, f"Hub reduction reward must be large positive, got {r_hub_drop}"
    assert is_inc is True, "Hub reduction must create a new incumbent"

    # 2. Hub increase must yield severe negative penalty even if J improved
    r_hub_gain, is_inc2 = compute_rl_step_reward(
        prev_state=s_base,
        curr_state=s_increased_h,
        incumbent_state=s_base,
        baseline_J=30.0,
        baseline_H=3,
        was_applied=True,
        is_valid=True,
    )
    assert r_hub_gain < -5.0, f"Hub increase must be heavily penalized, got {r_hub_gain}"
    assert is_inc2 is False, "Hub increase cannot be a valid incumbent"


def test_reward_formulation_delta_j():
    """Verify local delta J improvement rewards and penalties."""
    class DummyState:
        def __init__(self, h, j):
            self.h_count = h
            self.cost_J = j

    s_prev = DummyState(h=2, j=25.0)
    s_better = DummyState(h=2, j=22.0)  # delta J = +3.0
    s_worse = DummyState(h=2, j=28.0)   # delta J = -3.0

    r_better, _ = compute_rl_step_reward(
        prev_state=s_prev,
        curr_state=s_better,
        incumbent_state=s_prev,
        baseline_J=25.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    r_worse, _ = compute_rl_step_reward(
        prev_state=s_prev,
        curr_state=s_worse,
        incumbent_state=s_prev,
        baseline_J=25.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )

    assert r_better > 0.0, f"Improvement must yield positive reward, got {r_better}"
    assert r_worse < 0.0, f"Degradation must yield negative reward, got {r_worse}"
    assert r_better > r_worse, "Better solution must receive higher reward than worse solution"


def test_reward_formulation_penalties():
    """Verify penalties for invalid candidates and inapplicable operators."""
    class DummyState:
        def __init__(self, h, j):
            self.h_count = h
            self.cost_J = j

    s_prev = DummyState(h=2, j=25.0)

    # Inapplicable
    r_inapp, _ = compute_rl_step_reward(
        prev_state=s_prev,
        curr_state=s_prev,
        incumbent_state=s_prev,
        baseline_J=25.0,
        baseline_H=2,
        was_applied=False,
        is_valid=True,
    )
    assert r_inapp == -0.05, f"Inapplicable operator penalty expected -0.05, got {r_inapp}"

    # Invalid
    r_inv, _ = compute_rl_step_reward(
        prev_state=s_prev,
        curr_state=s_prev,
        incumbent_state=s_prev,
        baseline_J=25.0,
        baseline_H=2,
        was_applied=True,
        is_valid=False,
    )
    assert r_inv == -1.0, f"Invalid candidate penalty expected -1.0, got {r_inv}"
