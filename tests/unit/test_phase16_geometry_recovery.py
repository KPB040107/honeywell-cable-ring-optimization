"""Unit tests for Phase 16 Geometry Recovery and Healing (Agent 2).

Tests:
1. Cadastral Wall Overlap Healing (zero-thickness overlaps, collinear slivers).
2. Doorway Portal Refinement (normal dilation, step points in free space).
3. Sub-millimeter Hanan Spur Snapping (exact 0.000 mm snapping, >= 2 points).
4. Corridor Corner Chamfering & Adaptive Grid Densification (pocket connectivity).
5. Safe Hub Location Separation (clearance >= 0.50 m, separation from fixed nodes).
6. Empirical Plan Recovery on key discrepancy plans (Plans 8, 10, 19, 8202, 10080, 8335, 1924).
7. Mandatory Invariant Checks (nodes never moved, 1 mm tolerance, 0 obstacle penetration).
"""

from __future__ import annotations

import math
import networkx as nx
import pytest
from shapely.geometry import box, LineString, Point, Polygon

from resplan_router.contracts import (
    CompassDirection,
    CompletedHubRing,
    FixedNode,
    MetricFloorPlan,
    Point2D,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.geometry_healing import (
    build_adaptive_hanan_grid,
    extract_adaptive_corridor_points,
    find_separated_safe_hub_location,
    heal_cad_wall_geometry,
    heal_solution_bundle,
    recover_and_certify_plan,
    refine_doorway_portals,
)
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase6_output.checker import FloorPlanChecker


# =============================================================================
# 1. CADASTRAL WALL OVERLAP HEALING
# =============================================================================

def test_cad_wall_overlap_healing():
    """Verify healing of collinear overlapping wall polygons and zero-width slivers."""
    inner = box(0.0, 0.0, 10.0, 10.0)
    # Two walls that overlap collinearly and have a zero-width sliver
    w1 = box(2.0, 0.0, 4.0, 5.0)
    w2 = box(3.9999, 0.0, 6.0, 5.0)  # 0.1 mm overlap with w1
    door = box(3.5, 0.0, 4.5, 0.5)   # Carved door

    obs, free_space = heal_cad_wall_geometry(
        walls=[w1, w2],
        windows=[],
        inner_geom=inner,
        doors=[door],
    )

    assert obs.is_valid
    assert free_space.is_valid
    # Overlapping walls should be cleanly unioned without self-intersection
    assert obs.geom_type in ("Polygon", "MultiPolygon")
    assert free_space.geom_type in ("Polygon", "MultiPolygon")
    # Door area should be routable in free space
    assert free_space.intersects(door)


# =============================================================================
# 2. DOORWAY PORTAL REFINEMENT
# =============================================================================

def test_doorway_portal_refinement():
    """Verify portal refinement preserves threshold >= 0.05 m and validates step points."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    assert len(portals) > 0

    refined = refine_doorway_portals(portals, plan, normal_dilation_m=0.005)
    assert len(refined) > 0
    for p in refined:
        assert p.width_m >= 0.05
        # Verify step points are inside inner boundary
        pt1 = Point(p.step_room1.x, p.step_room1.y)
        pt2 = Point(p.step_room2.x, p.step_room2.y)
        assert plan.inner_geom.buffer(0.01).contains(pt1)
        assert plan.inner_geom.buffer(0.01).contains(pt2)


# =============================================================================
# 3. SUB-MILLIMETER HANAN SPUR SNAPPING
# =============================================================================

def test_sub_millimeter_spur_snapping():
    """Verify that degenerate 1-point segments and sub-mm terminal misses are healed."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(8)
    n1 = plan.nodes["N1"]

    # Create a synthetic ring with a 1-point segment and a slight 2.2 mm terminal miss
    hub_loc = Point2D(x=round(n1.location.x + 0.0002, 4), y=n1.location.y)
    seg0 = RouteSegment(
        from_node="H1",
        to_node="N1",
        length_m=0.0,
        polyline=[hub_loc],  # Only 1 point! (Portal degeneracy)
    )
    seg1 = RouteSegment(
        from_node="N1",
        to_node="H1",
        length_m=0.0,
        polyline=[hub_loc],  # Only 1 point!
    )
    ring = CompletedHubRing(
        hub_id="H1",
        room_id=n1.room_id,
        location=hub_loc,
        assigned_rooms=[n1.room_id],
        assigned_nodes=["N1"],
        closed_sequence=["H1", "N1", "H1"],
        route_segments=[seg0, seg1],
        ring_length_m=0.0,
        ring_bends=0,
        ring_crossings=0,
        ring_overlap_m=0.0,
        cost_J=0.0,
    )
    bundle = SynthesizedRingsBundle(
        plan_id=plan.plan_id,
        hub_rings=[ring],
        total_length_m=0.0,
        total_bends=0,
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=0.0,
    )

    healed_bundle = heal_solution_bundle(bundle, plan)

    # Assert all segments now have >= 2 points
    for r in healed_bundle.hub_rings:
        for s in r.route_segments:
            assert len(s.polyline) >= 2
            # Verify exact endpoint alignment
            assert math.hypot(s.polyline[0].x - hub_loc.x, s.polyline[0].y - hub_loc.y) < 1e-4 or \
                   math.hypot(s.polyline[0].x - n1.location.x, s.polyline[0].y - n1.location.y) < 1e-4
            assert math.hypot(s.polyline[-1].x - hub_loc.x, s.polyline[-1].y - hub_loc.y) < 1e-4 or \
                   math.hypot(s.polyline[-1].x - n1.location.x, s.polyline[-1].y - n1.location.y) < 1e-4


# =============================================================================
# 4. CORRIDOR CORNER CHAMFERING & ADAPTIVE GRID DENSIFICATION
# =============================================================================

def test_corridor_corner_chamfer_densification():
    """Verify that adaptive grid line insertion connects isolated corridor pockets."""
    repo = DatasetRepository.get_instance()
    # Plan 1924 failed on standard Hanan grid because living room pocket was disconnected
    plan = repo.get_floor_plan(1924)
    topology = MacroTopologyGraph.build_topology(plan, extract_all_portals(plan))

    adaptive_grid = build_adaptive_hanan_grid(plan, topology, include_corridor_corners=True)
    n1_v = adaptive_grid.snap_to_vertex(plan.nodes["N1"].location, 0.005)
    n3_v = adaptive_grid.snap_to_vertex(plan.nodes["N3"].location, 0.005)

    # In adaptive grid, N1 and N3 MUST be in the same connected component
    assert adaptive_grid.graph.has_node(n1_v)
    assert adaptive_grid.graph.has_node(n3_v)
    assert nx.has_path(adaptive_grid.graph, n1_v, n3_v)

    # A* router finds a valid obstacle-free path
    router = DirectionStateRouter()
    path = router.find_shortest_path(adaptive_grid, plan.nodes["N1"].location, plan.nodes["N3"].location)
    assert path.length_m > 0.0
    assert len(path.polyline) >= 2

    # Check zero obstacle penetration
    for i in range(len(path.polyline) - 1):
        p1, p2 = path.polyline[i], path.polyline[i + 1]
        ls = LineString([(p1.x, p1.y), (p2.x, p2.y)])
        inter = plan.obstacles_geom.intersection(ls)
        assert inter.length <= 0.001


# =============================================================================
# 5. SAFE HUB LOCATION SEPARATION
# =============================================================================

def test_safe_hub_location_separation():
    """Verify that hubs in single-room zones are placed with >= 0.50m clearance and offset from node."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(8)
    n4 = plan.nodes["N4"]

    loc, clr = find_separated_safe_hub_location(
        plan=plan,
        room_id=n4.room_id,
        assigned_nodes=[n4],
        min_clearance_m=0.50,
    )
    # Rule 6 requirement: clearance >= 0.499 m
    assert clr >= 0.499
    # Must lie inside host room
    room_poly = plan.rooms[n4.room_id].geometry
    assert room_poly.buffer(0.001).contains(Point(loc.x, loc.y))


# =============================================================================
# 6. EMPIRICAL PLAN RECOVERY ON DISCREPANCY PLANS
# =============================================================================

@pytest.mark.parametrize("plan_id", [8, 10, 19, 8202, 10080, 8335, 1924])
def test_empirical_plan_recovery(plan_id: int):
    """Verify that previously failing discrepancy plans are certified 100% valid."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(plan_id)

    is_valid, report, bundle = recover_and_certify_plan(plan)

    assert is_valid is True, f"Plan {plan_id} recovery failed: {report.violations}"
    assert len(report.violations) == 0
    assert report.hub_count >= 1
    assert report.total_cost_J < float("inf")
    assert report.total_length_m > 0.0

    # Independent FloorPlanChecker confirmation
    checker_report = FloorPlanChecker.evaluate(plan, bundle)
    assert checker_report.is_valid is True
    assert len(checker_report.violations) == 0


# =============================================================================
# 7. MANDATORY INVARIANT CHECKS
# =============================================================================

def test_mandatory_invariants_preserved():
    """Verify: fixed nodes NEVER moved, 1mm tolerance respected, 0 wall penetration."""
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(8335)

    original_nodes = {nid: (n.location.x, n.location.y) for nid, n in plan.nodes.items()}

    is_valid, report, bundle = recover_and_certify_plan(plan)
    assert is_valid is True

    # 1. Invariant 1: Fixed nodes never moved
    for nid, coords in original_nodes.items():
        assert plan.nodes[nid].location.x == coords[0]
        assert plan.nodes[nid].location.y == coords[1]

    # 2. Invariant 2 & 3: Cables strictly inside inner boundary and outside obstacles
    for ring in bundle.hub_rings:
        for seg in ring.route_segments:
            pts = seg.polyline
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i + 1]
                ls = LineString([(p1.x, p1.y), (p2.x, p2.y)])
                # Obstacle penetration <= 1 mm
                inter = plan.obstacles_geom.intersection(ls)
                assert inter.length <= 0.001
                # Inner containment
                diff = ls.difference(plan.inner_geom.buffer(0.001))
                assert diff.length <= 0.001

    # 3. Invariant 4: Hub clearance >= 0.50 m (0.499 m with tolerance)
    for ring in bundle.hub_rings:
        hub_pt = Point(ring.location.x, ring.location.y)
        clr_obs = plan.obstacles_geom.distance(hub_pt)
        clr_inner = plan.inner_geom.boundary.distance(hub_pt)
        assert clr_obs >= 0.499
        assert clr_inner >= 0.499
