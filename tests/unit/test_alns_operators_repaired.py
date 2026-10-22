"""Regression and correctness tests for repaired ALNS operators (Task 1).

Validates:
1. operator_worst_route_rebuild excludes the current hub room.
2. Single-room zones return None (clean inapplicability) instead of returning an identical state.
3. apply_alns_operator detects genuine no-op moves and sets was_applied=False.
4. All 8 operators preserve 100% FloorPlanChecker validity.
"""

from __future__ import annotations

import random
from typing import Set

import pytest

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
    operator_worst_route_rebuild,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker


@pytest.fixture(scope="module")
def canonical_plan_and_state():
    repo = DatasetRepository()
    plan = repo.get_floor_plan("14433")
    assert plan is not None

    opt = PortfolioHybridOptimizer()
    init_res = opt.optimize(plan, time_limit_seconds=2.0)
    assert init_res.is_valid
    assert init_res.bundle is not None
    assert init_res.partition is not None

    zones = [set(h.assigned_rooms) for h in init_res.partition.hubs]
    state = ALNSState(
        zones=zones,
        bundle=init_res.bundle,
        partition=init_res.partition,
        cost_J=init_res.total_cost_J,
        h_count=init_res.h_count,
        area_gap=init_res.area_gap_m2,
        is_valid=True,
    )

    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)
    door_graph = build_door_connectivity_graph(plan, topology)

    return plan, topology, door_graph, state


def test_worst_route_rebuild_excludes_current_hub_room(canonical_plan_and_state):
    """Verify that operator_worst_route_rebuild never selects the current hub room."""
    plan, topology, door_graph, state = canonical_plan_and_state
    rng = random.Random(42)

    # Call operator 20 times with different RNG seeds
    for seed in range(20):
        rng_local = random.Random(seed)
        cand_state = operator_worst_route_rebuild(state, plan, topology, door_graph, rng_local)
        if cand_state is not None:
            # If applied, check that the rebuilt ring actually relocated the hub
            rings = state.bundle.hub_rings
            worst_idx = max(
                range(len(rings)),
                key=lambda i: rings[i].cost_J / max(1, len(rings[i].assigned_nodes)),
            )
            orig_hub_room = rings[worst_idx].room_id
            new_hub_room = cand_state.bundle.hub_rings[worst_idx].room_id
            assert new_hub_room != orig_hub_room, (
                f"Candidate hub room {new_hub_room} must differ from original {orig_hub_room}"
            )


def test_worst_route_rebuild_single_room_zone_returns_none(canonical_plan_and_state):
    """Verify that if the target zone has only 1 room, the operator cleanly returns None."""
    plan, topology, door_graph, state = canonical_plan_and_state

    # Synthetic small state with single-room target zone
    synth_state = state.clone()
    rings = synth_state.bundle.hub_rings
    worst_idx = max(
        range(len(rings)),
        key=lambda i: rings[i].cost_J / max(1, len(rings[i].assigned_nodes)),
    )
    synth_state.zones[worst_idx] = {rings[worst_idx].room_id}

    rng = random.Random(42)
    cand_state = operator_worst_route_rebuild(synth_state, plan, topology, door_graph, rng)
    assert cand_state is None, "Operator must return None when no alternate rooms exist in zone"


def test_apply_alns_operator_detects_no_op(canonical_plan_and_state):
    """Verify that apply_alns_operator sets was_applied=False if state is unchanged."""
    plan, topology, door_graph, state = canonical_plan_and_state
    rng = random.Random(42)

    # Calling an operator that cannot make changes should return was_applied=False
    # Test on a single-room state where boundary exchange is impossible
    single_zone_state = state.clone()
    single_zone_state.zones = [set().union(*state.zones)]

    new_state, applied, op_name = apply_alns_operator(
        operator_idx=0,  # BOUNDARY_EXCHANGE requires >= 2 zones
        state=single_zone_state,
        plan=plan,
        topology=topology,
        door_graph=door_graph,
        rng=rng,
    )
    assert applied is False
    assert new_state.cost_J == single_zone_state.cost_J


def test_all_operators_pass_floorplan_checker(canonical_plan_and_state):
    """Verify all 8 operators generate 100% valid solutions certified by FloorPlanChecker."""
    plan, topology, door_graph, state = canonical_plan_and_state

    for op_idx in range(NUM_OPERATORS):
        rng = random.Random(100 + op_idx)
        new_state, applied, op_name = apply_alns_operator(
            operator_idx=op_idx,
            state=state,
            plan=plan,
            topology=topology,
            door_graph=door_graph,
            rng=rng,
        )
        assert new_state.is_valid
        if applied:
            report = FloorPlanChecker.evaluate(plan, new_state.bundle)
            assert report.is_valid, f"Operator {op_name} produced invalid solution: {report.violations}"
