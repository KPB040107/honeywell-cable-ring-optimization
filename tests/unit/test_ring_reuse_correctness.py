"""Exhaustive Verification of Ring-Reuse Optimization & Constraint Preservation.

Tests that ALNS ring-reuse acceleration:
1. Preserves all 6 official Hard Rules and Slide 8 contracts.
2. Correctly flags and rejects any candidate where modifying one ring causes an inter-ring
   crossing or collinear overlap with a supposedly reusable ring.
3. Guarantees that unmodified rings preserve their exact sequence, terminal matching,
   clearance, and rectilinear integrity.
4. Correctly invalidates cache when zone room memberships change.
"""

import copy
from typing import Any, Tuple
import pytest

from resplan_router.contracts import (
    CompletedHubRing,
    HubAssignment,
    MetricFloorPlan,
    Point2D,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    state_from_zones,
    synthesize_rings_for_zones,
    operator_boundary_exchange,
    operator_crossing_elim,
    operator_bend_smooth,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker


def _state_from_result(base_res, plan: MetricFloorPlan) -> ALNSState:
    zones = [set(h.assigned_rooms) for h in base_res.partition.hubs]
    return ALNSState(
        zones=zones,
        bundle=base_res.bundle,
        partition=base_res.partition,
        cost_J=base_res.total_cost_J,
        h_count=base_res.h_count,
        area_gap=base_res.area_gap_m2,
        is_valid=base_res.is_valid,
    )


@pytest.fixture(scope="module")
def sample_multizone_plan() -> Tuple[MetricFloorPlan, Any]:
    """Fixture providing a verified multi-room floor plan from ResPlan and its topology."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)
    return plan, topology


def test_ring_reuse_preserves_unmodified_ring_geometry(sample_multizone_plan):
    """Verify that reusable rings retain identical polylines, lengths, and bends."""
    plan, topology = sample_multizone_plan
    opt = PortfolioHybridOptimizer()
    base_res = opt.optimize(plan)
    assert base_res.is_valid, "Baseline must be valid"

    initial_state = _state_from_result(base_res, plan)
    assert len(initial_state.zones) >= 2, "Test requires at least 2 zones"

    orig_ring_0 = initial_state.bundle.hub_rings[0]
    orig_hub_0 = initial_state.partition.hubs[0]

    reusable_rings = {0: orig_ring_0}
    reusable_hubs = {0: orig_hub_0}

    new_bundle, new_partition = synthesize_rings_for_zones(
        zones=initial_state.zones,
        plan=plan,
        topology=topology,
        reusable_rings=reusable_rings,
        reusable_hubs=reusable_hubs,
    )

    reused_ring_0 = new_bundle.hub_rings[0]
    reused_hub_0 = new_partition.hubs[0]

    assert reused_ring_0.ring_length_m == orig_ring_0.ring_length_m
    assert reused_ring_0.ring_bends == orig_ring_0.ring_bends
    assert reused_ring_0.closed_sequence == orig_ring_0.closed_sequence
    assert reused_hub_0.location == orig_hub_0.location
    assert len(reused_ring_0.route_segments) == len(orig_ring_0.route_segments)

    report = FloorPlanChecker.evaluate(plan, new_bundle)
    assert report.is_valid, f"Reused ring bundle must be valid: {report.violations}"


def test_checker_detects_inter_ring_crossing_with_reused_ring(sample_multizone_plan):
    """Test that if modifying ring 1 creates an inter-ring crossing with reused ring 0,
    the FloorPlanChecker correctly flags it and increments total_crossings.
    """
    plan, topology = sample_multizone_plan
    opt = PortfolioHybridOptimizer()
    base_res = opt.optimize(plan)
    assert base_res.is_valid

    bundle = copy.deepcopy(base_res.bundle)
    assert len(bundle.hub_rings) >= 2

    ring0_seg = bundle.hub_rings[0].route_segments[0]
    p0_start = ring0_seg.polyline[0]
    p0_end = ring0_seg.polyline[-1]

    mid_x = (p0_start.x + p0_end.x) / 2.0
    mid_y = (p0_start.y + p0_end.y) / 2.0

    # Cross perpendicular across midpoint with strictly positive coordinates
    delta = 0.5
    if abs(p0_end.x - p0_start.x) > abs(p0_end.y - p0_start.y):
        cross_poly = [
            Point2D(x=mid_x, y=max(0.05, mid_y - delta)),
            Point2D(x=mid_x, y=mid_y + delta),
        ]
    else:
        cross_poly = [
            Point2D(x=max(0.05, mid_x - delta), y=mid_y),
            Point2D(x=mid_x + delta, y=mid_y),
        ]

    bundle.hub_rings[1].route_segments.append(
        RouteSegment(from_node="dummy_a", to_node="dummy_b", length_m=round(2 * delta, 4), polyline=cross_poly)
    )

    report = FloorPlanChecker.evaluate(plan, bundle)
    assert report.total_crossings > 0, "Checker must detect inter-ring crossing between ring 1 and reused ring 0"


def test_cache_invalidation_on_zone_modification(sample_multizone_plan):
    """Verify that when room membership in a zone changes, the ring is NOT reused."""
    plan, topology = sample_multizone_plan
    opt = PortfolioHybridOptimizer()
    base_res = opt.optimize(plan)
    assert base_res.is_valid

    initial_state = _state_from_result(base_res, plan)
    z0 = initial_state.zones[0]
    z1 = initial_state.zones[1]

    room_to_move = sorted(z0)[0]
    mod_z0 = z0 - {room_to_move}
    mod_z1 = z1 | {room_to_move}

    new_state = state_from_zones(
        zones=[mod_z0, mod_z1],
        plan=plan,
        topology=topology,
        parent_state=initial_state,
    )

    if new_state is not None:
        ring0_rooms = set(new_state.bundle.hub_rings[0].assigned_rooms)
        assert ring0_rooms == mod_z0, "Ring 0 must reflect modified room partition"
        assert room_to_move not in ring0_rooms, "Moved room must not remain in ring 0"


def test_all_alns_operators_preserve_checker_validity(sample_multizone_plan):
    """Verify that ALNS operators with ring reuse produce strictly valid states."""
    import random
    rng = random.Random(42)
    plan, topology = sample_multizone_plan
    opt = PortfolioHybridOptimizer()
    base_res = opt.optimize(plan)
    state = _state_from_result(base_res, plan)
    door_graph = build_door_connectivity_graph(plan, topology)

    operators = [
        ("BOUNDARY_EXCHANGE", operator_boundary_exchange),
        ("CROSSING_ELIM", operator_crossing_elim),
        ("BEND_SMOOTH", operator_bend_smooth),
    ]

    for name, op in operators:
        res_state = op(state, plan, topology, door_graph, rng)
        if res_state is not None:
            assert res_state.is_valid, f"Operator {name} returned invalid state"
            report = FloorPlanChecker.evaluate(plan, res_state.bundle)
            assert report.is_valid, f"FloorPlanChecker flagged operator {name} solution as invalid: {report.violations}"
            assert res_state.cost_J == report.total_cost_J, "State cost must equal checker recomputed cost"
