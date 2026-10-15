"""Official Phase 2 verification suite for dual graph topology and parallel doorway lanes.

Validates:
- Test 2.1 (test_slide5_door_connectivity): Slide 5 dual graph connectivity and single-door leaf rooms.
- Test 2.2 (test_window_wall_entrance_rejection): Zero edges across walls, windows, and front doors.
- Test 2.3 (test_parallel_door_lanes_exceed_1mm_tolerance): >= 20mm lateral separation inside free space.
- Test 2.4 (test_phase1_phase2_regression): Phase 1 scale conversion and node clearance regression.
- Test 2.5 (test_topology_error_handling): TopologyError raised on disconnected plans when required.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import pytest
import shapely
from shapely.geometry import box, Point, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    DoorOrPassagePortal,
    EligibleOrExcludedRoom,
    MetricFloorPlan,
    Point2D,
    PortalType,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph, TopologyError
from resplan_router.phase2_topology.portal_extractor import (
    extract_all_portals,
    extract_door_portals,
)


@pytest.fixture
def slide5_data_and_plan() -> tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]]:
    """Fixture providing parsed Slide 5 floor plan and extracted portals."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    return data, plan, portals


# ---------------------------------------------------------------------------
# Test 2.1: Slide 5 Benchmark Topology Connectivity & Single-Door Leaf Rooms
# ---------------------------------------------------------------------------
def test_slide5_door_connectivity(
    slide5_data_and_plan: tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]],
) -> None:
    """Verify G_macro dual graph connectivity and leaf room detection on Slide 5 schematic.

    Per v2.md Section 5.2.3 (Test 2.1):
    - Connects Living - Bedroom 3
    - Connects Bedroom 3 - Bedroom 1
    - Connects Living - Bedroom 1
    - Connects Bedroom 2 - Kitchen
    - Connects Kitchen - Storage
    - Bedroom 2 and Storage identified as degree-1 single-door leaf rooms.
    """
    _, plan, portals = slide5_data_and_plan

    # Build dual graph
    mtg = MacroTopologyGraph(plan, portals)
    top = mtg.topology
    assert top is not None

    # Map category to room_ids for clear semantic assertions
    rooms_by_cat: dict[str, list[str]] = {}
    for r_id, r in plan.rooms.items():
        rooms_by_cat.setdefault(r.category, []).append(r_id)

    # In slide5_schematic.json:
    # R01: living, R02: bedroom (1), R03: bedroom (3), R04: bedroom (2), R05: kitchen, R06: storage
    living_id = "R01"
    bed1_id = "R02"
    bed3_id = "R03"
    bed2_id = "R04"
    kitchen_id = "R05"
    storage_id = "R06"

    # Assert G_macro connects the specified pairs
    assert mtg.graph.has_edge(living_id, bed3_id), "Living and Bedroom 3 must be connected"
    assert mtg.graph.has_edge(bed3_id, bed1_id), "Bedroom 3 and Bedroom 1 must be connected"
    assert mtg.graph.has_edge(living_id, bed1_id), "Living and Bedroom 1 must be connected"
    assert mtg.graph.has_edge(bed2_id, kitchen_id), "Bedroom 2 and Kitchen must be connected"
    assert mtg.graph.has_edge(kitchen_id, storage_id), "Kitchen and Storage must be connected"

    # Verify edge portal payloads
    p_liv_bed3 = mtg.get_edge_portal(living_id, bed3_id)
    assert p_liv_bed3 is not None
    assert p_liv_bed3.portal_type == PortalType.DOOR

    # Verify leaf rooms: eligible rooms with degree 1 in eligible subgraph
    assert set(top.leaf_room_ids) == {bed2_id, storage_id}, (
        f"Expected leaf rooms {{'{bed2_id}', '{storage_id}'}}, got {top.leaf_room_ids}"
    )

    # Assert degree-1 invariant directly on NetworkX subgraph
    subgraph = mtg.graph.subgraph(top.eligible_room_ids)
    assert subgraph.degree(bed2_id) == 1, "Bedroom 2 must have degree 1 in eligible subgraph"
    assert subgraph.degree(storage_id) == 1, "Storage must have degree 1 in eligible subgraph"
    assert subgraph.degree(living_id) == 2, "Living must have degree 2"
    assert subgraph.degree(bed1_id) == 2, "Bedroom 1 must have degree 2"
    assert subgraph.degree(bed3_id) == 2, "Bedroom 3 must have degree 2"
    assert subgraph.degree(kitchen_id) == 2, "Kitchen must have degree 2 in eligible subgraph"

    assert mtg.is_leaf_room(bed2_id) is True
    assert mtg.is_leaf_room(storage_id) is True
    assert mtg.is_leaf_room(living_id) is False


# ---------------------------------------------------------------------------
# Test 2.2: Window, Wall, and Entrance Rejection
# ---------------------------------------------------------------------------
def test_window_wall_entrance_rejection(
    slide5_data_and_plan: tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]],
) -> None:
    """Verify rooms sharing only solid wall or window have zero direct edges in G_macro,
    and the building entrance (front_door) creates zero interior edges.
    """
    _, plan, portals = slide5_data_and_plan
    mtg = MacroTopologyGraph(plan, portals)

    # 1. Slide 5 rooms sharing solid walls must NOT have edges
    # Living (R01) and Storage (R06) share a solid wall at x=110
    assert not mtg.graph.has_edge("R01", "R06"), "Solid wall between Living and Storage must not be an edge"
    # Living (R01) and Bedroom 2 (R04) share a solid wall at y=50
    assert not mtg.graph.has_edge("R01", "R04"), "Solid wall between Living and Bedroom 2 must not be an edge"
    # Living (R01) and Kitchen (R05) share a solid wall at y=50
    assert not mtg.graph.has_edge("R01", "R05"), "Solid wall between Living and Kitchen must not be an edge"
    # Bedroom 1 (R02) and Bedroom 2 (R04) share a solid wall at x=40
    assert not mtg.graph.has_edge("R02", "R04"), "Solid wall between Bedroom 1 and Bedroom 2 must not be an edge"
    # Storage (R06) and Bathroom (R07) share a solid wall at y=50
    assert not mtg.graph.has_edge("R06", "R07"), "Solid wall between Storage and Bathroom must not be an edge"

    # 2. Entrance (front_door) verification: zero portals link to exterior front door
    for p in portals:
        assert p.portal_type in (PortalType.DOOR, PortalType.OPEN_PASSAGE)
        # Both endpoints must be interior room IDs
        assert p.room1_id in plan.rooms
        assert p.room2_id in plan.rooms

    # 3. Synthetic test: room boundary with window only produces zero edges
    inner = box(0.0, 0.0, 10.0, 5.0)
    r1 = box(0.0, 0.0, 5.0, 5.0)
    r2 = box(5.0, 0.0, 10.0, 5.0)
    # Continuous wall with window gap (no free space passage)
    wall1 = box(4.95, 0.0, 5.05, 2.0)
    wall2 = box(4.95, 3.0, 5.05, 5.0)
    window = box(4.95, 2.0, 5.05, 3.0)
    obstacles = shapely.unary_union([wall1, wall2, window])
    free_space = inner.difference(obstacles)

    synthetic_plan = MetricFloorPlan(
        plan_id="window_plan",
        scale_units_per_m=1.0,
        total_area_m2=50.0,
        inner_wkt=inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms={
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category="living",
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt=r1.wkt,
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category="bedroom",
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt=r2.wkt,
            ),
        },
        nodes={},
    )
    # Portal extraction with zero doors
    synthetic_portals = extract_all_portals(synthetic_plan, raw_doors=[])
    # Window must not create an open passage
    assert len(synthetic_portals) == 0

    synth_mtg = MacroTopologyGraph(synthetic_plan, synthetic_portals)
    assert not synth_mtg.graph.has_edge("R01", "R02"), "Window must not form a graph edge"


# ---------------------------------------------------------------------------
# Test 2.3: Parallel Door Lanes Exceed 1mm Checker Tolerance (>= 20mm)
# ---------------------------------------------------------------------------
def test_parallel_door_lanes_exceed_1mm_tolerance(
    slide5_data_and_plan: tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]],
) -> None:
    r"""Verify that every extracted portal generates parallel lanes A and B that:
    1. Lie strictly inside routable free space F = inner \ (wall U window).
    2. Maintain lateral separation >= 0.020m (20mm = 20x the 1mm overlap tolerance).
    3. Pass MacroTopologyGraph.validate_parallel_lane_clearance.
    """
    _, plan, portals = slide5_data_and_plan
    assert len(portals) > 0, "Slide 5 must have extracted portals"

    tolerance_1mm_m = 0.001  # 1 mm checker threshold for Overlap O
    min_separation_m = 0.020  # 20 mm requirement

    for portal in portals:
        # Check lane retrieval
        center, lane_a, lane_b = MacroTopologyGraph.get_portal_lanes(portal)
        assert center == portal.center_point
        assert lane_a == portal.lane_a_point
        assert lane_b == portal.lane_b_point

        # Calculate Euclidean separation between lane A and lane B
        dx = lane_b.x - lane_a.x
        dy = lane_b.y - lane_a.y
        sep = math.hypot(dx, dy)

        # Separation must exceed 20mm (and thus strictly exceed 1mm tolerance by >= 20x)
        assert sep >= min_separation_m, (
            f"Portal {portal.portal_id} separation {sep:.4f}m is below {min_separation_m}m"
        )
        assert sep > tolerance_1mm_m * 20.0, (
            f"Portal {portal.portal_id} separation {sep:.4f}m must be > 20x 1mm tolerance"
        )

        # Lateral offset from centerline must be >= 0.02m
        assert portal.lateral_offset_m >= 0.020

        # Both lanes must lie inside free space
        pt_a = Point(lane_a.x, lane_a.y)
        pt_b = Point(lane_b.x, lane_b.y)
        assert plan.free_space_geom.distance(pt_a) < 1e-4, (
            f"Portal {portal.portal_id} lane_a ({lane_a.x}, {lane_a.y}) outside free space"
        )
        assert plan.free_space_geom.distance(pt_b) < 1e-4, (
            f"Portal {portal.portal_id} lane_b ({lane_b.x}, {lane_b.y}) outside free space"
        )

        # Threshold geometry itself must be covered by free space
        assert plan.free_space_geom.covers(portal.threshold_geom)

        # Clearance validation method must succeed
        assert MacroTopologyGraph.validate_parallel_lane_clearance(portal, plan.free_space_geom) is True
        assert MacroTopologyGraph.validate_parallel_lane_clearance(portal, plan) is True


# ---------------------------------------------------------------------------
# Test 2.4: Phase 1 & Phase 2 Cross-Phase Regression
# ---------------------------------------------------------------------------
def test_phase1_phase2_regression(
    slide5_data_and_plan: tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]],
) -> None:
    """Re-verify Phase 1 scale conversion and node clearance invariants on the topological output."""
    data, plan, portals = slide5_data_and_plan

    # 1. Scale conversion invariants
    assert plan.scale_units_per_m == 10.0, "Slide 5 scale must be 10.0 units/m"
    assert math.isclose(plan.total_area_m2, 126.0, rel_tol=1e-3), "Total metric area must be 126.0 m^2"

    # 2. Node placement clearance invariants
    assert len(plan.nodes) == 6, "Slide 5 must contain 6 fixed nodes"
    for node_id, node in plan.nodes.items():
        node_pt = Point(node.location.x, node.location.y)
        # Node must lie inside its assigned room
        room = plan.rooms[node.room_id]
        assert room.geometry.contains(node_pt) or room.geometry.distance(node_pt) < 1e-3, (
            f"Node {node_id} outside room {node.room_id}"
        )
        # Node must lie inside routable free space
        assert plan.free_space_geom.distance(node_pt) < 1e-4, (
            f"Node {node_id} outside free space"
        )

    # 3. Topological consistency: every room referenced by a portal exists in plan
    for p in portals:
        assert p.room1_id in plan.rooms
        assert p.room2_id in plan.rooms
        # Room boundaries intersect at threshold
        r1_poly = plan.rooms[p.room1_id].geometry
        r2_poly = plan.rooms[p.room2_id].geometry
        assert r1_poly.distance(p.threshold_geom) < 0.05
        assert r2_poly.distance(p.threshold_geom) < 0.05


# ---------------------------------------------------------------------------
# Test 2.5: Topology Error Handling and Strict Single Component Checking
# ---------------------------------------------------------------------------
def test_topology_error_handling(
    slide5_data_and_plan: tuple[dict, MetricFloorPlan, list[DoorOrPassagePortal]],
) -> None:
    """Verify that require_connected=True raises TopologyError on disconnected plans,
    and succeeds on fully connected plans.
    """
    _, plan, portals = slide5_data_and_plan

    # 1. Slide 5 has 2 disconnected eligible components (Living cluster vs Kitchen cluster)
    with pytest.raises(TopologyError, match="disconnected into 2 components"):
        MacroTopologyGraph.build_topology(plan, portals, require_connected=True)

    # 2. Synthetic single connected component floor plan
    inner = box(0.0, 0.0, 10.0, 5.0)
    r1 = box(0.0, 0.0, 5.0, 5.0)
    r2 = box(5.0, 0.0, 10.0, 5.0)
    wall = box(4.95, 0.0, 5.05, 2.0)  # Wall only covers bottom, leaving top open
    free_space = inner.difference(wall)

    connected_plan = MetricFloorPlan(
        plan_id="connected_2room",
        scale_units_per_m=1.0,
        total_area_m2=50.0,
        inner_wkt=inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=wall.wkt,
        rooms={
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category="living",
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt=r1.wkt,
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category="bedroom",
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt=r2.wkt,
            ),
        },
        nodes={},
    )
    connected_portals = extract_all_portals(connected_plan)
    assert len(connected_portals) >= 1

    top = MacroTopologyGraph.build_topology(connected_plan, connected_portals, require_connected=True)
    assert top.is_fully_connected is True
    assert set(top.eligible_room_ids) == {"R01", "R02"}


# ---------------------------------------------------------------------------
# Test 2.6: Parallel Lane Validation Error Handling
# ---------------------------------------------------------------------------
def test_parallel_lane_clearance_validation_errors() -> None:
    """Verify ValueError is raised when lane separation is insufficient or outside free space."""
    # Synthetic portal with separation < 0.020m
    narrow_portal = DoorOrPassagePortal(
        portal_id="P_narrow",
        portal_type=PortalType.DOOR,
        room1_id="R01",
        room2_id="R02",
        center_point=Point2D(x=5.0, y=2.5),
        threshold_wkt="LINESTRING(5.0 2.0, 5.0 3.0)",
        width_m=1.0,
        lane_a_point=Point2D(x=5.0, y=2.495),
        lane_b_point=Point2D(x=5.0, y=2.505),  # sep = 0.010m < 0.020m
        step_room1=Point2D(x=4.85, y=2.5),
        step_room2=Point2D(x=5.15, y=2.5),
        lateral_offset_m=0.02,
    )
    free_space = box(0.0, 0.0, 10.0, 5.0)

    with pytest.raises(ValueError, match="lane separation .* is below minimum required"):
        MacroTopologyGraph.validate_parallel_lane_clearance(narrow_portal, free_space)

    # Synthetic portal with lane point outside free space
    outside_portal = DoorOrPassagePortal(
        portal_id="P_outside",
        portal_type=PortalType.DOOR,
        room1_id="R01",
        room2_id="R02",
        center_point=Point2D(x=5.0, y=2.5),
        threshold_wkt="LINESTRING(5.0 2.0, 5.0 3.0)",
        width_m=1.0,
        lane_a_point=Point2D(x=15.0, y=2.5),  # outside [0, 10]
        lane_b_point=Point2D(x=5.0, y=2.6),
        step_room1=Point2D(x=4.85, y=2.5),
        step_room2=Point2D(x=5.15, y=2.5),
        lateral_offset_m=0.05,
    )
    with pytest.raises(ValueError, match="lies outside free space"):
        MacroTopologyGraph.validate_parallel_lane_clearance(outside_portal, free_space)
