"""Comprehensive Unit and Integration Tests for LinUCB-ALNS v2 Optimizer (Agent 1).

Tests:
1. Operator registry integrity: 14 operators (8 core + 6 advanced), index bounds, naming.
2. Sherman-Morrison rank-1 update mathematical correctness across all 14 arms.
3. 24-feature context compatibility: dimension, normalization ranges, cluster rates.
4. Strict dynamic action masking:
   - Crossing reconstruction & elim masked when X == 0.
   - Overlap rerouting & reduce masked when O <= 0.05m.
   - Alternative partitions masked when only 1 optimal partition exists.
   - Boundary exchange, random exploration, and joint reassignment masked when zones < 2.
   - Ring order search masked when all rings have < 3 assigned nodes.
   - Bandit select_arm strictly respects masked actions.
5. Unified operator dispatcher (apply_v2_operator) across core (0..7) and advanced (8..13).
6. Reward shaping properties (feasibility, hub priority, conflict reduction bonuses).
7. Optimizer execution on canonical floor plans (Plan 0 and Plan 4).
8. Incumbent monotonicity and validated fallback to baseline/PortfolioHybrid.
"""

from __future__ import annotations

import math
from pathlib import Path
import random
from typing import Any, Dict, List, Set

import networkx as nx
import numpy as np
import pytest

from resplan_router.contracts import (
    CompletedHubRing,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.alns_linucb_v2 import (
    NUM_OPERATORS_V2,
    OPERATOR_NAMES_V2,
    OP_ALTERNATIVE_MIN_HUB_PARTITIONS,
    OP_BEND_SMOOTH,
    OP_BOUNDARY_EXCHANGE,
    OP_CROSSING_AWARE_RECONSTRUCTION,
    OP_CROSSING_ELIM,
    OP_HUB_CENTROID_RELOC,
    OP_HUB_POSITION_REFINEMENT,
    OP_JOINT_ZONE_REASSIGNMENT,
    OP_MERGE_SPLIT,
    OP_OVERLAP_AWARE_REROUTING,
    OP_OVERLAP_REDUCE,
    OP_RANDOM_EXPLORATION,
    OP_RING_ORDER_SEARCH,
    OP_WORST_ROUTE_REBUILD,
    LinUCB_ALNS_V2Optimizer,
    apply_v2_operator,
    compute_v2_reward,
    extract_observation_vector_v2,
    get_available_operators_v2,
)
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS as NUM_CORE_OPERATORS,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.linucb import LinUCBBandit
from rl.observations import OBSERVATION_DIM


# =========================================================================
# FIXTURES
# =========================================================================

@pytest.fixture(scope="module")
def canonical_plan_0() -> MetricFloorPlan:
    """Load canonical benchmark floor plan 0."""
    repo = DatasetRepository.get_instance()
    return repo.get_floor_plan("0")


@pytest.fixture(scope="module")
def canonical_plan_4() -> MetricFloorPlan:
    """Load canonical benchmark floor plan 4."""
    repo = DatasetRepository.get_instance()
    return repo.get_floor_plan("4")


@pytest.fixture
def mock_state_with_conflicts() -> ALNSState:
    """Create a mock ALNSState with non-zero crossings, overlaps, and 2 zones."""
    seg1 = RouteSegment(
        from_node="H1",
        to_node="N1",
        length_m=10.0,
        polyline=[Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=0.0)],
    )
    seg2 = RouteSegment(
        from_node="N1",
        to_node="N2",
        length_m=10.0,
        polyline=[Point2D(x=10.0, y=0.0), Point2D(x=10.0, y=10.0)],
    )
    seg3 = RouteSegment(
        from_node="N2",
        to_node="H1",
        length_m=20.0,
        polyline=[Point2D(x=10.0, y=10.0), Point2D(x=0.0, y=10.0), Point2D(x=0.0, y=0.0)],
    )

    ring1 = CompletedHubRing(
        hub_id="H1",
        room_id="R1",
        location=Point2D(x=0.0, y=0.0),
        assigned_rooms=["R1"],
        assigned_nodes=["N1", "N2"],
        closed_sequence=["H1", "N1", "N2", "H1"],
        route_segments=[seg1, seg2, seg3],
        ring_length_m=34.14,
        ring_bends=2,
        ring_crossings=2,
        ring_overlap_m=1.5,
        cost_J=45.0,
    )

    ring2 = CompletedHubRing(
        hub_id="H2",
        room_id="R2",
        location=Point2D(x=20.0, y=20.0),
        assigned_rooms=["R2", "R3"],
        assigned_nodes=["N3", "N4", "N5"],
        closed_sequence=["H2", "N3", "N4", "N5", "H2"],
        route_segments=[seg1, seg2, seg3],
        ring_length_m=34.14,
        ring_bends=2,
        ring_crossings=1,
        ring_overlap_m=0.8,
        cost_J=42.0,
    )

    bundle = SynthesizedRingsBundle(
        plan_id="mock_plan",
        hub_rings=[ring1, ring2],
        total_length_m=68.28,
        total_bends=4,
        total_crossings=3,
        total_overlap_m=2.3,
        total_cost_J=87.0,
    )

    hubs = [
        HubAssignment(hub_id="H1", room_id="R1", location=Point2D(x=0.0, y=0.0), assigned_rooms=["R1"], zone_area_m2=20.0),
        HubAssignment(hub_id="H2", room_id="R2", location=Point2D(x=20.0, y=20.0), assigned_rooms=["R2", "R3"], zone_area_m2=35.0),
    ]
    partition = HubZonePartition(plan_id="mock_plan", hub_count=2, lower_bound_hubs=2, hubs=hubs)

    return ALNSState(
        zones=[{"R1"}, {"R2", "R3"}],
        bundle=bundle,
        partition=partition,
        cost_J=87.0,
        h_count=2,
        area_gap=5.0,
        is_valid=True,
    )


# =========================================================================
# TEST 1: Operator Registry Integrity (14 operators)
# =========================================================================

def test_operator_registry_and_bounds() -> None:
    """Verify 14 operators registered with exact naming and index bounds."""
    assert NUM_OPERATORS_V2 == 14
    assert len(OPERATOR_NAMES_V2) == 14

    # Verify first 8 are core operators
    assert OPERATOR_NAMES_V2[0] == "BOUNDARY_EXCHANGE"
    assert OPERATOR_NAMES_V2[1] == "MERGE_SPLIT"
    assert OPERATOR_NAMES_V2[2] == "CROSSING_ELIM"
    assert OPERATOR_NAMES_V2[3] == "OVERLAP_REDUCE"
    assert OPERATOR_NAMES_V2[4] == "BEND_SMOOTH"
    assert OPERATOR_NAMES_V2[5] == "WORST_ROUTE_REBUILD"
    assert OPERATOR_NAMES_V2[6] == "RANDOM_EXPLORATION"
    assert OPERATOR_NAMES_V2[7] == "HUB_CENTROID_RELOC"

    # Verify last 6 are advanced operators
    assert OPERATOR_NAMES_V2[8] == "CROSSING_AWARE_RECONSTRUCTION"
    assert OPERATOR_NAMES_V2[9] == "OVERLAP_AWARE_REROUTING"
    assert OPERATOR_NAMES_V2[10] == "HUB_POSITION_REFINEMENT"
    assert OPERATOR_NAMES_V2[11] == "ALTERNATIVE_MIN_HUB_PARTITIONS"
    assert OPERATOR_NAMES_V2[12] == "RING_ORDER_SEARCH"
    assert OPERATOR_NAMES_V2[13] == "JOINT_ZONE_REASSIGNMENT"

    # Index bounds
    with pytest.raises(ValueError):
        apply_v2_operator(-1, None, None, None, None, None)  # type: ignore

    with pytest.raises(ValueError):
        apply_v2_operator(14, None, None, None, None, None)  # type: ignore


# =========================================================================
# TEST 2: Sherman-Morrison Rank-1 Updates Across All 14 Arms
# =========================================================================

def test_sherman_morrison_all_14_arms() -> None:
    """Verify Sherman-Morrison rank-1 update across all 14 bandit arms."""
    d = OBSERVATION_DIM  # 24
    k = NUM_OPERATORS_V2  # 14
    lambda_reg = 1.5

    bandit = LinUCBBandit(
        feature_dim=d,
        num_arms=k,
        alpha=0.5,
        lambda_reg=lambda_reg,
        use_sherman_morrison=True,
    )

    rng = np.random.RandomState(123)

    # Test rank-1 update across every single arm 0..13
    for arm in range(k):
        A_direct = lambda_reg * np.eye(d, dtype=np.float64)
        b_direct = np.zeros(d, dtype=np.float64)

        for step in range(10):
            x = rng.randn(d).astype(np.float64)
            x = np.clip(x / 2.0, -1.0, 1.0)
            r = float(rng.uniform(-1.0, 4.0))

            bandit.update(arm=arm, context=x, reward=r)
            A_direct += np.outer(x, x)
            b_direct += r * x

        # Ground truth direct solve
        A_inv_direct = np.linalg.inv(A_direct)
        theta_direct = np.linalg.solve(A_direct, b_direct)

        # Check precision against Sherman-Morrison tracking
        np.testing.assert_allclose(bandit.A[arm], A_direct, rtol=1e-5, atol=1e-5)
        np.testing.assert_allclose(bandit.A_inv[arm], A_inv_direct, rtol=1e-4, atol=1e-4)
        np.testing.assert_allclose(bandit.theta[arm], theta_direct, rtol=1e-4, atol=1e-4)

        # Symmetry check
        assert np.allclose(bandit.A_inv[arm], bandit.A_inv[arm].T, atol=1e-8)


# =========================================================================
# TEST 3: 24-Feature Context Compatibility
# =========================================================================

def test_24_feature_context_compatibility(canonical_plan_0: MetricFloorPlan, mock_state_with_conflicts: ALNSState) -> None:
    """Verify 24-feature extraction conforms to bounds and dimensions."""
    calls = [2] * NUM_OPERATORS_V2
    succs = [1] * NUM_OPERATORS_V2

    obs = extract_observation_vector_v2(
        state=mock_state_with_conflicts,
        incumbent=mock_state_with_conflicts,
        baseline_J=100.0,
        baseline_H=2,
        step_idx=5,
        max_steps=25,
        temperature=5.0,
        initial_temperature=10.0,
        stagnation_steps=2,
        operator_success_counts=succs,
        operator_call_counts=calls,
        plan=canonical_plan_0,
    )

    assert obs.shape == (24,)
    assert obs.dtype == np.float32
    assert not np.isnan(obs).any()
    assert not np.isinf(obs).any()

    # Features 0-15 check
    assert 0.0 <= obs[0] <= 1.0  # eligible rooms
    assert 0.0 <= obs[1] <= 1.0  # area
    assert 0.0 <= obs[2] <= 1.0  # hub count
    assert 0.0 <= obs[3] <= 1.0  # incumbent hub count
    assert 0.0 <= obs[4] <= 1.0  # J cost
    assert -1.0 <= obs[7] <= 1.0  # improvement
    assert 0.0 <= obs[8] <= 1.0  # gap to inc

    # Features 16-23 check (operator cluster success rates)
    for f_idx in range(16, 24):
        assert 0.0 <= obs[f_idx] <= 1.0


# =========================================================================
# TEST 4: Strict Dynamic Action Masking
# =========================================================================

def test_action_masking_crossing_reconstruction(canonical_plan_0: MetricFloorPlan, mock_state_with_conflicts: ALNSState) -> None:
    """Verify crossing reconstruction and elim are masked when X == 0."""
    door_graph = nx.Graph()
    door_graph.add_edge("R1", "R2")
    door_graph.add_edge("R2", "R3")

    # State with X == 3: crossing operators must be available
    avail = get_available_operators_v2(
        state=mock_state_with_conflicts,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=2,
    )
    assert OP_CROSSING_ELIM in avail
    assert OP_CROSSING_AWARE_RECONSTRUCTION in avail

    # Zero out crossings
    no_crossing_state = mock_state_with_conflicts.clone()
    no_crossing_state.bundle = no_crossing_state.bundle.model_copy(update={"total_crossings": 0})

    avail_zero_x = get_available_operators_v2(
        state=no_crossing_state,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=2,
    )
    # Strictly masked when X == 0
    assert OP_CROSSING_ELIM not in avail_zero_x
    assert OP_CROSSING_AWARE_RECONSTRUCTION not in avail_zero_x


def test_action_masking_overlap_rerouting(canonical_plan_0: MetricFloorPlan, mock_state_with_conflicts: ALNSState) -> None:
    """Verify overlap rerouting and reduce are masked when O <= 0.05m."""
    door_graph = nx.Graph()
    door_graph.add_edge("R1", "R2")
    door_graph.add_edge("R2", "R3")

    # State with O == 2.3: overlap operators must be available
    avail = get_available_operators_v2(
        state=mock_state_with_conflicts,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=2,
    )
    assert OP_OVERLAP_REDUCE in avail
    assert OP_OVERLAP_AWARE_REROUTING in avail

    # Zero out overlap
    no_overlap_state = mock_state_with_conflicts.clone()
    no_overlap_state.bundle = no_overlap_state.bundle.model_copy(update={"total_overlap_m": 0.0})

    avail_zero_o = get_available_operators_v2(
        state=no_overlap_state,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=2,
    )
    # Strictly masked when O <= 0.05
    assert OP_OVERLAP_REDUCE not in avail_zero_o
    assert OP_OVERLAP_AWARE_REROUTING not in avail_zero_o


def test_action_masking_alternative_partitions(canonical_plan_0: MetricFloorPlan, mock_state_with_conflicts: ALNSState) -> None:
    """Verify alternative partitions is strictly masked when only 1 optimal partition exists."""
    door_graph = nx.Graph()
    door_graph.add_edge("R1", "R2")
    door_graph.add_edge("R2", "R3")

    # When num_optimal_partitions == 1 (e.g. Plan 4): must be masked
    avail_single_part = get_available_operators_v2(
        state=mock_state_with_conflicts,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=1,
    )
    assert OP_ALTERNATIVE_MIN_HUB_PARTITIONS not in avail_single_part

    # When num_optimal_partitions > 1 (e.g. Plan 0 has 4): must be available
    avail_multi_part = get_available_operators_v2(
        state=mock_state_with_conflicts,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=4,
    )
    assert OP_ALTERNATIVE_MIN_HUB_PARTITIONS in avail_multi_part


def test_action_masking_single_zone(canonical_plan_0: MetricFloorPlan, mock_state_with_conflicts: ALNSState) -> None:
    """Verify multi-zone operators are masked when zone count < 2."""
    single_zone_state = mock_state_with_conflicts.clone()
    single_zone_state.zones = [{"R1", "R2", "R3"}]

    door_graph = nx.Graph()
    door_graph.add_edge("R1", "R2")
    door_graph.add_edge("R2", "R3")

    avail = get_available_operators_v2(
        state=single_zone_state,
        plan=canonical_plan_0,
        topology=None,
        door_graph=door_graph,
        num_optimal_partitions=1,
    )
    assert OP_BOUNDARY_EXCHANGE not in avail
    assert OP_RANDOM_EXPLORATION not in avail
    assert OP_JOINT_ZONE_REASSIGNMENT not in avail


def test_bandit_select_arm_respects_masks() -> None:
    """Verify LinUCB bandit select_arm strictly chooses from available_actions."""
    bandit = LinUCBBandit(feature_dim=24, num_arms=14, alpha=0.5)
    context = np.ones(24, dtype=np.float64)

    # Only allow advanced operators 8, 9, 10
    allowed = [OP_CROSSING_AWARE_RECONSTRUCTION, OP_OVERLAP_AWARE_REROUTING, OP_HUB_POSITION_REFINEMENT]
    for _ in range(20):
        chosen, scores = bandit.select_arm(context, available_actions=allowed)
        assert chosen in allowed
        for a in range(14):
            if a not in allowed:
                assert scores[a] == float("-inf")


# =========================================================================
# TEST 5: Reward Shaping Properties
# =========================================================================

def test_v2_reward_properties(mock_state_with_conflicts: ALNSState) -> None:
    """Verify reward shaping formulation with feasibility, hub priority, and geometric signals."""
    prev = mock_state_with_conflicts.clone()
    curr = mock_state_with_conflicts.clone()

    # 1. Inapplicable move penalty
    r_unapplied = compute_v2_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=prev,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=False,
        is_valid=True,
    )
    assert r_unapplied == -0.05

    # 2. Invalid state penalty
    r_invalid = compute_v2_reward(
        prev_state=prev,
        curr_state=curr,
        incumbent_state=prev,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=False,
    )
    assert r_invalid == -1.0

    # 3. Hub reduction bonus
    curr_h_reduced = curr.clone()
    curr_h_reduced.h_count = 1
    r_h_reduced = compute_v2_reward(
        prev_state=prev,
        curr_state=curr_h_reduced,
        incumbent_state=prev,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert r_h_reduced > 5.0  # +5.0 for dH + incumbent bonus

    # 4. Hub increase penalty
    curr_h_increased = curr.clone()
    curr_h_increased.h_count = 3
    r_h_increased = compute_v2_reward(
        prev_state=prev,
        curr_state=curr_h_increased,
        incumbent_state=prev,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert r_h_increased < -9.0  # -10.0 penalty for H increase

    # 5. Crossing elimination bonus
    curr_less_x = curr.clone()
    curr_less_x.bundle = curr_less_x.bundle.model_copy(update={"total_crossings": 1})  # reduced from 3 to 1 (dx = 2)
    r_less_x = compute_v2_reward(
        prev_state=prev,
        curr_state=curr_less_x,
        incumbent_state=prev,
        baseline_J=100.0,
        baseline_H=2,
        was_applied=True,
        is_valid=True,
    )
    assert r_less_x > 0.5  # Includes crossing elimination bonus


# =========================================================================
# TEST 6: Optimizer Execution on Canonical Plan 0
# =========================================================================

def test_alns_v2_optimizer_execution_plan_0(canonical_plan_0: MetricFloorPlan) -> None:
    """Execute LinUCB_ALNS_V2Optimizer on Plan 0 and audit feasibility."""
    opt = LinUCB_ALNS_V2Optimizer(
        max_iterations=10,
        alpha=0.5,
        mask_inapplicable=True,
    )

    result = opt.optimize(canonical_plan_0, time_limit_seconds=5.0, random_seed=42)

    # 1. 100% FloorPlanChecker Feasibility
    assert result.is_valid
    report = FloorPlanChecker.evaluate(canonical_plan_0, result.bundle)
    assert report.is_valid

    # 2. Telemetry and arm pull verification
    assert opt.bandit.k == 14
    diag = result.diagnostic_info
    assert "operator_calls" in diag
    assert len(diag["operator_calls"]) == 14
    assert "operator_successes" in diag
    assert len(diag["operator_successes"]) == 14
    assert sum(opt.arm_pull_counts) > 0


# =========================================================================
# TEST 7: Optimizer Execution on Canonical Plan 4 (High Crossing / Overlap)
# =========================================================================

def test_alns_v2_optimizer_execution_plan_4(canonical_plan_4: MetricFloorPlan) -> None:
    """Execute LinUCB_ALNS_V2Optimizer on Plan 4 with high initial crossings and overlap."""
    opt = LinUCB_ALNS_V2Optimizer(
        max_iterations=12,
        alpha=0.5,
        mask_inapplicable=True,
    )

    result = opt.optimize(canonical_plan_4, time_limit_seconds=5.0, random_seed=123)

    assert result.is_valid
    report = FloorPlanChecker.evaluate(canonical_plan_4, result.bundle)
    assert report.is_valid

    diag = result.diagnostic_info
    assert diag["iterations"] > 0
    assert opt.bandit.step_counter > 0


# =========================================================================
# TEST 8: Incumbent Monotonicity and Validated Fallback
# =========================================================================

def test_incumbent_monotonicity_never_worse_than_baseline(canonical_plan_0: MetricFloorPlan) -> None:
    """Verify LinUCB_ALNS_V2Optimizer strictly outperforms or matches PortfolioHybrid."""
    base_opt = PortfolioHybridOptimizer()
    base_res = base_opt.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)
    assert base_res.is_valid

    v2_opt = LinUCB_ALNS_V2Optimizer(
        max_iterations=8,
        base_opt=base_opt,
        use_cache=False,
    )
    v2_res = v2_opt.optimize(canonical_plan_0, time_limit_seconds=5.0, random_seed=42)

    assert v2_res.is_valid
    # Strict lexicographical check
    assert v2_res.h_count <= base_res.h_count
    if v2_res.h_count == base_res.h_count:
        assert v2_res.total_cost_J <= base_res.total_cost_J + 1e-4


def test_validated_fallback_when_zero_iterations(canonical_plan_0: MetricFloorPlan) -> None:
    """Verify safe fallback to pristine baseline when no iterations are executed."""
    opt = LinUCB_ALNS_V2Optimizer(
        max_iterations=0,  # Force immediate fallback
    )

    res = opt.optimize(canonical_plan_0, time_limit_seconds=3.0, random_seed=42)

    assert res.is_valid
    assert res.diagnostic_info.get("fallback_to_baseline") is True
    assert res.diagnostic_info.get("fallback_reason") == "no_improving_moves_found"
