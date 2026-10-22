"""Comprehensive Unit Tests for LinUCB Bandit and ALNSLinUCBOptimizer (Subagent 2).

Tests:
1. Sherman-Morrison rank-1 update mathematical correctness vs standard matrix inversion.
2. Contextual action selection under differing feature vectors.
3. Action masking functionality and edge cases.
4. Model serialization and deserialization (round-trip for .npz and .json).
5. Lexicographical reward shaping formulation (feasibility, hub priority, symmetric delta J).
6. Condition number tracking and telemetry diagnostics.
7. Optimizer execution on canonical floor plan 14433.
8. Incumbent monotonicity guarantee (never worse than starting PortfolioHybrid baseline).
"""

from __future__ import annotations

import math
from pathlib import Path
import random
from typing import Any, Dict, List, Set

import numpy as np
import pytest

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer, get_available_operators
from resplan_router.optimizers.alns_operators import ALNSState, NUM_OPERATORS
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.linucb import LinUCBBandit, compute_linucb_reward
from rl.observations import OBSERVATION_DIM


# =========================================================================
# FIXTURES
# =========================================================================
@pytest.fixture(scope="module")
def canonical_plan_14433() -> MetricFloorPlan:
    """Load canonical benchmark floor plan 14433."""
    repo = DatasetRepository.get_instance()
    return repo.get_floor_plan("14433")


@pytest.fixture
def mock_dummy_state() -> ALNSState:
    """Create a minimal mock ALNSState for reward shaping tests."""
    return ALNSState(
        zones=[{"room_1", "room_2"}, {"room_3"}],
        bundle=None,  # type: ignore
        partition=None,  # type: ignore
        cost_J=100.0,
        h_count=2,
        area_gap=10.0,
        is_valid=True,
    )


# =========================================================================
# TEST 1: Sherman-Morrison Rank-1 Update Mathematical Correctness
# =========================================================================
def test_sherman_morrison_rank1_correctness() -> None:
    """Verify Sherman-Morrison rank-1 inverse matches standard matrix inversion."""
    d = OBSERVATION_DIM  # 24
    k = NUM_OPERATORS    # 8
    lambda_reg = 2.0
    num_updates = 30
    arm = 1

    bandit = LinUCBBandit(
        feature_dim=d,
        num_arms=k,
        alpha=0.5,
        lambda_reg=lambda_reg,
        use_sherman_morrison=True,
    )

    rng = np.random.RandomState(42)

    # Accumulators for direct calculation
    A_direct = lambda_reg * np.eye(d, dtype=np.float64)
    b_direct = np.zeros(d, dtype=np.float64)

    for step in range(num_updates):
        x = rng.randn(d).astype(np.float64)
        # Normalize context to mimic real observation vector range [-1, 1]
        x = np.clip(x / 3.0, -1.0, 1.0)
        r = float(rng.uniform(-2.0, 5.0))

        # Update bandit via Sherman-Morrison
        bandit.update(arm=arm, context=x, reward=r)

        # Update direct ground truth
        A_direct += np.outer(x, x)
        b_direct += r * x

    # Direct ground-truth inverse and parameter vector
    A_inv_direct = np.linalg.inv(A_direct)
    theta_direct = np.linalg.solve(A_direct, b_direct)

    # 1. Verify A matches
    assert np.allclose(bandit.A[arm], A_direct, atol=1e-8, rtol=1e-7), (
        "Design matrix A does not match direct accumulation"
    )

    # 2. Verify b matches
    assert np.allclose(bandit.b[arm], b_direct, atol=1e-8, rtol=1e-7), (
        "Bias vector b does not match direct accumulation"
    )

    # 3. Verify Sherman-Morrison A_inv matches np.linalg.inv
    assert np.allclose(bandit.A_inv[arm], A_inv_direct, atol=1e-5, rtol=1e-5), (
        f"Sherman-Morrison inverse differs from direct matrix inverse: max diff = "
        f"{np.max(np.abs(bandit.A_inv[arm] - A_inv_direct))}"
    )

    # 4. Verify A @ A_inv = I
    prod = np.dot(bandit.A[arm], bandit.A_inv[arm])
    assert np.allclose(prod, np.eye(d), atol=1e-5), (
        "Matrix product A @ A_inv deviates from Identity matrix"
    )

    # 5. Verify ridge parameter theta matches
    assert np.allclose(bandit.theta[arm], theta_direct, atol=1e-5, rtol=1e-5), (
        "Ridge parameters theta deviate from direct linear solve"
    )

    # 6. Verify UCB score on a fresh context
    x_test = rng.randn(d)
    pred_score, pred_mean, uncertainty = bandit.get_arm_score(arm, x_test)

    expected_mean = float(np.dot(theta_direct, x_test))
    expected_unc = math.sqrt(max(0.0, float(np.dot(x_test, np.dot(A_inv_direct, x_test)))))
    expected_score = expected_mean + 0.5 * expected_unc

    assert math.isclose(pred_score, expected_score, abs_tol=1e-5)
    assert math.isclose(pred_mean, expected_mean, abs_tol=1e-5)
    assert math.isclose(uncertainty, expected_unc, abs_tol=1e-5)


# =========================================================================
# TEST 2: Action Selection Under Differing Contexts
# =========================================================================
def test_action_selection_differing_contexts() -> None:
    """Verify LinUCB chooses different arms conditioned on context features."""
    d = OBSERVATION_DIM
    bandit = LinUCBBandit(feature_dim=d, num_arms=NUM_OPERATORS, alpha=0.1, lambda_reg=1.0)

    # Context 1: feature 0 is active (e.g. high eligible room count)
    x1 = np.zeros(d, dtype=np.float64)
    x1[0] = 1.0

    # Context 2: feature 10 is active (e.g. high bend count)
    x2 = np.zeros(d, dtype=np.float64)
    x2[10] = 1.0

    arm_A = 2
    arm_B = 4

    # Train arm_A to perform well on Context 1, poorly on Context 2
    for _ in range(20):
        bandit.update(arm=arm_A, context=x1, reward=3.0)
        bandit.update(arm=arm_A, context=x2, reward=-1.0)

    # Train arm_B to perform well on Context 2, poorly on Context 1
    for _ in range(20):
        bandit.update(arm=arm_B, context=x2, reward=3.0)
        bandit.update(arm=arm_B, context=x1, reward=-1.0)

    # On Context 1, arm_A should be preferred over arm_B
    chosen_1, scores_1 = bandit.select_arm(x1)
    assert scores_1[arm_A] > scores_1[arm_B]
    assert chosen_1 == arm_A

    # On Context 2, arm_B should be preferred over arm_A
    chosen_2, scores_2 = bandit.select_arm(x2)
    assert scores_2[arm_B] > scores_2[arm_A]
    assert chosen_2 == arm_B


# =========================================================================
# TEST 3: Action Masking
# =========================================================================
def test_action_masking() -> None:
    """Verify action masking strictly restricts selection to allowed actions."""
    bandit = LinUCBBandit(feature_dim=OBSERVATION_DIM, num_arms=NUM_OPERATORS, alpha=0.2)
    x = np.ones(OBSERVATION_DIM, dtype=np.float64) * 0.5

    # Artificially give arm 0 an extremely high reward
    for _ in range(10):
        bandit.update(arm=0, context=x, reward=10.0)

    # Without masking, arm 0 is overwhelmingly dominant
    chosen_unmasked, _ = bandit.select_arm(x)
    assert chosen_unmasked == 0

    # Mask out arm 0: available_actions = [1, 2, 3]
    chosen_masked, scores_masked = bandit.select_arm(x, available_actions=[1, 2, 3])
    assert chosen_masked in {1, 2, 3}
    assert chosen_masked != 0
    assert scores_masked[0] == float("-inf")
    assert scores_masked[4] == float("-inf")

    # Single available action
    single_chosen, single_scores = bandit.select_arm(x, available_actions=[5])
    assert single_chosen == 5
    for a in range(NUM_OPERATORS):
        if a != 5:
            assert single_scores[a] == float("-inf")

    # Error handling: empty action list
    with pytest.raises(ValueError, match="cannot be empty"):
        bandit.select_arm(x, available_actions=[])

    # Error handling: out of bounds action
    with pytest.raises(ValueError, match="outside"):
        bandit.select_arm(x, available_actions=[99])


# =========================================================================
# TEST 4: Model Save & Load Round-Trip (.npz and .json)
# =========================================================================
def test_model_persistence_npz_round_trip(tmp_path: Path) -> None:
    """Verify state preservation across save and load with .npz format."""
    npz_path = tmp_path / "linucb_model.npz"
    d = OBSERVATION_DIM
    bandit = LinUCBBandit(feature_dim=d, num_arms=NUM_OPERATORS, alpha=0.75, lambda_reg=1.5)

    # Perform some random updates
    rng = np.random.RandomState(123)
    for arm in range(NUM_OPERATORS):
        for _ in range(5):
            ctx = rng.randn(d)
            rew = float(rng.uniform(-1.0, 2.0))
            bandit.update(arm, ctx, rew)

    bandit.save(npz_path)
    assert npz_path.exists()

    # Load into fresh instance
    loaded_bandit = LinUCBBandit.load_from_file(npz_path)

    assert loaded_bandit.d == bandit.d
    assert loaded_bandit.k == bandit.k
    assert math.isclose(loaded_bandit.alpha, bandit.alpha)
    assert math.isclose(loaded_bandit.lambda_reg, bandit.lambda_reg)
    assert loaded_bandit.step_counter == bandit.step_counter
    assert np.allclose(loaded_bandit.A, bandit.A)
    assert np.allclose(loaded_bandit.A_inv, bandit.A_inv)
    assert np.allclose(loaded_bandit.b, bandit.b)
    assert np.allclose(loaded_bandit.theta, bandit.theta)
    assert np.array_equal(loaded_bandit.pull_counts, bandit.pull_counts)
    assert np.allclose(loaded_bandit.cumulative_rewards, bandit.cumulative_rewards)


def test_model_persistence_json_round_trip(tmp_path: Path) -> None:
    """Verify state preservation across save and load with .json format."""
    json_path = tmp_path / "linucb_model.json"
    d = OBSERVATION_DIM
    bandit = LinUCBBandit(feature_dim=d, num_arms=NUM_OPERATORS, alpha=0.4, lambda_reg=0.8)

    rng = np.random.RandomState(456)
    for _ in range(15):
        arm = rng.randint(0, NUM_OPERATORS)
        ctx = rng.randn(d)
        rew = float(rng.uniform(-0.5, 1.5))
        bandit.update(arm, ctx, rew)

    bandit.save(json_path)
    assert json_path.exists()

    loaded_bandit = LinUCBBandit()
    loaded_bandit.load(json_path)

    assert loaded_bandit.d == bandit.d
    assert loaded_bandit.k == bandit.k
    assert math.isclose(loaded_bandit.alpha, bandit.alpha)
    assert loaded_bandit.step_counter == bandit.step_counter
    assert np.allclose(loaded_bandit.A, bandit.A)
    assert np.allclose(loaded_bandit.A_inv, bandit.A_inv)
    assert np.allclose(loaded_bandit.b, bandit.b)
    assert np.allclose(loaded_bandit.theta, bandit.theta)


# =========================================================================
# TEST 5: Lexicographical Reward Shaping Formulation
# =========================================================================
def test_reward_shaping_properties(mock_dummy_state: ALNSState) -> None:
    """Verify reward shaping matches Honeywell requirements: feasibility, hubs, symmetric delta J."""
    prev = mock_dummy_state  # H=2, J=100.0
    incumbent = mock_dummy_state.clone()

    # 1. Invalid candidate: strict negative feedback
    r_invalid = compute_linucb_reward(
        prev_state=prev,
        curr_state=None,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=False,
        is_valid=False,
    )
    assert r_invalid == -1.0

    # 2. Inapplicable operator (valid=True, was_applied=False): small negative feedback
    r_inapp = compute_linucb_reward(
        prev_state=prev,
        curr_state=prev,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=False,
        is_valid=True,
    )
    assert r_inapp == -0.05

    # 3. Hub reduction: large positive bonus
    cand_fewer_hubs = prev.clone()
    cand_fewer_hubs.h_count = 1
    cand_fewer_hubs.cost_J = 110.0
    r_hub_red = compute_linucb_reward(
        prev_state=prev,
        curr_state=cand_fewer_hubs,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    # 5.0 * (2 - 1) + 2.0 (new incumbent H) - 0.01 = 6.99
    assert r_hub_red >= 5.0

    # 4. Hub increase: severe negative penalty
    cand_more_hubs = prev.clone()
    cand_more_hubs.h_count = 3
    cand_more_hubs.cost_J = 70.0
    r_hub_inc = compute_linucb_reward(
        prev_state=prev,
        curr_state=cand_more_hubs,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert r_hub_inc <= -10.0

    # 5. Symmetric delta J
    # Improvement: delta J = 100 - 80 = +20 -> +0.20
    cand_better_j = prev.clone()
    cand_better_j.cost_J = 80.0
    r_better = compute_linucb_reward(
        prev_state=prev,
        curr_state=cand_better_j,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )

    # Worsening: delta J = 100 - 120 = -20 -> -0.20
    cand_worse_j = prev.clone()
    cand_worse_j.cost_J = 120.0
    r_worse = compute_linucb_reward(
        prev_state=prev,
        curr_state=cand_worse_j,
        incumbent_state=incumbent,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )

    # Note: r_better also gets +1.0 for new incumbent best cost
    # So r_better = -0.01 + 0.20 + 1.0 = 1.19
    # r_worse = -0.01 - 0.20 = -0.21
    delta_better_component = r_better - (-0.01 + 1.0)  # should be +0.20
    delta_worse_component = r_worse - (-0.01)          # should be -0.20
    assert math.isclose(delta_better_component, -delta_worse_component, abs_tol=1e-5)


# =========================================================================
# TEST 6: Condition Number Tracking and Telemetry Diagnostics
# =========================================================================
def test_condition_number_and_telemetry() -> None:
    """Verify telemetry tracking captures pulls, rewards, and matrix conditioning."""
    bandit = LinUCBBandit(feature_dim=OBSERVATION_DIM, num_arms=NUM_OPERATORS, lambda_reg=1.0)

    # Initial condition numbers should be 1.0 (lambda * Identity)
    for a in range(NUM_OPERATORS):
        assert math.isclose(bandit.condition_numbers[a], 1.0, abs_tol=1e-4)

    rng = np.random.RandomState(999)
    for _ in range(40):
        a = rng.randint(0, NUM_OPERATORS)
        ctx = rng.randn(OBSERVATION_DIM)
        rew = float(rng.uniform(-1.0, 1.0))
        bandit.update(a, ctx, rew)

    telemetry = bandit.get_telemetry()
    assert telemetry["total_pulls"] == 40
    assert sum(telemetry["pull_counts"].values()) == 40
    assert len(telemetry["condition_numbers"]) == NUM_OPERATORS
    # Condition numbers must remain well-conditioned (finite and >= 1.0)
    for a, cond in telemetry["condition_numbers"].items():
        assert cond >= 1.0
        assert not math.isinf(cond) and not math.isnan(cond)


# =========================================================================
# TEST 7: Operator Masking Helper
# =========================================================================
def test_get_available_operators() -> None:
    """Verify operator applicability filter for single-zone vs multi-zone states."""
    # Single zone state
    state_single = ALNSState(
        zones=[{"room_1"}],
        bundle=None,  # type: ignore
        partition=None,  # type: ignore
        cost_J=50.0,
        h_count=1,
        area_gap=0.0,
        is_valid=True,
    )
    avail_single = get_available_operators(state_single)
    # Operators 0 (BOUNDARY_EXCHANGE) and 6 (RANDOM_EXPLORATION) and 1 (MERGE_SPLIT) should be masked
    assert 0 not in avail_single
    assert 6 not in avail_single
    assert 1 not in avail_single
    # Intra-zone operators like 2, 3, 4, 5, 7 should still be available
    assert 2 in avail_single
    assert 4 in avail_single

    # Multi-zone state
    state_multi = ALNSState(
        zones=[{"room_1", "room_2"}, {"room_3"}],
        bundle=None,  # type: ignore
        partition=None,  # type: ignore
        cost_J=80.0,
        h_count=2,
        area_gap=0.0,
        is_valid=True,
    )
    avail_multi = get_available_operators(state_multi)
    assert len(avail_multi) == NUM_OPERATORS


# =========================================================================
# TEST 8: ALNSLinUCBOptimizer Execution on Canonical Plan 14433
# =========================================================================
def test_alns_linucb_optimizer_execution(canonical_plan_14433: MetricFloorPlan) -> None:
    """Verify complete ALNSLinUCBOptimizer execution, validity audit, and telemetry."""
    opt = ALNSLinUCBOptimizer(
        max_iterations=15,
        alpha=0.5,
        cooling_rate=0.90,
    )

    res = opt.optimize(
        floor_plan=canonical_plan_14433,
        time_limit_seconds=5.0,
        random_seed=42,
    )

    # 1. Verification of standardized OptimizerResult
    assert res.is_valid is True, f"OptimizerResult is not valid: {res.violations}"
    assert res.bundle is not None
    assert res.partition is not None
    assert res.h_count >= 1
    assert res.total_cost_J > 0.0
    assert res.runtime_s > 0.0

    # 2. Independent FloorPlanChecker audit
    report = FloorPlanChecker.evaluate(canonical_plan_14433, res.bundle)
    assert report.is_valid is True, f"Independent checker failed: {report.violations}"
    assert math.isclose(report.total_cost_J, res.total_cost_J, abs_tol=1e-3)
    assert report.hub_count == res.h_count

    # 3. Diagnostic telemetry verification
    assert "diagnostic_info" in res.as_dict()
    diag = res.diagnostic_info
    assert "iterations" in diag
    assert diag["iterations"] > 0
    assert "operator_calls" in diag
    assert "bandit_telemetry" in diag
    assert diag["bandit_telemetry"]["total_pulls"] > 0


# =========================================================================
# TEST 9: Incumbent Monotonicity Guarantee (Never Worse than PortfolioHybrid)
# =========================================================================
def test_alns_linucb_never_worse_than_portfolio_hybrid(canonical_plan_14433: MetricFloorPlan) -> None:
    """Verify ALNSLinUCBOptimizer strictly maintains or improves baseline solution."""
    base_opt = PortfolioHybridOptimizer()
    base_res = base_opt.optimize(canonical_plan_14433, time_limit_seconds=2.0, random_seed=42)
    assert base_res.is_valid is True

    linucb_opt = ALNSLinUCBOptimizer(
        max_iterations=15,
        alpha=0.5,
        base_opt=base_opt,
    )
    linucb_res = linucb_opt.optimize(canonical_plan_14433, time_limit_seconds=5.0, random_seed=42)

    assert linucb_res.is_valid is True

    # Primary check: Hub count must not increase
    assert linucb_res.h_count <= base_res.h_count, (
        f"Hub count regressed: linucb={linucb_res.h_count} > baseline={base_res.h_count}"
    )

    # Secondary check: If hub count is equal, J cost must not regress
    if linucb_res.h_count == base_res.h_count:
        assert linucb_res.total_cost_J <= base_res.total_cost_J + 1e-4, (
            f"J cost regressed: linucb={linucb_res.total_cost_J:.4f} > baseline={base_res.total_cost_J:.4f}"
        )
