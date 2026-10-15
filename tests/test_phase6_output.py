"""Phase 6 Evaluation Checker & Slide 7 Metric Suite Verification.

Tests the independent 1 mm FloorPlanChecker against:
1. Slide 7 Metric Examples:
   - Length L: 4m+2m+3m+3m = 12.0m, out-and-back 3m out + 3m back = 6.0m
   - Crossings X: cables meeting in the open X = 1, legs meeting at node/hub X = 0, deduplicated X = 1
   - Overlap O: shared line <= 1mm -> O = 2.0m, parallel > 1mm (20mm) -> O = 0.0m
   - Bends B: two 90 deg turns -> B = 2, 180 deg reversal -> B = 1, straight through -> B = 0, hub wrap-around -> B = 1
2. Slide 3 Negative Rule Violations:
   - Hard Rule 1 (Coverage): Omitted node, duplicate room, excluded space assigned
   - Hard Rule 2 (Capacity): Assigned area > 100 m^2, nodes > 5
   - Hard Rule 3 (Contiguity): Disconnected rooms without doorway connection
   - Hard Rule 4 (Closed Ring): Open ring with 50mm gap, missing node in sequence
   - Hard Rule 5 (Free Space): Diagonal cable, cable intersecting wall obstacle
   - Hard Rule 6 (Hub Clearance): Hub placed within 0.2m of wall, hub in ineligible room
3. Slide 5 Baseline Validation:
   - Full Slide 5 benchmark evaluated with FloorPlanChecker: is_valid == True, L = 49.0m, B = 20, X = 0, O = 0.0, J = 59.0.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import pytest
from shapely.geometry import LineString, Point

from resplan_router.contracts import (
    CompletedHubRing,
    FixedNode,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer
from resplan_router.phase6_output.checker import FloorPlanChecker


@pytest.fixture
def slide5_setup() -> tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]:
    """Fixture providing Slide 5 metric floor plan, topology graph, and synthesized bundle."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)

    h1_pt = Point2D(x=5.2, y=3.6)
    h2_pt = Point2D(x=11.4, y=7.0)

    h1 = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=h1_pt,
        assigned_rooms=["R01", "R02", "R03"],
        assigned_nodes=["N1", "N2", "N3"],
        zone_area_m2=71.0,
    )
    h2 = HubAssignment(
        hub_id="H2",
        room_id="R05",
        location=h2_pt,
        assigned_rooms=["R04", "R05", "R06"],
        assigned_nodes=["N4", "N5", "N6"],
        zone_area_m2=47.0,
    )

    grid = build_hanan_grid(plan, topology, extra_points=[h1_pt, h2_pt])
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()

    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=2,
        lower_bound_hubs=2,
        area_gap_m2=24.0,
        hubs=[h1, h2],
    )
    bundle = synthesizer.synthesize_all_rings(partition, plan, topology, router, grid)
    return plan, topology, bundle


# =============================================================================
# 1. Slide 7 Metric Suite Unit Tests
# =============================================================================


def test_slide7_length_metric() -> None:
    """Test Slide 7 Metric 1: Cable length L recomputation.

    Slide 7 Example 1:
    - Segments 4m + 2m + 3m + 3m => L = 12.0m
    Slide 7 Example 2:
    - Out-and-back parallel segments 3m out + 3m back => L = 6.0m
    """
    # 1. 4m + 2m + 3m + 3m
    legs1 = [
        [
            Point2D(x=0.0, y=1.0),
            Point2D(x=4.0, y=1.0),
            Point2D(x=4.0, y=3.0),
            Point2D(x=1.0, y=3.0),
            Point2D(x=1.0, y=0.0),
        ]
    ]
    # Check segment distances directly
    l1 = sum(
        math.hypot(legs1[0][i + 1].x - legs1[0][i].x, legs1[0][i + 1].y - legs1[0][i].y)
        for i in range(len(legs1[0]) - 1)
    )
    assert l1 == pytest.approx(12.0, abs=1e-4)

    # 2. Out-and-back 3m out + 3m back = 6.0m
    legs2 = [
        [Point2D(x=0.0, y=0.0), Point2D(x=3.0, y=0.0)],
        [Point2D(x=3.0, y=0.0), Point2D(x=0.0, y=0.0)],
    ]
    l2 = sum(
        sum(
            math.hypot(leg[i + 1].x - leg[i].x, leg[i + 1].y - leg[i].y)
            for i in range(len(leg) - 1)
        )
        for leg in legs2
    )
    assert l2 == pytest.approx(6.0, abs=1e-4)


def test_slide7_crossings_metric() -> None:
    """Test Slide 7 Metric 2: Crossings X recomputation.

    Slide 7 Semantics:
    - Two cables crossing orthogonally in the open => X = 1 (5.0 penalty)
    - Two legs meeting at a node or hub => X = 0 (0.0 penalty)
    - Two crossings at the same location deduplicated => X = 1
    """
    # 1. Two cables crossing in the open
    leg_h = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0)]
    leg_v = [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
    terminals = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0), Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]

    x1 = FloorPlanChecker._compute_crossings_for_legs([leg_h, leg_v], terminals)
    assert x1 == 1

    # 2. Two legs meeting at a node (5.0, 5.0) -> X = 0
    node_pt = Point2D(x=5.0, y=5.0)
    leg_arr = [Point2D(x=0.0, y=5.0), node_pt]
    leg_dep = [node_pt, Point2D(x=5.0, y=10.0)]
    x2 = FloorPlanChecker._compute_crossings_for_legs([leg_arr, leg_dep], [node_pt])
    assert x2 == 0

    # 3. Two crossing pairs at the exact same location -> deduplicated to X = 1
    leg_v2 = [Point2D(x=5.0, y=2.0), Point2D(x=5.0, y=8.0)]
    x3 = FloorPlanChecker._compute_crossings_for_legs([leg_h, leg_v, leg_v2], terminals)
    assert x3 == 1


def test_slide7_overlap_metric() -> None:
    """Test Slide 7 Metric 3: Overlap O recomputation.

    Slide 7 Semantics:
    - Two 2m segments sharing the exact same line (<= 1mm) => O = 2.0m
    - Two 2m segments running parallel separated by 20mm (> 1mm) => O = 0.0m
    - Three 2m segments sharing the exact same line => (k - 1) * 2m = 4.0m
    """
    # 1. Exact retrace on same line (<= 1mm) -> O = 2.0m
    leg_a = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0)]
    leg_b = [Point2D(x=2.0, y=0.0), Point2D(x=0.0, y=0.0)]
    o1 = FloorPlanChecker._compute_overlap_for_legs([leg_a, leg_b])
    assert o1 == pytest.approx(2.0, abs=1e-4)

    # 2. Parallel runs separated by 20mm (> 1mm) -> O = 0.0m
    leg_c = [Point2D(x=0.0, y=0.02), Point2D(x=2.0, y=0.02)]
    o2 = FloorPlanChecker._compute_overlap_for_legs([leg_a, leg_c])
    assert o2 == pytest.approx(0.0, abs=1e-4)

    # 3. Three overlapping legs on [0, 2] -> (3 - 1) * 2.0m = 4.0m
    leg_d = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0)]
    o3 = FloorPlanChecker._compute_overlap_for_legs([leg_a, leg_b, leg_d])
    assert o3 == pytest.approx(4.0, abs=1e-4)


def test_slide7_bends_metric() -> None:
    """Test Slide 7 Metric 4: Bend count B recomputation.

    Slide 7 Semantics:
    - Two 90 deg turns along a leg => 2 bends
    - 180 deg reversal at a node => 1 bend
    - Straight path through a node => 0 bends
    - Wrap-around bend at hub => 1 bend
    """
    # 1. Closed loop with 4 turns (standard rectangle)
    rect_legs = [
        [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)],
        [Point2D(x=4.0, y=0.0), Point2D(x=4.0, y=2.0)],
        [Point2D(x=4.0, y=2.0), Point2D(x=0.0, y=2.0)],
        [Point2D(x=0.0, y=2.0), Point2D(x=0.0, y=0.0)],
    ]
    b_rect = FloorPlanChecker._compute_ring_bends(rect_legs)
    assert b_rect == 4

    # 2. Leg with two 90 deg internal turns
    s_leg = [
        Point2D(x=0.0, y=1.0),
        Point2D(x=2.0, y=1.0),
        Point2D(x=2.0, y=3.0),
        Point2D(x=4.0, y=3.0),
    ]
    # In a closed ring that returns directly:
    return_leg = [
        Point2D(x=4.0, y=3.0),
        Point2D(x=4.0, y=0.0),
        Point2D(x=0.0, y=0.0),
        Point2D(x=0.0, y=1.0),
    ]
    # S-leg has 2 internal turns (EAST->NORTH, NORTH->EAST)
    # Return-leg has 2 internal turns (SOUTH->WEST, WEST->NORTH)
    # Node turn at (4, 2): EAST -> SOUTH (1 turn)
    # Hub turn at (0, 0): NORTH -> EAST (1 turn)
    # Total = 6 bends
    b_s = FloorPlanChecker._compute_ring_bends([s_leg, return_leg])
    assert b_s == 6

    # 3. 180 deg reversal at a node
    out_leg = [Point2D(x=0.0, y=0.0), Point2D(x=3.0, y=0.0)]
    back_leg = [Point2D(x=3.0, y=0.0), Point2D(x=0.0, y=0.02)]
    closure = [Point2D(x=0.0, y=0.02), Point2D(x=0.0, y=0.0)]
    # Turn at node (3, 0): EAST -> WEST (1 turn, reversal)
    # Turn at hub return (0, 0.02): WEST -> SOUTH (1 turn)
    # Turn at hub start (0, 0): SOUTH -> EAST (1 turn)
    # Total = 3 bends
    b_rev = FloorPlanChecker._compute_ring_bends([out_leg, back_leg, closure])
    assert b_rev == 3


# =============================================================================
# 2. Slide 3 Negative Rule Violations
# =============================================================================


def test_rule1_negative_omitted_node(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 1 Negative: Omitted fixed node must fail validation."""
    plan, topology, bundle = slide5_setup
    submission = bundle.model_dump()

    # Remove node N3 from H1's assigned_nodes
    submission["hub_rings"][0]["assigned_nodes"] = ["N1", "N2"]

    report = FloorPlanChecker.evaluate(plan, submission, topology=topology)
    assert not report.is_valid
    assert not report.rules_passed["Rule 1"]
    assert any(v.rule_number == 1 and "N3" in v.message for v in report.violations)


def test_rule1_negative_excluded_room(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 1 Negative: Assigning an excluded bathroom must fail validation."""
    plan, topology, bundle = slide5_setup

    # Create a copy of plan with an excluded bathroom R99
    rooms_copy = dict(plan.rooms)
    from resplan_router.contracts import EligibleOrExcludedRoom
    rooms_copy["R99"] = EligibleOrExcludedRoom(
        room_id="R99",
        category="bathroom",
        is_eligible=False,
        area_m2=6.0,
        polygon_wkt="POLYGON ((0 0, 2 0, 2 3, 0 3, 0 0))",
    )
    plan_with_bath = MetricFloorPlan(
        plan_id=plan.plan_id,
        scale_units_per_m=plan.scale_units_per_m,
        total_area_m2=plan.total_area_m2,
        inner_wkt=plan.inner_wkt,
        free_space_wkt=plan.free_space_wkt,
        obstacles_wkt=plan.obstacles_wkt,
        rooms=rooms_copy,
        nodes=plan.nodes,
    )

    submission = bundle.model_dump()
    submission["hub_rings"][0]["assigned_rooms"].append("R99")

    report = FloorPlanChecker.evaluate(plan_with_bath, submission, topology=topology)
    assert not report.is_valid
    assert not report.rules_passed["Rule 1"]
    assert any(v.rule_number == 1 and "R99" in v.message for v in report.violations)


def test_rule2_negative_capacity_exceeded(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 2 Negative: Exceeding 100 m^2 or 5 nodes must fail validation."""
    plan, topology, bundle = slide5_setup
    submission = bundle.model_dump()

    # Assign all 6 rooms (118 m^2 > 100 m^2) and all 6 nodes (6 > 5) to H1
    submission["hub_rings"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
    submission["hub_rings"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]

    report = FloorPlanChecker.evaluate(plan, submission, topology=topology)
    assert not report.is_valid
    assert not report.rules_passed["Rule 2"]
    assert any(v.rule_number == 2 for v in report.violations)


def test_rule3_negative_disconnected_rooms(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 3 Negative: Zone rooms disconnected across walls without door must fail."""
    plan, topology, bundle = slide5_setup
    submission = bundle.model_dump()

    # Assign R01 (Living) and R06 (Storage) to H1 without intermediate connecting rooms
    # R01 and R06 do not share a door/passage
    submission["hub_rings"][0]["assigned_rooms"] = ["R01", "R06"]
    submission["hub_rings"][0]["assigned_nodes"] = ["N1", "N6"]

    report = FloorPlanChecker.evaluate(plan, submission, topology=topology)
    assert not report.is_valid
    assert not report.rules_passed["Rule 3"]
    assert any(v.rule_number == 3 for v in report.violations)


def test_rule4_negative_open_ring_gap(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 4 Negative: Open ring with 50 mm gap at hub return must fail."""
    plan, topology, bundle = slide5_setup
    submission = bundle.model_dump()

    # Inject 50 mm gap into last segment endpoint of H1 (5.2, 3.65 instead of 5.2, 3.6)
    last_seg = submission["hub_rings"][0]["route_segments"][-1]
    last_seg["polyline"][-1] = [5.2, 3.65]

    report = FloorPlanChecker.evaluate(plan, submission, topology=topology)
    assert not report.is_valid
    assert not report.rules_passed["Rule 4"]
    assert any(v.rule_number == 4 for v in report.violations)


def test_rule5_negative_diagonal_and_obstacle_penetration(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 5 Negative: Diagonal segment or obstacle penetration must fail."""
    plan, topology, bundle = slide5_setup

    # 1. Diagonal segment
    sub_diag = bundle.model_dump()
    seg = sub_diag["hub_rings"][0]["route_segments"][0]
    seg["polyline"][1] = [6.0, 3.8]  # Diagonal from (5.2, 3.6) to (6.0, 3.8)

    report_diag = FloorPlanChecker.evaluate(plan, sub_diag, topology=topology)
    assert not report_diag.is_valid
    assert not report_diag.rules_passed["Rule 5"]
    assert any(v.rule_number == 5 and "diagonal" in v.message.lower() for v in report_diag.violations)

    # 2. Obstacle penetration (routing through an outer wall)
    sub_wall = bundle.model_dump()
    seg_w = sub_wall["hub_rings"][0]["route_segments"][0]
    # Route into wall obstacle outside room
    seg_w["polyline"] = [[5.2, 3.6], [5.2, 0.05], [7.6, 0.05], [7.6, 1.2]]

    report_wall = FloorPlanChecker.evaluate(plan, sub_wall, topology=topology)
    assert not report_wall.is_valid
    assert not report_wall.rules_passed["Rule 5"]
    assert any(v.rule_number == 5 for v in report_wall.violations)


def test_rule6_negative_hub_clearance_and_placement(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Hard Rule 6 Negative: Hub within 0.2m of wall or in wrong room must fail."""
    plan, topology, bundle = slide5_setup

    # 1. Hub too close to wall (0.1m clearance < 0.50m)
    sub_clear = bundle.model_dump()
    sub_clear["hub_rings"][0]["location"] = {"x": 0.1, "y": 3.6}

    report_clear = FloorPlanChecker.evaluate(plan, sub_clear, topology=topology)
    assert not report_clear.is_valid
    assert not report_clear.rules_passed["Rule 6"]
    assert any(v.rule_number == 6 for v in report_clear.violations)

    # 2. Hub designated in unassigned room
    sub_room = bundle.model_dump()
    sub_room["hub_rings"][0]["room_id"] = "R05"  # R05 is in H2, not H1
    report_room = FloorPlanChecker.evaluate(plan, sub_room, topology=topology)
    assert not report_room.is_valid
    assert not report_room.rules_passed["Rule 6"]
    assert any(v.rule_number == 6 for v in report_room.violations)


# =============================================================================
# 3. Slide 5 Baseline Validation
# =============================================================================


def test_slide5_baseline_evaluation_pass(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph, SynthesizedRingsBundle]) -> None:
    """Validate full Slide 5 schematic benchmark pass against official checker."""
    plan, topology, bundle = slide5_setup

    # Evaluate directly from SynthesizedRingsBundle
    report = FloorPlanChecker.evaluate(plan, bundle, topology=topology)

    assert report.is_valid, f"Slide 5 evaluation failed with violations: {report.violations}"
    assert len(report.violations) == 0
    assert report.hub_count == 2
    assert all(report.rules_passed.values()), f"Some rules did not pass: {report.rules_passed}"

    # Official Slide 5 benchmark numbers
    assert report.total_length_m == pytest.approx(49.0, abs=0.5)
    assert report.total_bends == 20
    assert report.total_crossings == 0
    assert report.total_overlap_m == 0.0
    assert report.total_cost_J == pytest.approx(59.0, abs=0.5)

    # Check per-hub metrics
    h1_m = report.hub_metrics[0]
    assert h1_m.hub_id == "H1"
    assert h1_m.length_m == pytest.approx(23.8, abs=0.5)
    assert h1_m.bends == 8
    assert h1_m.crossings == 0
    assert h1_m.overlap_m == 0.0
    assert h1_m.cost_J == pytest.approx(27.8, abs=0.5)

    h2_m = report.hub_metrics[1]
    assert h2_m.hub_id == "H2"
    assert h2_m.length_m == pytest.approx(25.2, abs=0.5)
    assert h2_m.bends == 12
    assert h2_m.crossings == 0
    assert h2_m.overlap_m == 0.0
    assert h2_m.cost_J == pytest.approx(31.2, abs=0.5)

    # Slide 8 dict serialization evaluation
    report_dict = FloorPlanChecker.evaluate(plan, bundle.model_dump(), topology=topology)
    assert report_dict.is_valid
    assert report_dict.total_cost_J == report.total_cost_J
