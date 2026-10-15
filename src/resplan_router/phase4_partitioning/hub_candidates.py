"""Rule 6 Compliant Hub Candidate Generator.

Generates candidate hub locations inside eligible rooms satisfying:
1. Rule 6 wall clearance: >= 0.50m from walls, windows, and obstacle boundaries
   (using 0.505m inset margin with >= 0.499m checker verification).
2. Point lies strictly within the host room polygon.
3. Separation from fixed nodes: >= 0.30m separation to preserve visual distinctness.
4. Alignment with Hanan grid lines induced by fixed nodes and room doorways.
5. Excluded rooms (e.g. bathrooms, balconies) produce 0 hub candidates.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from resplan_router.contracts import (
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
)


def _extract_doorway_points(
    plan: MetricFloorPlan,
    room_id: str,
    topology: Optional[PortalTopologyGraph] = None,
) -> Tuple[Set[float], Set[float]]:
    """Extract X and Y coordinates of doorway waypoints associated with room_id."""
    xs: Set[float] = set()
    ys: Set[float] = set()

    # 1. From provided PortalTopologyGraph
    if topology is not None:
        for portal in topology.portals:
            if portal.room1_id == room_id or portal.room2_id == room_id:
                for pt in [
                    portal.center_point,
                    portal.lane_a_point,
                    portal.lane_b_point,
                    portal.step_room1,
                    portal.step_room2,
                ]:
                    xs.add(round(pt.x, 4))
                    ys.add(round(pt.y, 4))

    # 2. Geometric extraction from shared boundary with neighboring rooms
    room = plan.rooms.get(room_id)
    if room is not None:
        r_geom = room.geometry
        for other_id, other_room in plan.rooms.items():
            if other_id == room_id:
                continue
            shared = r_geom.intersection(other_room.geometry)
            if shared is None or shared.is_empty:
                continue
            clipped = shared.intersection(plan.free_space_geom)
            if clipped is None or clipped.is_empty:
                continue

            # Extract lines from clipped shared boundary
            lines: List[LineString] = []
            if isinstance(clipped, LineString):
                lines.append(clipped)
            elif isinstance(clipped, (MultiLineString, GeometryCollection)):
                for part in clipped.geoms:
                    if isinstance(part, LineString):
                        lines.append(part)

            for line in lines:
                if line.length > 0.05:
                    coords = list(line.coords)
                    if coords:
                        p_start = coords[0]
                        p_end = coords[-1]
                        p_mid = line.interpolate(0.5, normalized=True)
                        xs.update([round(p_start[0], 4), round(p_end[0], 4), round(p_mid.x, 4)])
                        ys.update([round(p_start[1], 4), round(p_end[1], 4), round(p_mid.y, 4)])

    return xs, ys


def generate_hub_candidates(
    plan: MetricFloorPlan,
    room_id: str,
    margin_m: float = 0.505,
    min_node_dist: float = 0.30,
    topology: Optional[PortalTopologyGraph] = None,
) -> List[Point2D]:
    """Generate Rule 6 compliant candidate hub locations inside an eligible room.

    Args:
        plan: MetricFloorPlan containing rooms, obstacles, and fixed nodes.
        room_id: Identifier of the room to place hub in.
        margin_m: Inset buffer margin (default 0.505m, providing 5mm safety buffer).
        min_node_dist: Minimum distance between candidate hub and fixed nodes (default 0.30m).
        topology: Optional PortalTopologyGraph providing door and passage waypoints.

    Returns:
        List of valid Point2D candidates strictly inside the room, >= 0.50m from obstacles,
        and >= min_node_dist from fixed nodes. Returns empty list for excluded rooms.

    Raises:
        ValueError: If room_id is not present in plan.rooms.
    """
    if room_id not in plan.rooms:
        raise ValueError(f"Room '{room_id}' not found in floor plan")

    room = plan.rooms[room_id]
    if not room.is_eligible:
        return []

    r_geom = room.geometry
    obs = plan.obstacles_geom

    # 1. Safe placement polygon: P_safe = R_geom \ obstacles.buffer(margin_m)
    obs_buffered = obs.buffer(margin_m)
    safe_poly = r_geom.difference(obs_buffered)
    safe_poly = shapely.make_valid(safe_poly)

    # Fallback to 0.495m if safe_poly is empty or degenerate
    if safe_poly.is_empty or safe_poly.area < 1e-4:
        fallback_buffered = obs.buffer(0.495)
        fallback_safe = r_geom.difference(fallback_buffered)
        fallback_safe = shapely.make_valid(fallback_safe)
        if fallback_safe.is_empty or fallback_safe.area < 1e-4:
            safe_poly = r_geom
        else:
            safe_poly = fallback_safe

    # 2. Extract alignment coordinates (X and Y sets)
    xs: Set[float] = set()
    ys: Set[float] = set()

    # Fixed nodes in this room and across the floor plan
    room_nodes = [n for n in plan.nodes.values() if n.room_id == room_id]
    all_nodes = list(plan.nodes.values())

    offsets = [0.35, 0.50, 0.75, 1.0, 1.5, 2.0, -0.35, -0.50, -0.75, -1.0, -1.5, -2.0]

    for n in room_nodes:
        nx = round(n.location.x, 4)
        ny = round(n.location.y, 4)
        xs.add(nx)
        ys.add(ny)
        for delta in offsets:
            xs.add(round(nx + delta, 4))
            ys.add(round(ny + delta, 4))

    # Doorway lines and threshold waypoints
    door_xs, door_ys = _extract_doorway_points(plan, room_id, topology)
    xs.update(door_xs)
    ys.update(door_ys)

    # Room bounds and interior pole
    minx, miny, maxx, maxy = safe_poly.bounds
    cx = round((minx + maxx) / 2.0, 4)
    cy = round((miny + maxy) / 2.0, 4)
    xs.add(cx)
    ys.add(cy)

    try:
        pole = polylabel(safe_poly, tolerance=0.001)
        px = round(pole.x, 4)
        py = round(pole.y, 4)
        xs.add(px)
        ys.add(py)
        for delta in offsets:
            xs.add(round(px + delta, 4))
            ys.add(round(py + delta, 4))
    except Exception:
        pass

    # Inset bounds
    xs.update([round(minx + 0.1, 4), round(maxx - 0.1, 4)])
    ys.update([round(miny + 0.1, 4), round(maxy - 0.1, 4)])

    # 3. Filter candidates against all invariants
    min_obs_dist = 0.50 - 0.001  # 0.499m Rule 6 tolerance

    candidates: List[Point2D] = []
    seen_coords: Set[Tuple[float, float]] = set()

    def try_add_point(x_val: float, y_val: float) -> bool:
        coord_key = (round(x_val, 3), round(y_val, 3))
        if coord_key in seen_coords:
            return False

        pt = Point(x_val, y_val)

        # Must lie strictly inside the host room polygon
        if not r_geom.contains(pt):
            return False

        # Must lie within safe polygon (or satisfy distance to obstacles)
        dist_obs = obs.distance(pt)
        if dist_obs < min_obs_dist:
            return False

        # Must maintain separation from fixed nodes
        for node in all_nodes:
            node_pt = Point(node.location.x, node.location.y)
            if pt.distance(node_pt) < (min_node_dist - 1e-4):
                return False

        seen_coords.add(coord_key)
        candidates.append(Point2D(x=round(x_val, 4), y=round(y_val, 4)))
        return True

    # Test coordinate grid crossings
    for x in sorted(xs):
        if x < minx or x > maxx:
            continue
        for y in sorted(ys):
            if y < miny or y > maxy:
                continue
            try_add_point(x, y)

    # 4. Fallback regular grid if no candidate found
    if not candidates:
        grid_step = 0.20
        curr_x = minx + 0.05
        while curr_x <= maxx:
            curr_y = miny + 0.05
            while curr_y <= maxy:
                try_add_point(curr_x, curr_y)
                curr_y += grid_step
            curr_x += grid_step

    # 5. Fallback interior pole if still empty
    if not candidates:
        try:
            fallback_pole = polylabel(safe_poly, tolerance=0.001)
            f_pt = Point(fallback_pole.x, fallback_pole.y)
            if r_geom.contains(f_pt) and obs.distance(f_pt) >= min_obs_dist:
                candidates.append(Point2D(x=round(fallback_pole.x, 4), y=round(fallback_pole.y, 4)))
        except Exception:
            pass

    # Sort candidates deterministically
    candidates.sort(key=lambda p: (p.x, p.y))
    return candidates


def generate_all_room_hub_candidates(
    plan: MetricFloorPlan,
    margin_m: float = 0.505,
    min_node_dist: float = 0.30,
    topology: Optional[PortalTopologyGraph] = None,
) -> Dict[str, List[Point2D]]:
    """Generate Rule 6 candidate hub locations for all rooms in the floor plan.

    Eligible rooms receive non-empty lists of valid Point2D candidates.
    Excluded rooms (e.g. bathroom, balcony) receive empty lists (0 candidates).

    Args:
        plan: Normalized MetricFloorPlan.
        margin_m: Wall clearance margin in metres (default 0.505m).
        min_node_dist: Minimum separation from fixed nodes in metres (default 0.30m).
        topology: Optional PortalTopologyGraph.

    Returns:
        Dictionary mapping room_id to list of candidate Point2D locations.
    """
    results: Dict[str, List[Point2D]] = {}
    for room_id in sorted(plan.rooms.keys()):
        results[room_id] = generate_hub_candidates(
            plan=plan,
            room_id=room_id,
            margin_m=margin_m,
            min_node_dist=min_node_dist,
            topology=topology,
        )
    return results
