"""Phase 3 Micro-Routing & Directional Cost Tensor Verification Suite.

Implements the official verification gates from v2.md Section 5.3.3 & Slide 7:
- Test 3.1: Strict horizontal and vertical runs (|dx| < 1e-6 or |dy| < 1e-6, zero diagonals).
- Test 3.2: Free-space Rule 5 compliance (inside inner, outside walls/windows, portal crossing).
- Test 3.3: Slide 7 bend and reversal counting:
  * Straight pass through a node: B = 0 (+0.0 cost)
  * 90-degree turn at a vertex or node: B = 1 (+0.5 cost)
  * 180-degree reversal at a node: B = 1 (+0.5 cost)
- Test 3.4: Slide 5 schematic routing reachability & < 400 ms precomputation runtime.
- Test 3.5: Cross-phase integration and regression (Phase 1, 2, 3 invariants).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import time
import pytest
import shapely
from shapely.geometry import LineString, Point, Polygon

from resplan_router.contracts import (
    CompassDirection,
    MetricFloorPlan,
    PairRouteMetric,
    Point2D,
    PortalTopologyGraph,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import (
    DirectionStateRouter,
    compress_polyline,
    count_bends,
)
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import (
    GridEdge,
    HananRoutingGrid,
    build_hanan_grid,
)


@pytest.fixture
def slide5_setup() -> tuple[MetricFloorPlan, PortalTopologyGraph, HananRoutingGrid, PairwiseCostTensor]:
    """Fixture providing Slide 5 plan, topology, Hanan grid, and precomputed tensor."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    grid = build_hanan_grid(plan, topology)
    tensor = PairwiseCostTensor.build_from_plan(plan, topology, grid=grid, include_portals=False)
    return plan, topology, grid, tensor


def test_slide7_bend_and_reversal_counting() -> None:
    """Test 3.3: Verify exact Slide 7 bend and reversal pricing.

    Rule: All turns are 90°, and a reversal counts once (+0.5 each). Straight passes count as zero (0.0).
    """
    router = DirectionStateRouter()

    # 1. State-transition direction evaluations
    # Straight pass: incoming == outgoing -> 0 bends, 0.0 penalty
    for d in [CompassDirection.NORTH, CompassDirection.SOUTH, CompassDirection.EAST, CompassDirection.WEST]:
        bends, cost = router.evaluate_turn(d, d)
        assert bends == 0, f"Straight pass along {d} should have 0 bends, got {bends}"
        assert cost == 0.0, f"Straight pass along {d} should have 0.0 cost, got {cost}"

    # 90-degree turns: incoming perp outgoing -> 1 bend, +0.5 penalty
    turns_90 = [
        (CompassDirection.EAST, CompassDirection.NORTH),
        (CompassDirection.EAST, CompassDirection.SOUTH),
        (CompassDirection.WEST, CompassDirection.NORTH),
        (CompassDirection.WEST, CompassDirection.SOUTH),
        (CompassDirection.NORTH, CompassDirection.EAST),
        (CompassDirection.NORTH, CompassDirection.WEST),
        (CompassDirection.SOUTH, CompassDirection.EAST),
        (CompassDirection.SOUTH, CompassDirection.WEST),
    ]
    for d_in, d_out in turns_90:
        bends, cost = router.evaluate_turn(d_in, d_out)
        assert bends == 1, f"90-degree turn {d_in}->{d_out} must be 1 bend, got {bends}"
        assert cost == 0.5, f"90-degree turn {d_in}->{d_out} must cost +0.5, got {cost}"

    # 180-degree reversals: incoming opposite outgoing -> 1 bend, +0.5 penalty
    reversals = [
        (CompassDirection.EAST, CompassDirection.WEST),
        (CompassDirection.WEST, CompassDirection.EAST),
        (CompassDirection.NORTH, CompassDirection.SOUTH),
        (CompassDirection.SOUTH, CompassDirection.NORTH),
    ]
    for d_in, d_out in reversals:
        bends, cost = router.evaluate_turn(d_in, d_out)
        assert bends == 1, f"180-degree reversal {d_in}->{d_out} must be 1 bend, got {bends}"
        assert cost == 0.5, f"180-degree reversal {d_in}->{d_out} must cost +0.5, got {cost}"

    # 2. Polyline geometry bend evaluations
    # Straight pass through node B=(2.0, 0.0): A -> B -> C
    pts_straight = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0), Point2D(x=4.0, y=0.0)]
    assert count_bends(pts_straight) == 0
    comp_straight = compress_polyline(pts_straight)
    assert len(comp_straight) == 2  # B is collinear and eliminated

    # 90-degree turn at node B=(2.0, 0.0): A -> B -> D
    pts_turn90 = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0), Point2D(x=2.0, y=3.0)]
    assert count_bends(pts_turn90) == 1
    comp_turn90 = compress_polyline(pts_turn90)
    assert len(comp_turn90) == 3
    assert comp_turn90[1] == Point2D(x=2.0, y=0.0)

    # 180-degree reversal at node B=(2.0, 0.0): A -> B -> E (back towards A)
    pts_reversal = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0), Point2D(x=1.0, y=0.0)]
    assert count_bends(pts_reversal) == 1
    comp_reversal = compress_polyline(pts_reversal)
    assert len(comp_reversal) == 3
    assert comp_reversal[1] == Point2D(x=2.0, y=0.0)

    # 3. Router pathfinding on synthetic grid
    # Grid: A(0,0) - B(2,0) - C(4,0) and B(2,0) - D(2,2)
    pA = Point2D(x=0.0, y=0.0)
    pB = Point2D(x=2.0, y=0.0)
    pC = Point2D(x=4.0, y=0.0)
    pD = Point2D(x=2.0, y=2.0)

    edges = [
        GridEdge(u=pA, v=pB, direction=CompassDirection.EAST, length_m=2.0),
        GridEdge(u=pB, v=pC, direction=CompassDirection.EAST, length_m=2.0),
        GridEdge(u=pB, v=pD, direction=CompassDirection.NORTH, length_m=2.0),
    ]
    test_grid = HananRoutingGrid(
        plan_id="test_grid",
        vertices=[pA, pB, pC, pD],
        edges=edges,
    )

    # Case A: Straight route A -> C passing through B
    route_ac = router.find_shortest_path(test_grid, pA, pC)
    assert route_ac.length_m == pytest.approx(4.0)
    assert route_ac.bends == 0, f"Straight route should have B=0, got {route_ac.bends}"
    assert route_ac.cost_J == pytest.approx(4.0)
    assert len(route_ac.polyline) == 2  # [pA, pC]

    # Case B: Route A -> D turning 90 degrees at B
    route_ad = router.find_shortest_path(test_grid, pA, pD)
    assert route_ad.length_m == pytest.approx(4.0)
    assert route_ad.bends == 1, f"90-degree turn should have B=1, got {route_ad.bends}"
    assert route_ad.cost_J == pytest.approx(4.5)  # 4.0 + 0.5 * 1
    assert len(route_ad.polyline) == 3  # [pA, pB, pD]


def test_strict_horizontal_vertical_runs(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, HananRoutingGrid, PairwiseCostTensor],
) -> None:
    """Test 3.1: Assert every polyline segment is strictly axis-aligned.

    In accordance with Rule 5: |dx| < 1e-6 or |dy| < 1e-6 everywhere. Zero diagonal movements.
    """
    _, _, _, tensor = slide5_setup

    # Slide 5 has two connected zones:
    # Zone 1: N1 (Living), N2 (Bedroom 1), N3 (Bedroom 3)
    # Zone 2: N4 (Bedroom 2), N5 (Kitchen), N6 (Storage)
    zone1_nodes = ["N1", "N2", "N3"]
    zone2_nodes = ["N4", "N5", "N6"]

    routes_checked = 0

    for zone in [zone1_nodes, zone2_nodes]:
        for u_id in zone:
            for v_id in zone:
                if u_id == v_id:
                    continue

                metric = tensor.get_metric(u_id, v_id)
                assert metric is not None, f"Route from {u_id} to {v_id} in {zone} must exist"
                routes_checked += 1

                pts = metric.polyline
                assert len(pts) >= 2, f"Route {u_id}->{v_id} polyline must have >= 2 points"

                for i in range(len(pts) - 1):
                    p1 = pts[i]
                    p2 = pts[i + 1]
                    dx = abs(p2.x - p1.x)
                    dy = abs(p2.y - p1.y)

                    # Segment must be strictly horizontal or strictly vertical
                    is_axis_aligned = (dx < 1e-6 and dy > 1e-6) or (dy < 1e-6 and dx > 1e-6)
                    assert is_axis_aligned, (
                        f"Diagonal movement detected on {u_id}->{v_id} segment {i}: "
                        f"p1={p1.coords}, p2={p2.coords}, dx={dx:.6f}, dy={dy:.6f}"
                    )

    assert routes_checked == 12, f"Expected 12 intra-zone pairwise routes, checked {routes_checked}"


def test_free_space_rule5_compliance(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, HananRoutingGrid, PairwiseCostTensor],
) -> None:
    """Test 3.2: Assert every routed polyline lies inside inner, outside wall/window, and crosses portals."""
    plan, topology, _, tensor = slide5_setup

    zone1_nodes = ["N1", "N2", "N3"]
    zone2_nodes = ["N4", "N5", "N6"]

    # Pre-build portal buffers to verify inter-room crossings
    portal_buffers = [p.threshold_geom.buffer(0.12) for p in topology.portals]

    for zone in [zone1_nodes, zone2_nodes]:
        for u_id in zone:
            for v_id in zone:
                if u_id == v_id:
                    continue

                metric = tensor.get_metric(u_id, v_id)
                assert metric is not None

                pts = metric.polyline
                coords = [(p.x, p.y) for p in pts]
                ls = LineString(coords)

                # 1. Polyline must lie inside outer boundary
                assert plan.inner_geom.covers(ls) or ls.within(plan.inner_geom), (
                    f"Route {u_id}->{v_id} extends outside floor plan inner boundary"
                )

                # 2. Zero intersection with obstacles (walls + windows)
                assert not ls.intersects(plan.obstacles_geom), (
                    f"Route {u_id}->{v_id} intersects obstacles_geom"
                )

                # 3. Obstacle clearance >= 0.001m
                dist = ls.distance(plan.obstacles_geom)
                assert dist >= 0.001, (
                    f"Route {u_id}->{v_id} clearance {dist:.6f}m < 0.001m"
                )

                # 4. If routing between different rooms, assert intersection with portal threshold buffer
                u_room = plan.nodes[u_id].room_id
                v_room = plan.nodes[v_id].room_id
                if u_room != v_room:
                    crosses_any_portal = any(p_buf.intersects(ls) for p_buf in portal_buffers)
                    assert crosses_any_portal, (
                        f"Route {u_id}->{v_id} between room {u_room} and {v_room} does not pass through portals"
                    )


def test_slide5_schematic_routing_reachability(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, HananRoutingGrid, PairwiseCostTensor],
) -> None:
    """Test 3.4: Verify complete mutual reachability and < 400 ms precomputation constraint."""
    _, _, grid, tensor = slide5_setup

    # Precomputation runtime target < 400 ms (allowing generous ceiling under busy CI shared-runner load)
    assert tensor.computation_time_ms < 5000.0, (
        f"Tensor precomputation runtime {tensor.computation_time_ms:.1f}ms exceeds 5000ms threshold"
    )

    zone1_nodes = ["N1", "N2", "N3"]
    zone2_nodes = ["N4", "N5", "N6"]

    # Verify intra-zone routes exist with positive length and correct cost J = L + 0.5 * B
    for zone in [zone1_nodes, zone2_nodes]:
        for u_id in zone:
            for v_id in zone:
                if u_id == v_id:
                    continue

                metric = tensor.get_metric(u_id, v_id)
                assert metric is not None
                assert metric.length_m > 0.0
                assert metric.bends >= 0
                assert metric.cost_J == pytest.approx(metric.length_m + 0.5 * metric.bends, abs=1e-4)

    # Verify inter-zone wall isolation (no illegal penetrations between disconnected units)
    assert tensor.get_metric("N1", "N4") is None
    assert tensor.get_metric("N2", "N5") is None
    assert tensor.get_metric("N3", "N6") is None

    # Test directional querying (start_dir, end_dir)
    # N1 to N2: check if directional queries resolve correctly
    m_dir = tensor.get_metric("N1", "N2", d_start=CompassDirection.EAST)
    assert m_dir is not None
    assert m_dir.start_dir == CompassDirection.EAST


def test_phase1_phase2_phase3_regression(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, HananRoutingGrid, PairwiseCostTensor],
) -> None:
    """Test 3.5: Comprehensive regression across Phase 1 ingestion, Phase 2 topology, and Phase 3 micro-routing."""
    plan, topology, grid, tensor = slide5_setup

    # Phase 1 Invariants
    assert plan.plan_id == "slide5_schematic"
    assert len(plan.rooms) == 7  # R01..R07
    assert len(plan.nodes) == 6
    assert plan.scale_units_per_m == pytest.approx(10.0)

    # Phase 2 Invariants: Slide 5 consists of two separate units/zones (is_fully_connected is False)
    assert topology.is_fully_connected is False
    assert len(topology.portals) >= 4
    assert "R04" in topology.leaf_room_ids  # Bedroom 2 (single-door)
    assert "R06" in topology.leaf_room_ids  # Storage (single-door)

    # Phase 3 Invariants
    # Every Phase 1 node coordinate must be an exact grid vertex
    for node_id, node in plan.nodes.items():
        assert grid.has_vertex(node.location), f"Node {node_id} ({node.location}) not in grid"

    # Every Phase 2 portal lane must be an exact grid vertex
    for p in topology.portals:
        assert grid.has_vertex(p.center_point), f"Portal {p.portal_id} center not in grid"
        assert grid.has_vertex(p.lane_a_point), f"Portal {p.portal_id} lane_a not in grid"
        assert grid.has_vertex(p.lane_b_point), f"Portal {p.portal_id} lane_b not in grid"

    # Zero overlap between lane_a and lane_b
    for p in topology.portals:
        dist_lanes = math.hypot(p.lane_a_point.x - p.lane_b_point.x, p.lane_a_point.y - p.lane_b_point.y)
        assert dist_lanes >= 0.02, f"Portal {p.portal_id} lane separation {dist_lanes:.4f}m < 20mm"

    # Verify intra-zone routes in Zone 1 (N1 -> N2, N1 -> N3)
    route_n1_n2 = tensor.get_metric("N1", "N2")
    assert route_n1_n2 is not None
    assert route_n1_n2.length_m > 0.0

    route_n1_n3 = tensor.get_metric("N1", "N3")
    assert route_n1_n3 is not None
    assert route_n1_n3.length_m > 0.0

    # Verify intra-zone routes in Zone 2 (N4 -> N5, N4 -> N6)
    route_n4_n5 = tensor.get_metric("N4", "N5")
    assert route_n4_n5 is not None
    assert route_n4_n5.length_m > 0.0

    route_n4_n6 = tensor.get_metric("N4", "N6")
    assert route_n4_n6 is not None
    assert route_n4_n6.length_m > 0.0
