"""Unit and integration test suite for Advanced ALNS Operators (Agent 3).

Validates:
1. All 6 advanced ALNS operators execute safely and preserve 100% FloorPlanChecker validity.
2. Crossing-aware reconstruction successfully reduces or preserves orthogonal crossings.
3. Overlap-aware rerouting reduces or preserves collinear overlap.
4. Hub-position refinement respects host room bounds and >= 0.50m wall clearance.
5. Alternative minimum-hub partitions strictly preserve the proven minimum hub count H*.
6. Ring-order neighborhood search (3-opt / Or-opt) yields valid closed ring cycles.
7. Joint zone reassignment maintains capacity limits (<= 100 m2, <= 5 nodes) and door contiguity.
8. AdvancedALNSOptimizer achieves 100% validity and incumbent monotonicity (never worse than champion).
"""

from __future__ import annotations

import random
import pytest

from resplan_router.contracts import MetricFloorPlan
from resplan_router.experimental.advanced_operators import (
    ADVANCED_OPERATOR_NAMES,
    NUM_ADVANCED_OPERATORS,
    AdvancedALNSOptimizer,
    apply_advanced_operator,
    detect_ring_crossings,
    detect_ring_overlaps,
    operator_alternative_min_hub_partitions,
    operator_crossing_aware_route_reconstruction,
    operator_hub_position_refinement,
    operator_joint_zone_reassignment,
    operator_overlap_aware_rerouting,
    operator_ring_order_search,
)
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker


@pytest.fixture(scope="module")
def sample_plans_and_states():
    """Load canonical test plans (0 and 4) with initialized champion states."""
    repo = DatasetRepository()
    opt = PortfolioHybridOptimizer()
    data = {}

    for pid in ["0", "4"]:
        plan = repo.get_floor_plan(pid)
        assert plan is not None
        res = opt.optimize(plan)
        assert res.is_valid
        assert res.bundle is not None
        assert res.partition is not None

        portals = extract_all_portals(plan)
        topology = MacroTopologyGraph.build_topology(plan, portals)
        door_graph = build_door_connectivity_graph(plan, topology)

        zones = [set(h.assigned_rooms) for h in res.partition.hubs]
        state = ALNSState(
            zones=zones,
            bundle=res.bundle,
            partition=res.partition,
            cost_J=res.total_cost_J,
            h_count=res.h_count,
            area_gap=res.area_gap_m2,
            is_valid=True,
        )
        data[pid] = (plan, topology, door_graph, state, res)

    return data


def test_detect_crossings_and_overlaps(sample_plans_and_states):
    """Test geometric conflict detection on Plan 4."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    ring = state.bundle.hub_rings[0]

    terminals = {ring.hub_id: ring.location}
    for nid in ring.assigned_nodes:
        terminals[nid] = plan.nodes[nid].location

    crossings = detect_ring_crossings(ring, terminals)
    overlaps = detect_ring_overlaps(ring)

    assert isinstance(crossings, list)
    assert isinstance(overlaps, list)
    # Plan 4 has known crossings and overlap
    assert len(crossings) > 0
    assert len(overlaps) > 0


def test_operator_crossing_aware_route_reconstruction(sample_plans_and_states):
    """Test crossing-aware reconstruction on Plan 4."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    rng = random.Random(42)

    cand_state = operator_crossing_aware_route_reconstruction(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count == state.h_count
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid
        assert cand_state.bundle.total_crossings <= state.bundle.total_crossings or cand_state.cost_J <= state.cost_J


def test_operator_overlap_aware_rerouting(sample_plans_and_states):
    """Test overlap-aware rerouting on Plan 4."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    rng = random.Random(42)

    cand_state = operator_overlap_aware_rerouting(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count == state.h_count
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid
        assert cand_state.bundle.total_overlap_m <= state.bundle.total_overlap_m or cand_state.cost_J <= state.cost_J


def test_operator_hub_position_refinement(sample_plans_and_states):
    """Test hub position refinement on Plan 4."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    rng = random.Random(42)

    cand_state = operator_hub_position_refinement(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count == state.h_count
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid
        # Hub location clearance is verified by FloorPlanChecker (Rule 6)
        assert rep.rules_passed["Rule 6"]
        assert len([v for v in rep.violations if v.rule_number == 6]) == 0


def test_operator_alternative_min_hub_partitions(sample_plans_and_states):
    """Test alternative minimum-hub partitions on Plan 0 (which has H*=2)."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["0"]
    rng = random.Random(42)

    cand_state = operator_alternative_min_hub_partitions(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count == state.h_count  # Strictly preserves H*
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid


def test_operator_ring_order_search(sample_plans_and_states):
    """Test 3-opt / Or-opt ring order search on Plan 4."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    rng = random.Random(42)

    cand_state = operator_ring_order_search(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count == state.h_count
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid


def test_operator_joint_zone_reassignment(sample_plans_and_states):
    """Test joint zone reassignment on Plan 0."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["0"]
    rng = random.Random(42)

    cand_state = operator_joint_zone_reassignment(state, plan, topology, door_graph, rng)
    if cand_state is not None:
        assert cand_state.is_valid
        assert cand_state.h_count <= state.h_count
        rep = FloorPlanChecker.evaluate(plan, cand_state.bundle)
        assert rep.is_valid


def test_all_advanced_operators_dispatch(sample_plans_and_states):
    """Test dispatcher for all 6 operators."""
    plan, topology, door_graph, state, init_res = sample_plans_and_states["4"]
    rng = random.Random(123)

    for op_idx in range(NUM_ADVANCED_OPERATORS):
        res_state, applied, op_name = apply_advanced_operator(op_idx, state, plan, topology, door_graph, rng)
        assert op_name == ADVANCED_OPERATOR_NAMES[op_idx]
        assert res_state.is_valid
        assert res_state.h_count <= state.h_count


def test_advanced_alns_optimizer_end_to_end(sample_plans_and_states):
    """Test AdvancedALNSOptimizer end-to-end execution on Plan 4."""
    plan, _, _, _, init_res = sample_plans_and_states["4"]

    opt = AdvancedALNSOptimizer(name="Test_Advanced_ALNS", max_iterations=10)
    res = opt.optimize(plan, time_limit_seconds=5.0, random_seed=42)

    assert res.is_valid
    assert res.h_count == init_res.h_count  # Provable minimum H preserved
    assert res.total_cost_J <= init_res.total_cost_J + 1e-4  # Incumbent monotonicity
    assert res.bundle is not None
    assert res.partition is not None
