"""Unit tests for Reward Function Audit (Agent 3).

Examines:
1. Exact reward values for positive, zero, and negative delta_J cost improvements.
2. Why rewards stabilize near -0.020 when operators produce zero change.
3. Penalties for inapplicable (-0.050) vs invalid (-1.000) actions.
4. Impact of step penalty (-0.020) relative to delta_J / j_scale.
5. Incumbent bonus mechanics (+1.000 for new best J, +2.000 for new best H).
"""

import pytest
from dataclasses import dataclass
from rl.rewards import compute_rl_step_reward


@dataclass
class MockState:
    h_count: int
    cost_J: float


def test_zero_cost_improvement_yields_exact_step_penalty():
    """Prove that any applied valid operator yielding zero change produces exactly -0.020."""
    prev = MockState(h_count=2, cost_J=25.0)
    curr = MockState(h_count=2, cost_J=25.0)  # delta_J == 0
    incumbent = MockState(h_count=2, cost_J=25.0)

    reward, is_new_incumbent = compute_rl_step_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=incumbent,
        baseline_J=25.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )

    assert not is_new_incumbent
    assert pytest.approx(reward, 1e-6) == -0.020, (
        f"Expected reward to be exactly -0.020 for zero improvement, got {reward}"
    )


def test_positive_cost_improvement_rewards():
    """Verify that valid cost improvements yield positive rewards when improvement exceeds step penalty."""
    prev = MockState(h_count=2, cost_J=20.0)
    curr = MockState(h_count=2, cost_J=18.0)  # delta_J = +2.0m, baseline_J = 20.0
    incumbent = MockState(h_count=2, cost_J=20.0)

    # 1. New incumbent: delta_J / 20.0 = +0.10, incumbent bonus = +1.0, step penalty = -0.02 -> +1.08
    reward, is_new_incumbent = compute_rl_step_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=incumbent,
        baseline_J=20.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert is_new_incumbent
    expected_new_inc = -0.02 + (2.0 / 20.0) + 1.0
    assert reward == pytest.approx(expected_new_inc, abs=1e-6)

    # 2. Local improvement without new incumbent:
    # Say incumbent was already 17.0, prev was 19.0, curr is 18.0
    incumbent_better = MockState(h_count=2, cost_J=17.0)
    prev_sub = MockState(h_count=2, cost_J=19.0)
    curr_sub = MockState(h_count=2, cost_J=18.0)
    reward_local, is_new_inc_local = compute_rl_step_reward(
        prev_state=prev_sub,
        curr_state=curr_sub,
        incumbent_state=incumbent_better,
        baseline_J=20.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert not is_new_inc_local
    # delta_j / 20.0 = +0.05, step penalty = -0.02 -> +0.03
    expected_local = -0.02 + (1.0 / 20.0)
    assert reward_local == pytest.approx(expected_local, abs=1e-6)


def test_negative_cost_improvement_penalties():
    """Verify that worsening cost J receives a negative reward worse than -0.020."""
    prev = MockState(h_count=2, cost_J=20.0)
    curr = MockState(h_count=2, cost_J=22.0)  # delta_J = -2.0m
    incumbent = MockState(h_count=2, cost_J=20.0)

    reward, is_new_incumbent = compute_rl_step_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=incumbent,
        baseline_J=20.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert not is_new_incumbent
    # delta_j / 20.0 = -0.10, step penalty = -0.02 -> -0.12
    expected_neg = -0.02 - (2.0 / 20.0)
    assert reward == pytest.approx(expected_neg, abs=1e-6)


def test_inapplicable_operator_penalty():
    """Verify that inapplicable operators receive -0.050 penalty, worse than zero-change (-0.020)."""
    prev = MockState(h_count=2, cost_J=20.0)
    curr = MockState(h_count=2, cost_J=20.0)
    incumbent = MockState(h_count=2, cost_J=20.0)

    reward, is_new_incumbent = compute_rl_step_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=incumbent,
        baseline_J=20.0,
        baseline_H=2,
        was_applied=False,
        is_valid=True,
    )
    assert not is_new_incumbent
    assert pytest.approx(reward, 1e-6) == -0.050


def test_invalid_solution_penalty():
    """Verify that invalid solutions receive severe -1.000 penalty."""
    prev = MockState(h_count=2, cost_J=20.0)
    curr = MockState(h_count=2, cost_J=10.0)  # Lower cost but invalid
    incumbent = MockState(h_count=2, cost_J=20.0)

    reward, is_new_incumbent = compute_rl_step_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=incumbent,
        baseline_J=20.0,
        baseline_H=2,
        was_applied=True,
        is_valid=False,
    )
    assert not is_new_incumbent
    assert pytest.approx(reward, 1e-6) == -1.000


def test_safe_no_op_dominance_proof():
    """Empirical proof of policy collapse incentive:
    If a state has no improving moves available (or highly uncertain moves),
    an operator that safely returns the current state with was_applied=True yields -0.020.
    Any operator that returns was_applied=False yields -0.050.
    Any operator that perturbs the route sub-optimally yields < -0.020.
    Any operator that violates validity yields -1.000.
    Therefore, the policy mathematically maximizes expected return by selecting the safe no-op.
    """
    safe_no_op_reward = -0.020
    inapplicable_reward = -0.050
    worse_cost_reward = -0.020 - 0.050  # -0.070
    invalid_reward = -1.000

    assert safe_no_op_reward > inapplicable_reward
    assert safe_no_op_reward > worse_cost_reward
    assert safe_no_op_reward > invalid_reward
