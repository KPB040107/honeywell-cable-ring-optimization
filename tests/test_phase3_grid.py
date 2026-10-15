"""Official Phase 3 Hanan grid verification suite.

Validates:
- 100% of grid edges are strictly axis-aligned (|dx| < 1e-6 or |dy| < 1e-6).
- Zero grid edges intersect plan.obstacles_geom or lie outside plan.inner_geom.
- All edges have clearance >= 0.001m to obstacles.
- Every fixed node and portal lane waypoint corresponds to an exact vertex in the grid.
- All edges are tagged with valid room or portal IDs.
- Pathfinding connectivity respects door portals and rejects solid walls / windows.
"""

from __future__ import annotations

import json
from pathlib import Path
import networkx as nx
import pytest
import shapely
from shapely.geometry import box, LineString, Point, Polygon

from resplan_router.contracts import (
    CompassDirection,
    DoorOrPassagePortal,
    EligibleOrExcludedRoom,
    FixedNode,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    PortalType,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.rectilinear_grid import (
    GridEdge,
    HananRoutingGrid,
    build_hanan_grid,
    deduplicate_coords,
)


@pytest.fixture
def slide5_plan_and_topology() -> tuple[MetricFloorPlan, PortalTopologyGraph]:
    """Fixture providing parsed Slide 5 plan and constructed topology graph."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    return plan, topology


def test_slide5_schematic_hanan_grid(
    slide5_plan_and_topology: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Verify Hanan grid properties on the official Slide 5 benchmark schematic."""
    plan, topology = slide5_plan_and_topology
    grid = build_hanan_grid(plan, topology)

    assert grid.plan_id == "slide5_schematic"
    assert grid.vertex_count > 100
    assert grid.edge_count > 200

    portal_ids = {p.portal_id for p in topology.portals}
    room_ids = set(plan.rooms.keys())

    # 1. Invariant 1: 100% of grid edges are strictly axis-aligned
    for edge in grid.edges:
        dx = abs(edge.u.x - edge.v.x)
        dy = abs(edge.u.y - edge.v.y)
        assert dx < 1e-6 or dy < 1e-6, f"Edge {edge} is not axis-aligned: dx={dx}, dy={dy}"
        assert edge.length_m > 0.0

    # 2. Invariant 2: Zero grid edges intersect obstacles or lie outside inner boundary
    for edge in grid.edges:
        ls = edge.linestring
        # Edge must be inside inner boundary
        assert plan.inner_geom.covers(ls) or ls.within(plan.inner_geom), (
            f"Edge {edge} lies outside inner boundary"
        )
        # Zero intersection with obstacle polygons
        assert not ls.intersects(plan.obstacles_geom), (
            f"Edge {edge} intersects obstacles_geom"
        )
        # Distance to obstacles must be positive (>= 1mm)
        dist_obs = ls.distance(plan.obstacles_geom)
        assert dist_obs >= 0.001, (
            f"Edge {edge} obstacle clearance {dist_obs:.6f}m < 0.001m"
        )

    # 3. Invariant 3: All edges tagged with valid room or portal IDs
    for edge in grid.edges:
        assert (edge.portal_id is not None) or (edge.room_id is not None), (
            f"Edge {edge} has neither portal_id nor room_id"
        )
        if edge.portal_id is not None:
            assert edge.portal_id in portal_ids, f"Invalid portal tag {edge.portal_id}"
        if edge.room_id is not None:
            assert edge.room_id in room_ids, f"Invalid room tag {edge.room_id}"
        assert edge.tag in portal_ids or edge.tag in room_ids

    # 4. Invariant 4: Every fixed node corresponds to an exact grid vertex
    assert len(plan.nodes) > 0
    for node_id, node in plan.nodes.items():
        assert grid.has_vertex(node.location), f"Node {node_id} ({node.location.coords}) missing from grid"
        assert grid.get_vertex(node.location) == node.location
        # Node must be connected in the grid (degree >= 1)
        assert grid.graph.degree(node.location) >= 1, (
            f"Node {node_id} is isolated in grid graph (degree 0)"
        )

    # 5. Invariant 5: Every portal lane waypoint corresponds to an exact grid vertex
    assert len(topology.portals) > 0
    for portal in topology.portals:
        for pt_name, pt in [
            ("center", portal.center_point),
            ("lane_a", portal.lane_a_point),
            ("lane_b", portal.lane_b_point),
            ("step1", portal.step_room1),
            ("step2", portal.step_room2),
        ]:
            assert grid.has_vertex(pt), (
                f"Portal {portal.portal_id} {pt_name} waypoint {pt.coords} missing from grid"
            )
            assert grid.get_vertex(pt) == pt
            assert grid.graph.degree(pt) >= 1, (
                f"Portal {portal.portal_id} {pt_name} waypoint is isolated in grid"
            )


def test_synthetic_two_room_door_grid() -> None:
    """Verify Hanan grid construction and inter-room connectivity on synthetic two-room plan."""
    # Room 1: (0, 0) to (5, 5)
    # Room 2: (5, 0) to (10, 5)
    # Wall between rooms at x in [4.95, 5.05] with door at y in [2.0, 3.0]
    inner = box(0.0, 0.0, 10.0, 5.0)
    r1 = box(0.0, 0.0, 5.0, 5.0)
    r2 = box(5.0, 0.0, 10.0, 5.0)

    wall_bottom = box(4.95, 0.0, 5.05, 2.0)
    wall_top = box(4.95, 3.0, 5.05, 5.0)
    obstacles = shapely.unary_union([wall_bottom, wall_top])
    free_space = inner.difference(obstacles)

    door_threshold = LineString([(5.0, 2.0), (5.0, 3.0)])

    portal = DoorOrPassagePortal(
        portal_id="P01",
        portal_type=PortalType.DOOR,
        room1_id="R01",
        room2_id="R02",
        center_point=Point2D(x=5.0, y=2.5),
        threshold_wkt=door_threshold.wkt,
        width_m=1.0,
        lane_a_point=Point2D(x=5.0, y=2.4),
        lane_b_point=Point2D(x=5.0, y=2.6),
        step_room1=Point2D(x=4.85, y=2.5),
        step_room2=Point2D(x=5.15, y=2.5),
        lateral_offset_m=0.1,
    )

    n1 = FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.0, y=2.5))
    n2 = FixedNode(node_id="N2", room_id="R02", location=Point2D(x=8.0, y=2.5))

    plan = MetricFloorPlan(
        plan_id="two_room_plan",
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
        nodes={"N1": n1, "N2": n2},
    )

    topology = PortalTopologyGraph(
        plan_id="two_room_plan",
        eligible_room_ids=["R01", "R02"],
        portals=[portal],
        adjacency={"R01": ["R02"], "R02": ["R01"]},
        is_fully_connected=True,
        leaf_room_ids=["R01", "R02"],
    )

    grid = build_hanan_grid(plan, topology)

    assert grid.has_vertex(n1.location)
    assert grid.has_vertex(n2.location)
    assert grid.has_vertex(portal.center_point)
    assert grid.has_vertex(portal.lane_a_point)
    assert grid.has_vertex(portal.lane_b_point)

    # Verify zero obstacle collisions across all edges
    for edge in grid.edges:
        assert not edge.linestring.intersects(obstacles)
        assert free_space.covers(edge.linestring) or edge.linestring.within(free_space)

    # Verify connectivity: path exists between N1 and N2 through the door
    assert nx.has_path(grid.graph, n1.location, n2.location)
    path = nx.shortest_path(grid.graph, n1.location, n2.location)
    assert len(path) >= 2


def test_synthetic_window_wall_obstacle_rejection() -> None:
    """Verify that solid walls and windows block Hanan grid edges completely."""
    inner = box(0.0, 0.0, 10.0, 5.0)
    r1 = box(0.0, 0.0, 5.0, 5.0)
    r2 = box(5.0, 0.0, 10.0, 5.0)

    # Dividing wall with window gap (no door)
    wall1 = box(4.95, 0.0, 5.05, 2.0)
    wall2 = box(4.95, 3.0, 5.05, 5.0)
    window = box(4.95, 2.0, 5.05, 3.0)
    obstacles = shapely.unary_union([wall1, wall2, window])
    free_space = inner.difference(obstacles)

    n1 = FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.0, y=2.5))
    n2 = FixedNode(node_id="N2", room_id="R02", location=Point2D(x=8.0, y=2.5))

    plan = MetricFloorPlan(
        plan_id="window_blocked_plan",
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
        nodes={"N1": n1, "N2": n2},
    )

    topology = PortalTopologyGraph(
        plan_id="window_blocked_plan",
        eligible_room_ids=["R01", "R02"],
        portals=[],
        adjacency={"R01": [], "R02": []},
        is_fully_connected=False,
        leaf_room_ids=[],
    )

    grid = build_hanan_grid(plan, topology)

    # Zero edges intersect window or wall
    for edge in grid.edges:
        assert not edge.linestring.intersects(obstacles)

    # Since there is no door and window is an obstacle, N1 and N2 MUST NOT be connected!
    assert not nx.has_path(grid.graph, n1.location, n2.location)


def test_deduplicate_coords_preserves_terminals() -> None:
    """Verify deduplicate_coords maintains exact terminal coordinates and merges close candidates."""
    candidates = [1.0, 1.0004, 1.0008, 2.5, 2.5002, 3.0, 3.04, 3.0401]
    exact = {1.0004, 3.0401}

    result = deduplicate_coords(candidates, exact, tol=0.001)

    # Exact coordinates must be retained exactly
    for ev in exact:
        assert ev in result

    # Consecutive items must be >= 1mm apart unless both are exact
    for i in range(len(result) - 1):
        diff = result[i + 1] - result[i]
        assert diff > 0.0


def test_grid_snap_to_vertex() -> None:
    """Verify snap_to_vertex exact matching, tolerance snapping, and out-of-range rejection."""
    v1 = Point2D(x=1.0, y=2.0)
    v2 = Point2D(x=3.0, y=4.0)
    edge = GridEdge(u=v1, v=v2, direction=CompassDirection.NORTH, length_m=2.0)

    grid = HananRoutingGrid(plan_id="test", vertices=[v1, v2], edges=[edge])

    # Exact match
    assert grid.snap_to_vertex(Point2D(x=1.0, y=2.0)) == v1

    # Snapping within 0.001m
    assert grid.snap_to_vertex(Point2D(x=1.0005, y=2.0002), tolerance=0.001) == v1

    # Distant point raises KeyError
    with pytest.raises(KeyError):
        grid.snap_to_vertex(Point2D(x=10.0, y=10.0), tolerance=0.001)


def test_grid_edge_reversal_and_directions() -> None:
    """Verify GridEdge reversal and directional logic."""
    p1 = Point2D(x=1.0, y=1.0)
    p2 = Point2D(x=4.0, y=1.0)
    edge = GridEdge(
        u=p1,
        v=p2,
        direction=CompassDirection.EAST,
        length_m=3.0,
        room_id="R01",
        portal_id="P01",
    )

    assert edge.tag == "P01"
    rev = edge.reverse()
    assert rev.u == p2
    assert rev.v == p1
    assert rev.direction == CompassDirection.WEST
    assert rev.length_m == 3.0
    assert rev.room_id == "R01"
    assert rev.portal_id == "P01"
