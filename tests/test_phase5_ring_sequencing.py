"""Phase 5 Direction-Aware Closed-Ring Sequencing & Route Synthesis Verification Suite.

Implements the official verification gates from v2.md Section 5.5.3:
- Test 5.1 (test_slide5_ring_metrics):
  Assert H1 visits H1 -> N1 -> N3 -> N2 -> H1 with L approx 23.8m, B = 8, O = 0.0m, X = 0, J_1 = 27.8.
  Assert H2 visits H2 -> N4 -> N5 -> N6 -> H2 with L approx 25.2m, B = 12, O = 0.0m, X = 0, J_2 = 31.2.
  Assert total cable length L = 49.0m +/- 0.5m, bends B = 20, crossings X = 0, overlap O = 0.0m, J = 59.0 +/- 0.5.
- Test 5.2 (test_single_door_parallel_runs_zero_overlap):
  Verify on single-door leaf rooms (Bedroom 2, Storage) that incoming and outgoing polylines stay > 1mm apart
  everywhere except at the fixed node point itself, yielding O = 0.0m and X = 0.
- Test 5.3 (test_node_and_hub_turn_counting):
  Verify that turns at fixed nodes and at the hub wrap-around connection are counted in total ring bends B.
- Test 5.4 (test_phase1_to_phase5_regression):
  Comprehensive cross-phase pipeline integrity check from Phase 1 through Phase 5.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import pytest
from shapely.geometry import LineString, Point

from resplan_router.contracts import (
    CompassDirection,
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
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase4_partitioning.hub_candidates import generate_all_room_hub_candidates
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner
from resplan_router.phase5_ring_sequencing.exact_ring_tsp import DirectionalRingSolver
from resplan_router.phase5_ring_sequencing.loop_synthesizer import (
    LoopSynthesizer,
    compute_crossings,
    compute_overlap,
    count_ring_bends,
)


@pytest.fixture
def slide5_setup() -> tuple[MetricFloorPlan, PortalTopologyGraph]:
    """Fixture providing Slide 5 metric floor plan and topology graph."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    return plan, topology


def test_slide5_ring_metrics(slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph]) -> None:
    """Test 5.1: Validate exact Slide 5 benchmark ring metrics (L=49.0m, B=20, X=0, O=0, J=59.0).

    Target benchmarks from Slide 5 & v2.md Section 5.5.3:
    - Hub H1 (Living, Bedrooms 1 & 3): 71 m^2, 3 nodes, L = 23.8 m, B = 8, O = 0.0 m, X = 0, J_1 = 27.8
    - Hub H2 (Bedroom 2, Kitchen, Storage): 47 m^2, 3 nodes, L = 25.2 m, B = 12, O = 0.0 m, X = 0, J_2 = 31.2
    - Combined Total: L = 49.0 m (+/- 0.5m), B = 20, X = 0, O = 0.0 m, J = 59.0 (+/- 0.5)
    """
    plan, topology = slide5_setup

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

    # 1. Synthesize Ring 1 (H1)
    seq1 = ["H1", "N1", "N3", "N2", "H1"]
    ring1 = synthesizer.synthesize_ring(h1, seq1, plan, topology, router, grid)

    assert ring1.hub_id == "H1"
    assert ring1.closed_sequence == seq1
    assert ring1.ring_length_m == pytest.approx(23.8, abs=0.5)
    assert ring1.ring_bends == 8
    assert ring1.ring_overlap_m == 0.0
    assert ring1.ring_crossings == 0
    assert ring1.cost_J == pytest.approx(27.8, abs=0.5)

    # 2. Synthesize Ring 2 (H2)
    seq2 = ["H2", "N4", "N5", "N6", "H2"]
    ring2 = synthesizer.synthesize_ring(h2, seq2, plan, topology, router, grid)

    assert ring2.hub_id == "H2"
    assert ring2.closed_sequence == seq2
    assert ring2.ring_length_m == pytest.approx(25.2, abs=0.5)
    assert ring2.ring_bends == 12
    assert ring2.ring_overlap_m == 0.0
    assert ring2.ring_crossings == 0
    assert ring2.cost_J == pytest.approx(31.2, abs=0.5)

    # 3. Plan-wide combined synthesis
    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=2,
        lower_bound_hubs=2,
        area_gap_m2=24.0,
        hubs=[h1, h2],
    )
    bundle = synthesizer.synthesize_all_rings(partition, plan, topology, router, grid)

    assert bundle.total_length_m == pytest.approx(49.0, abs=0.5)
    assert bundle.total_bends == 20
    assert bundle.total_crossings == 0
    assert bundle.total_overlap_m == 0.0
    assert bundle.total_cost_J == pytest.approx(59.0, abs=0.5)


def test_single_door_parallel_runs_zero_overlap(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Test 5.2: Verify zero overlap (O = 0) and separation > 1mm on single-door leaf rooms.

    On Slide 5 schematic, Bedroom 2 (R04) and Storage (R06) are single-door leaf rooms.
    Verify that incoming and outgoing polylines stay > 1mm apart everywhere except at the fixed node.
    """
    plan, topology = slide5_setup

    assert "R04" in topology.leaf_room_ids
    assert "R06" in topology.leaf_room_ids

    h2_pt = Point2D(x=11.4, y=7.0)
    h2 = HubAssignment(
        hub_id="H2",
        room_id="R05",
        location=h2_pt,
        assigned_rooms=["R04", "R05", "R06"],
        assigned_nodes=["N4", "N5", "N6"],
        zone_area_m2=47.0,
    )
    grid = build_hanan_grid(plan, topology, extra_points=[h2_pt])
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()

    seq2 = ["H2", "N4", "N5", "N6", "H2"]
    ring2 = synthesizer.synthesize_ring(h2, seq2, plan, topology, router, grid)

    # Leg 1 (H2 -> N4, in) and Leg 2 (N4 -> N5, out) connect to Bedroom 2 (N4)
    leg1 = ring2.route_segments[0]
    leg2 = ring2.route_segments[1]

    # Verify Bedroom 2 portal P04 separation
    p04 = next(p for p in topology.portals if p.portal_id == "P04")
    lane_sep_p04 = math.hypot(
        p04.lane_a_point.x - p04.lane_b_point.x,
        p04.lane_a_point.y - p04.lane_b_point.y,
    )
    assert lane_sep_p04 >= 0.02, f"P04 doorway lane separation {lane_sep_p04:.4f}m must be >= 20mm"

    # Verify that non-terminal segments between leg1 and leg2 are > 1mm apart
    node_n4 = Point(6.0, 6.95)
    for i in range(len(leg1.polyline) - 1):
        s1 = LineString([(leg1.polyline[i].x, leg1.polyline[i].y), (leg1.polyline[i + 1].x, leg1.polyline[i + 1].y)])
        for j in range(len(leg2.polyline) - 1):
            s2 = LineString([(leg2.polyline[j].x, leg2.polyline[j].y), (leg2.polyline[j + 1].x, leg2.polyline[j + 1].y)])

            # If neither segment contains the terminal node N4
            if s1.distance(node_n4) > 0.01 and s2.distance(node_n4) > 0.01:
                dist = s1.distance(s2)
                assert dist > 0.001, (
                    f"Collinear or colliding segments detected in Bedroom 2: distance {dist:.5f}m <= 1mm"
                )

    # Leg 3 (N5 -> N6, in) and Leg 4 (N6 -> H2, out) connect to Storage (N6)
    leg3 = ring2.route_segments[2]
    leg4 = ring2.route_segments[3]

    p05 = next(p for p in topology.portals if p.portal_id == "P05")
    lane_sep_p05 = math.hypot(
        p05.lane_a_point.x - p05.lane_b_point.x,
        p05.lane_a_point.y - p05.lane_b_point.y,
    )
    assert lane_sep_p05 >= 0.02, f"P05 doorway lane separation {lane_sep_p05:.4f}m must be >= 20mm"

    # Verify that non-terminal segments between leg3 and leg4 are > 1mm apart
    node_n6 = Point(12.5, 2.4)
    for i in range(len(leg3.polyline) - 1):
        s3 = LineString([(leg3.polyline[i].x, leg3.polyline[i].y), (leg3.polyline[i + 1].x, leg3.polyline[i + 1].y)])
        for j in range(len(leg4.polyline) - 1):
            s4 = LineString([(leg4.polyline[j].x, leg4.polyline[j].y), (leg4.polyline[j + 1].x, leg4.polyline[j + 1].y)])

            if s3.distance(node_n6) > 0.01 and s4.distance(node_n6) > 0.01:
                dist = s3.distance(s4)
                assert dist > 0.001, (
                    f"Collinear or colliding segments detected in Storage: distance {dist:.5f}m <= 1mm"
                )

    # Overall ring overlap and crossings must be strictly 0
    assert ring2.ring_overlap_m == 0.0
    assert ring2.ring_crossings == 0


def test_node_and_hub_turn_counting() -> None:
    """Test 5.3: Verify accurate turn counting at nodes and hub wrap-around.

    In accordance with Slide 7:
    - Straight pass through a node: B = 0 (+0.0 cost)
    - 90-degree turn at a node: B = 1 (+0.5 cost)
    - 180-degree reversal at a node: B = 1 (+0.5 cost)
    - Turn at the hub wrap-around: B = 1 (+0.5 cost)
    """
    # Create a 2-node triangular loop: H1 -> N1 -> N2 -> H1
    # H1 at (0, 0), N1 at (4, 0), N2 at (4, 3)
    # Leg 1: (0, 0) -> (4, 0) departing EAST, arriving EAST at N1 (0 int bends)
    # Leg 2: (4, 0) -> (4, 3) departing NORTH, arriving NORTH at N2 (0 int bends)
    # Leg 3: (4, 3) -> (0, 3) -> (0, 0) departing WEST, turning SOUTH at (0, 3), arriving SOUTH at H1 (1 int bend)
    seg1 = RouteSegment(
        from_node="H1",
        to_node="N1",
        length_m=4.0,
        polyline=[Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)],
    )
    seg2 = RouteSegment(
        from_node="N1",
        to_node="N2",
        length_m=3.0,
        polyline=[Point2D(x=4.0, y=0.0), Point2D(x=4.0, y=3.0)],
    )
    seg3 = RouteSegment(
        from_node="N2",
        to_node="H1",
        length_m=7.0,
        polyline=[Point2D(x=4.0, y=3.0), Point2D(x=0.0, y=3.0), Point2D(x=0.0, y=0.0)],
    )

    sequence = ["H1", "N1", "N2", "H1"]
    segments = [seg1, seg2, seg3]

    total_bends = count_ring_bends(sequence, segments)

    # Accounting:
    # 1. Internal turns: Seg 3 turns at (0, 3) -> 1 bend
    # 2. At N1: arrives EAST, departs NORTH -> 1 bend
    # 3. At N2: arrives NORTH, departs WEST -> 1 bend
    # 4. At H1 (wrap-around): arrives SOUTH, initial departs EAST -> 1 bend
    # Total = 1 + 1 + 1 + 1 = 4 bends
    assert total_bends == 4, f"Expected 4 total bends, got {total_bends}"

    # Overlap and crossings
    assert compute_overlap(segments) == 0.0
    assert compute_crossings(segments, [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0), Point2D(x=4.0, y=3.0)]) == 0


def test_phase1_to_phase5_regression(
    slide5_setup: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Test 5.4: Comprehensive cross-phase regression from Phase 1 through Phase 5.

    Executes full pipeline:
    - Phase 1: MetricFloorPlan ingestion
    - Phase 2: Portal extraction & topology dual graph
    - Phase 3: Hanan grid & directional A* micro-routing
    - Phase 4: Lexicographic hub placement & contiguous zone partitioning
    - Phase 5: Closed-ring sequencing & conflict-free polyline synthesis
    """
    plan, topology = slide5_setup

    # Phase 1 Checks
    assert plan.plan_id == "slide5_schematic"
    assert len(plan.rooms) == 7
    assert len(plan.nodes) == 6

    # Phase 2 Checks
    assert len(topology.portals) >= 5
    assert "R04" in topology.leaf_room_ids
    assert "R06" in topology.leaf_room_ids

    # Phase 3 Checks
    grid = build_hanan_grid(plan, topology)
    assert grid.vertex_count > 0
    assert grid.edge_count > 0

    tensor = PairwiseCostTensor.build_from_plan(plan, topology, grid=grid, include_portals=False)
    candidates = generate_all_room_hub_candidates(plan, topology=topology)

    # Phase 4 Checks
    partition = ZonePartitioner.solve_partition(
        plan=plan,
        topology=topology,
        cost_tensor=tensor,
        candidates=candidates,
    )
    assert partition.hub_count == 2
    assert len(partition.hubs) == 2

    # Phase 5 Checks
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()

    bundle = synthesizer.synthesize_all_rings(
        partition=partition,
        plan=plan,
        topology=topology,
        router=router,
        grid=grid,
        cost_tensor=tensor,
    )

    assert bundle.plan_id == "slide5_schematic"
    assert len(bundle.hub_rings) == 2

    for ring in bundle.hub_rings:
        # Hard Rule 4: Closed ring visits every assigned node once and returns to hub
        assert ring.closed_sequence[0] == ring.hub_id
        assert ring.closed_sequence[-1] == ring.hub_id
        assert set(ring.closed_sequence[1:-1]) == set(ring.assigned_nodes)
        assert len(ring.closed_sequence[1:-1]) == len(ring.assigned_nodes)

        # Hard Rule 5: Axis-aligned polyline segments
        for seg in ring.route_segments:
            for k in range(len(seg.polyline) - 1):
                p1 = seg.polyline[k]
                p2 = seg.polyline[k + 1]
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                assert (dx < 1e-4 and dy > 1e-4) or (dy < 1e-4 and dx > 1e-4), (
                    f"Non-axis-aligned segment on {seg.from_node}->{seg.to_node}: p1={p1.coords}, p2={p2.coords}"
                )

        # Zero overlap and zero crossings
        assert ring.ring_overlap_m == 0.0
        assert ring.ring_crossings == 0

    # Plan-wide totals check
    assert bundle.total_crossings == 0
    assert bundle.total_overlap_m == 0.0
    assert bundle.total_cost_J == pytest.approx(
        bundle.total_length_m + 0.5 * bundle.total_bends, abs=1e-4
    )
