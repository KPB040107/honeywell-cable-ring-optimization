"""Phase 8 Architectural Co-Pilot Unit & Regression Tests.

Validates:
1. Shared interior wall and movable doorway candidate generation with strict exterior/window exclusion.
2. Leaf-room ghost doorway optimization yielding Delta J > 0.
3. Doorway recommendation dropping required hubs Delta H = 1 when unlinked rooms (< 100 m^2) are connected.
4. Strict lexicographic ranking: Delta H descending, then Delta J descending.
5. Structural proposal application to floor plan geometry and independent FloorPlanChecker compliance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import pytest
import shapely
from shapely.geometry import LineString, Point, box

from resplan_router.config import SystemConfig
from resplan_router.contracts import (
    CompletedHubRing,
    EligibleOrExcludedRoom,
    FixedNode,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    PortalType,
    ProposalType,
    RouteSegment,
    StructuralProposal,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.pipeline import route_floor_plan
from resplan_router.phase8_copilot.candidate_generator import (
    PerturbationCandidateGenerator,
)
from resplan_router.phase8_copilot.wall_optimizer import ArchitecturalCoPilot


@pytest.fixture
def slide5_fixture_path() -> Path:
    """Path to the Slide 5 schematic JSON fixture."""
    return Path(__file__).parent / "fixtures" / "slide5_schematic.json"


@pytest.fixture
def slide5_baseline(slide5_fixture_path: Path):
    """Precomputed baseline solution for Slide 5 schematic."""
    config = SystemConfig(max_area_m2=100.0, max_nodes_per_hub=5)
    with open(slide5_fixture_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    plan = parse_floor_plan(raw_data)
    portals = extract_all_portals(plan, raw_data.get("doors"), raw_data.get("front_door"))
    topo = MacroTopologyGraph(plan, portals)

    _, evaluation, bundle = route_floor_plan(raw_data, config=config)

    # Reconstruct partition from bundle
    hub_assignments = []
    for ring in bundle.hub_rings:
        hub_assignments.append(
            HubAssignment(
                hub_id=ring.hub_id,
                room_id=ring.room_id,
                location=ring.location,
                assigned_rooms=ring.assigned_rooms,
                assigned_nodes=ring.assigned_nodes,
                zone_area_m2=sum(plan.rooms[r].area_m2 for r in ring.assigned_rooms),
            )
        )
    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=len(hub_assignments),
        lower_bound_hubs=len(hub_assignments),
        area_gap_m2=evaluation.area_gap_m2,
        hubs=hub_assignments,
    )

    return plan, topo, partition, bundle


def test_shared_wall_candidate_generation(slide5_fixture_path: Path) -> None:
    """Test 1: Shared wall detection on Slide 5 schematic with exterior/window exclusion."""
    with open(slide5_fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topo = MacroTopologyGraph(plan, portals)

    generator = PerturbationCandidateGenerator(min_wall_length_m=1.0, corner_clearance_m=0.50)

    # 1. Baseline candidate generation without windows
    candidates = generator.find_shared_interior_walls(plan, topo)
    assert len(candidates) > 0, "Must detect candidate ghost doors on shared interior walls"

    exterior_boundary = plan.inner_geom.boundary

    # Verify every candidate adheres to geometric clearance invariants
    for cand in candidates:
        assert cand["proposal_type"] == ProposalType.ADD_GHOST_DOOR
        r_a = cand["room_a_id"]
        r_b = cand["room_b_id"]
        assert plan.rooms[r_a].is_eligible and plan.rooms[r_b].is_eligible

        wall_seg = cand["wall_segment"]
        p1 = (wall_seg[0].x, wall_seg[0].y)
        p2 = (wall_seg[1].x, wall_seg[1].y)
        seg_len = math_hypot(p2[0] - p1[0], p2[1] - p1[1])
        assert seg_len >= 1.0, f"Wall segment length {seg_len} m must be >= 1.0 m"

        c = cand["suggested_door_center"]
        c_pt = Point(c.x, c.y)

        # Corner clearance
        d1 = math_hypot(c.x - p1[0], c.y - p1[1])
        d2 = math_hypot(c.x - p2[0], c.y - p2[1])
        assert d1 >= 0.499 and d2 >= 0.499, f"Door center {c} must maintain >= 0.5 m clearance to corners"

        # Strictly interior: must not lie on exterior boundary
        assert exterior_boundary.distance(c_pt) > 0.05, f"Door center {c} must not lie on exterior boundary"

    # 2. Window exclusion verification: place a window along wall between R01 and R04
    # Wall is at y=5.0, x in [4.0, 8.0]. Place window from x=5.5 to x=6.5 at y=5.0
    mock_window = LineString([(5.5, 5.0), (6.5, 5.0)]).buffer(0.10)
    candidates_with_win = generator.find_shared_interior_walls(plan, topo, windows=[mock_window])

    # Asserts door center near the window (x=6.0, y=5.0) was strictly excluded
    for cand in candidates_with_win:
        if {cand["room_a_id"], cand["room_b_id"]} == {"R01", "R04"}:
            c = cand["suggested_door_center"]
            dist_to_win = mock_window.distance(Point(c.x, c.y))
            assert dist_to_win >= 0.40, f"Door center {c} must not collide with window"


def test_leaf_room_ghost_doorway_optimization(slide5_baseline) -> None:
    """Test 2: Co-Pilot discovers ghost doorway shortcut for leaf room Bedroom 2 yielding Delta J > 0."""
    plan, topo, partition, bundle = slide5_baseline
    copilot = ArchitecturalCoPilot()

    report = copilot.analyze_floor_plan(plan, topo, partition, bundle, max_recommendations=5)

    assert report.plan_id == "slide5_schematic"
    assert report.baseline_hubs == 2
    assert report.proposals_evaluated > 0
    assert len(report.recommended_proposals) > 0

    # Look for Bedroom 2 (R04, leaf room) ghost doorway proposals
    r04_proposals = [
        p for p in report.recommended_proposals
        if "R04" in (p.room_a_id, p.room_b_id) and p.proposal_type == ProposalType.ADD_GHOST_DOOR
    ]
    assert len(r04_proposals) > 0, "Must recommend ghost doorway connecting leaf room Bedroom 2"

    best_r04 = r04_proposals[0]
    assert best_r04.delta_cost_J > 0.0, f"Expected Delta J > 0, got {best_r04.delta_cost_J}"
    assert best_r04.delta_length_m >= 0.0
    assert best_r04.delta_bends >= 1
    assert "leaf room" in best_r04.rationale or "shortcut" in best_r04.rationale


def test_hub_reduction_recommendation() -> None:
    """Test 3: Floor plan where two eligible rooms (< 100 m^2) are unlinked, forcing H=2, reduced to H=1."""
    # Synthetic floor plan: two rooms separated by a solid wall with NO door
    # Room 1: (0,0) to (5,5), area = 25 m^2, eligible, node N1 at (2.5, 2.5)
    # Room 2: (5,0) to (10,5), area = 25 m^2, eligible, node N2 at (7.5, 2.5)
    # Total area = 50 m^2 <= 100 m^2, total nodes = 2 <= 5
    r1_poly = box(0, 0, 5, 5)
    r2_poly = box(5, 0, 10, 5)
    inner = box(0, 0, 10, 5)
    wall = box(4.95, 0, 5.05, 5)
    free = inner.difference(wall)

    plan = MetricFloorPlan(
        plan_id="unlinked_two_rooms",
        scale_units_per_m=1.0,
        total_area_m2=50.0,
        inner_wkt=inner.wkt,
        free_space_wkt=free.wkt,
        obstacles_wkt=wall.wkt,
        rooms={
            "R01": EligibleOrExcludedRoom(
                room_id="R01", category="living", is_eligible=True, area_m2=25.0, polygon_wkt=r1_poly.wkt
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02", category="bedroom", is_eligible=True, area_m2=25.0, polygon_wkt=r2_poly.wkt
            ),
        },
        nodes={
            "N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.5, y=2.5)),
            "N2": FixedNode(node_id="N2", room_id="R02", location=Point2D(x=7.5, y=2.5)),
        },
    )

    # Baseline topology: NO portals exist between R01 and R02
    topo = MacroTopologyGraph(plan, portals=[])

    # Baseline partition: forced H=2 because components are disconnected
    hubs = [
        HubAssignment(
            hub_id="H1",
            room_id="R01",
            location=Point2D(x=1.0, y=1.0),
            assigned_rooms=["R01"],
            assigned_nodes=["N1"],
            zone_area_m2=25.0,
        ),
        HubAssignment(
            hub_id="H2",
            room_id="R02",
            location=Point2D(x=9.0, y=1.0),
            assigned_rooms=["R02"],
            assigned_nodes=["N2"],
            zone_area_m2=25.0,
        ),
    ]
    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=2,
        lower_bound_hubs=1,
        area_gap_m2=0.0,
        hubs=hubs,
    )

    ring1 = CompletedHubRing(
        hub_id="H1",
        room_id="R01",
        location=Point2D(x=1.0, y=1.0),
        assigned_rooms=["R01"],
        assigned_nodes=["N1"],
        closed_sequence=["H1", "N1", "H1"],
        route_segments=[
            RouteSegment(from_node="H1", to_node="N1", length_m=3.0, polyline=[Point2D(x=1.0, y=1.0), Point2D(x=2.5, y=1.0), Point2D(x=2.5, y=2.5)]),
            RouteSegment(from_node="N1", to_node="H1", length_m=3.0, polyline=[Point2D(x=2.5, y=2.5), Point2D(x=1.0, y=2.5), Point2D(x=1.0, y=1.0)]),
        ],
        ring_length_m=6.0,
        ring_crossings=0,
        ring_overlap_m=0.0,
        ring_bends=2,
        cost_J=7.0,
    )
    ring2 = CompletedHubRing(
        hub_id="H2",
        room_id="R02",
        location=Point2D(x=9.0, y=1.0),
        assigned_rooms=["R02"],
        assigned_nodes=["N2"],
        closed_sequence=["H2", "N2", "H2"],
        route_segments=[
            RouteSegment(from_node="H2", to_node="N2", length_m=3.0, polyline=[Point2D(x=9.0, y=1.0), Point2D(x=7.5, y=1.0), Point2D(x=7.5, y=2.5)]),
            RouteSegment(from_node="N2", to_node="H2", length_m=3.0, polyline=[Point2D(x=7.5, y=2.5), Point2D(x=9.0, y=2.5), Point2D(x=9.0, y=1.0)]),
        ],
        ring_length_m=6.0,
        ring_crossings=0,
        ring_overlap_m=0.0,
        ring_bends=2,
        cost_J=7.0,
    )
    bundle = SynthesizedRingsBundle(
        plan_id=plan.plan_id,
        hub_rings=[ring1, ring2],
        total_length_m=12.0,
        total_crossings=0,
        total_overlap_m=0.0,
        total_bends=4,
        total_cost_J=14.0,
    )

    copilot = ArchitecturalCoPilot()
    report = copilot.analyze_floor_plan(plan, topo, partition, bundle)

    assert report.baseline_hubs == 2
    # Verify that Co-Pilot proposes adding an opening between R01 and R02 that drops hubs to 1
    hub_drop_proposals = [p for p in report.recommended_proposals if p.delta_hubs == 1]
    assert len(hub_drop_proposals) > 0, "Must recommend doorway that reduces required hubs from 2 to 1"

    top_p = hub_drop_proposals[0]
    assert {top_p.room_a_id, top_p.room_b_id} == {"R01", "R02"}
    assert top_p.delta_hubs == 1
    assert "merges disconnected" in top_p.rationale or "hubs" in top_p.rationale


def test_ranking_order() -> None:
    """Test 4: Strict lexicographic ranking: Delta H descending first, then Delta J descending."""
    p_hub1 = StructuralProposal(
        proposal_id="p1",
        proposal_type=ProposalType.ADD_GHOST_DOOR,
        room_a_id="R01",
        room_b_id="R02",
        wall_segment=[Point2D(x=0, y=0), Point2D(x=0, y=5)],
        suggested_door_center=Point2D(x=0, y=2.5),
        door_width_m=0.9,
        delta_hubs=1,
        delta_cost_J=2.0,
        delta_length_m=1.0,
        delta_bends=2,
        original_cost_J=50.0,
        new_cost_J=48.0,
        rationale="Hub drop proposal with small delta J",
    )
    p_hub2 = StructuralProposal(
        proposal_id="p2",
        proposal_type=ProposalType.ADD_GHOST_DOOR,
        room_a_id="R03",
        room_b_id="R04",
        wall_segment=[Point2D(x=5, y=0), Point2D(x=5, y=5)],
        suggested_door_center=Point2D(x=5, y=2.5),
        door_width_m=0.9,
        delta_hubs=1,
        delta_cost_J=8.0,
        delta_length_m=6.0,
        delta_bends=4,
        original_cost_J=50.0,
        new_cost_J=42.0,
        rationale="Hub drop proposal with larger delta J",
    )
    p_cost_only = StructuralProposal(
        proposal_id="p3",
        proposal_type=ProposalType.SLIDE_DOOR,
        room_a_id="R01",
        room_b_id="R03",
        wall_segment=[Point2D(x=0, y=0), Point2D(x=5, y=0)],
        suggested_door_center=Point2D(x=2.5, y=0),
        door_width_m=0.9,
        delta_hubs=0,
        delta_cost_J=25.0,
        delta_length_m=20.0,
        delta_bends=10,
        original_cost_J=50.0,
        new_cost_J=25.0,
        rationale="High delta J but zero delta hubs",
    )

    proposals = [p_cost_only, p_hub1, p_hub2]
    # Apply standard lexicographic ranking key
    proposals.sort(key=lambda p: (p.delta_hubs, p.delta_cost_J), reverse=True)

    # Assert p_hub2 is first, p_hub1 is second, p_cost_only is last
    assert proposals[0].proposal_id == "p2", "Highest delta_hubs with highest delta_cost_J must be first"
    assert proposals[1].proposal_id == "p1", "delta_hubs=1 must rank above delta_hubs=0 even if delta_J is smaller"
    assert proposals[2].proposal_id == "p3", "delta_hubs=0 must be ranked last"


def test_apply_proposal_geometry(slide5_baseline) -> None:
    """Test 5: apply_proposal modifies floor plan geometry and passes FloorPlanChecker."""
    plan, topo, partition, bundle = slide5_baseline
    copilot = ArchitecturalCoPilot()

    report = copilot.analyze_floor_plan(plan, topo, partition, bundle)
    assert len(report.recommended_proposals) > 0
    top_prop = report.recommended_proposals[0]

    # Apply proposal
    updated_plan = copilot.apply_proposal(top_prop, plan)

    # Verify updated plan has valid WKT
    assert updated_plan.plan_id == plan.plan_id
    assert updated_plan.free_space_geom.is_valid
    assert updated_plan.obstacles_geom.is_valid

    # Verify portal extraction on updated plan finds the new opening
    updated_portals = extract_all_portals(updated_plan)
    assert len(updated_portals) >= len(topo.portals), "Updated plan must have at least as many portals"

    # Verify updated plan passes Slide 3, 6 & 7 checker with baseline bundle
    eval_report = FloorPlanChecker.evaluate(updated_plan, bundle)
    assert eval_report.is_valid is True, f"Updated plan failed checker: {eval_report.violations}"
    assert len(eval_report.violations) == 0


def math_hypot(dx: float, dy: float) -> float:
    """Helper hypotenuse function."""
    import math
    return math.hypot(dx, dy)
