"""Geometric portal extraction engine for interior doors and open passages.

Extracts interior doors and open passages (>= 0.50m width) between rooms,
generating parallel doorway lanes (>= 20mm lateral separation) and orthogonal
room step points, while strictly rejecting exterior walls, windows, and entrances.
"""

from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple
import shapely
from shapely.geometry import Point, Polygon, MultiPolygon, LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    DoorOrPassagePortal,
    MetricFloorPlan,
    Point2D,
    PortalType,
)


def _ensure_geometry(geom: Any) -> Optional[BaseGeometry]:
    """Normalize input geometry representation into a valid Shapely BaseGeometry."""
    if geom is None:
        return None
    if isinstance(geom, BaseGeometry):
        return geom if geom.is_valid else shapely.make_valid(geom)
    if isinstance(geom, dict):
        try:
            parsed = shapely.geometry.shape(geom)
            return parsed if parsed.is_valid else shapely.make_valid(parsed)
        except Exception:
            return None
    if isinstance(geom, (list, tuple)):
        try:
            if len(geom) >= 3:
                poly = Polygon(geom)
                return poly if poly.is_valid else shapely.make_valid(poly)
            elif len(geom) == 2:
                return LineString(geom)
        except Exception:
            return None
    return None


def _flatten_geometries(geoms: Optional[Sequence[Any]]) -> List[BaseGeometry]:
    """Flatten and validate a sequence of input geometries."""
    if not geoms:
        return []
    flat: List[BaseGeometry] = []
    for g in geoms:
        norm = _ensure_geometry(g)
        if norm is None or norm.is_empty:
            continue
        if isinstance(norm, (MultiPolygon, shapely.GeometryCollection)):
            for part in norm.geoms:
                if not part.is_empty:
                    flat.append(part)
        else:
            flat.append(norm)
    return flat


def _extract_linestrings(geom: Optional[BaseGeometry]) -> List[LineString]:
    """Extract non-trivial LineString segments (> 1e-4 m length) from geometry."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom] if geom.length > 1e-4 else []
    if isinstance(geom, MultiLineString):
        return [ls for ls in geom.geoms if ls.length > 1e-4]
    if isinstance(geom, (shapely.GeometryCollection, MultiPolygon)):
        result: List[LineString] = []
        for g in geom.geoms:
            result.extend(_extract_linestrings(g))
        return result
    return []


def _get_shared_boundary(poly1: BaseGeometry, poly2: BaseGeometry) -> Optional[BaseGeometry]:
    """Compute the shared 1D boundary between two room polygons."""
    inter = poly1.intersection(poly2)
    lines = _extract_linestrings(inter)
    if lines:
        return shapely.unary_union(lines)

    # Check boundary-to-boundary intersection
    b_inter = poly1.boundary.intersection(poly2.boundary)
    lines = _extract_linestrings(b_inter)
    if lines:
        return shapely.unary_union(lines)

    # Check poly1 boundary inside poly2
    p1_in_p2 = poly1.boundary.intersection(poly2)
    lines = _extract_linestrings(p1_in_p2)
    if lines:
        return shapely.unary_union(lines)

    return None


def _build_portal(
    portal_id: str,
    portal_type: PortalType,
    room1_id: str,
    room2_id: str,
    threshold_line: LineString,
    r1_poly: BaseGeometry,
    r2_poly: BaseGeometry,
) -> Optional[DoorOrPassagePortal]:
    """Construct a DoorOrPassagePortal from a threshold segment and connected room geometries."""
    coords = list(threshold_line.coords)
    if len(coords) < 2:
        return None

    w = float(threshold_line.length)
    if w < 0.02:
        return None

    # Midpoint of threshold
    p0 = threshold_line.interpolate(0.5, normalized=True)
    p0_x = max(0.0, float(p0.x))
    p0_y = max(0.0, float(p0.y))

    # Tangent vector from start to end
    dx = float(coords[-1][0] - coords[0][0])
    dy = float(coords[-1][1] - coords[0][1])
    chord_len = math.hypot(dx, dy)
    if chord_len < 1e-6:
        return None

    t_x = dx / chord_len
    t_y = dy / chord_len

    # Normal vector perpendicular to tangent
    n_x = -t_y
    n_y = t_x

    # Lateral offset delta = min(0.10, w / 4) >= 0.02 m
    delta = max(0.02, min(0.10, w / 4.0))

    # Parallel lane waypoints
    lane_a_x = max(0.0, p0_x - delta * t_x)
    lane_a_y = max(0.0, p0_y - delta * t_y)
    lane_b_x = max(0.0, p0_x + delta * t_x)
    lane_b_y = max(0.0, p0_y + delta * t_y)

    # Room step points inset 0.15m orthogonally into room1 and room2
    cand1 = (max(0.0, p0_x + 0.15 * n_x), max(0.0, p0_y + 0.15 * n_y))
    cand2 = (max(0.0, p0_x - 0.15 * n_x), max(0.0, p0_y - 0.15 * n_y))

    pt_c1 = Point(cand1[0], cand1[1])
    pt_c2 = Point(cand2[0], cand2[1])

    dist1_c1 = r1_poly.distance(pt_c1)
    dist1_c2 = r1_poly.distance(pt_c2)

    if dist1_c1 < dist1_c2:
        s1 = cand1
        s2 = cand2
    else:
        s1 = cand2
        s2 = cand1

    return DoorOrPassagePortal(
        portal_id=portal_id,
        portal_type=portal_type,
        room1_id=room1_id,
        room2_id=room2_id,
        center_point=Point2D(x=round(p0_x, 4), y=round(p0_y, 4)),
        threshold_wkt=threshold_line.wkt,
        width_m=round(w, 4),
        lane_a_point=Point2D(x=round(lane_a_x, 4), y=round(lane_a_y, 4)),
        lane_b_point=Point2D(x=round(lane_b_x, 4), y=round(lane_b_y, 4)),
        step_room1=Point2D(x=round(s1[0], 4), y=round(s1[1], 4)),
        step_room2=Point2D(x=round(s2[0], 4), y=round(s2[1], 4)),
        lateral_offset_m=round(delta, 4),
    )


def _normalize_geom_units(
    geom: BaseGeometry,
    plan: MetricFloorPlan,
) -> BaseGeometry:
    """Normalize geometry from drawing units to metric if needed."""
    if geom is None or geom.is_empty:
        return geom
    scale = plan.scale_units_per_m
    inner_bounds = plan.inner_geom.bounds
    g_bounds = geom.bounds
    if scale > 1.0 + 1e-4 and (g_bounds[2] > inner_bounds[2] + 0.1 or g_bounds[3] > inner_bounds[3] + 0.1):
        return shapely.affinity.scale(geom, xfact=1.0 / scale, yfact=1.0 / scale, origin=(0.0, 0.0))
    return geom


def extract_door_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:
    """Extract interior door portals between adjacent rooms.

    Filters out exterior entrance doors and doors within 0.05m of the exterior
    inner boundary. Threshold segments are clipped to free space to guarantee
    zero wall collision.

    Args:
        plan: Normalized metric floor plan contract.
        raw_doors: List of door polygons or geometries.
        raw_entrance: Optional entrance/front_door polygon or geometry.

    Returns:
        List[DoorOrPassagePortal]: Extracted interior door portals.
    """
    flat_doors = _flatten_geometries(raw_doors)
    if not flat_doors or not plan.rooms:
        return []

    entrance_geom = _ensure_geometry(raw_entrance)
    if entrance_geom is not None:
        entrance_geom = _normalize_geom_units(entrance_geom, plan)

    normalized_doors = [_normalize_geom_units(d, plan) for d in flat_doors]
    inner_boundary = plan.inner_geom.boundary

    room_items = list(plan.rooms.items())
    room_geoms = [r.geometry for _, r in room_items]
    tree = shapely.STRtree(room_geoms)

    portals: List[DoorOrPassagePortal] = []
    seen_connections: set[Tuple[str, str, Tuple[float, float]]] = set()

    for door in normalized_doors:
        # 1. Exclude doors intersecting entrance or near exterior boundary
        if entrance_geom is not None and not entrance_geom.is_empty:
            if door.intersects(entrance_geom) or door.distance(entrance_geom) < 0.05:
                continue

        if door.distance(inner_boundary) < 0.05 or door.intersects(inner_boundary):
            continue

        # 2. Query STRtree with buffered door (0.05m expansion)
        buffered_door = door.buffer(0.05)
        candidate_idxs = tree.query(buffered_door)

        matching_rooms: List[Tuple[str, BaseGeometry]] = []
        for idx in candidate_idxs:
            r_id, r_obj = room_items[idx]
            r_poly = room_geoms[idx]
            inter = r_poly.intersection(buffered_door)
            if inter.area > 1e-4:
                matching_rooms.append((r_id, r_poly))

        # Only interior doors connecting exactly two rooms are valid
        if len(matching_rooms) != 2:
            continue

        (r1_id, r1_poly), (r2_id, r2_poly) = matching_rooms
        if r1_id > r2_id:
            r1_id, r2_id = r2_id, r1_id
            r1_poly, r2_poly = r2_poly, r1_poly

        # 3. Compute shared boundary and threshold LineString
        shared = _get_shared_boundary(r1_poly, r2_poly)
        if shared is None or shared.is_empty:
            continue

        threshold_raw = shared.intersection(door)
        if threshold_raw.is_empty or threshold_raw.length < 1e-4:
            threshold_raw = shared.intersection(buffered_door)

        # Clip against free space
        threshold_free = threshold_raw.intersection(plan.free_space_geom)
        lines = _extract_linestrings(threshold_free)
        if not lines:
            continue

        # Use the longest contiguous threshold segment
        thresh_line = max(lines, key=lambda l: l.length)
        if thresh_line.length < 0.05:
            continue

        # Deduplicate
        p0 = thresh_line.interpolate(0.5, normalized=True)
        key = (r1_id, r2_id, (round(p0.x, 2), round(p0.y, 2)))
        if key in seen_connections:
            continue
        seen_connections.add(key)

        portal_id = f"P{len(portals) + 1:02d}"
        portal = _build_portal(
            portal_id=portal_id,
            portal_type=PortalType.DOOR,
            room1_id=r1_id,
            room2_id=r2_id,
            threshold_line=thresh_line,
            r1_poly=r1_poly,
            r2_poly=r2_poly,
        )
        if portal is not None:
            portals.append(portal)

    return portals


def extract_open_passages(
    plan: MetricFloorPlan,
    existing_portals: Optional[List[DoorOrPassagePortal]] = None,
) -> List[DoorOrPassagePortal]:
    """Extract open passage portals (width >= 0.50m) along shared boundaries in free space.

    Existing door portal thresholds are subtracted so doors are never duplicated
    as open passages. Solid walls and windows are rejected by free space clipping.

    Args:
        plan: Normalized metric floor plan contract.
        existing_portals: List of previously extracted door portals.

    Returns:
        List[DoorOrPassagePortal]: Extracted open passage portals.
    """
    if not plan.rooms:
        return []

    door_thresholds: List[BaseGeometry] = []
    if existing_portals:
        for p in existing_portals:
            door_thresholds.append(p.threshold_geom.buffer(0.05))

    door_union = shapely.unary_union(door_thresholds) if door_thresholds else None

    room_ids = sorted(plan.rooms.keys())
    open_portals: List[DoorOrPassagePortal] = []
    portal_counter = len(existing_portals or []) + 1

    for i in range(len(room_ids)):
        r1_id = room_ids[i]
        r1_poly = plan.rooms[r1_id].geometry

        for j in range(i + 1, len(room_ids)):
            r2_id = room_ids[j]
            r2_poly = plan.rooms[r2_id].geometry

            shared = _get_shared_boundary(r1_poly, r2_poly)
            if shared is None or shared.is_empty:
                continue

            # Clip shared boundary against free space F = inner \ (wall U window)
            clipped = shared.intersection(plan.free_space_geom)
            if clipped.is_empty:
                continue

            # Subtract existing door portals
            if door_union is not None and not door_union.is_empty:
                remaining = clipped.difference(door_union)
            else:
                remaining = clipped

            lines = _extract_linestrings(remaining)
            for seg in lines:
                # Open passage must have unobstructed width >= 0.50m
                if seg.length >= 0.50:
                    portal_id = f"P{portal_counter:02d}"
                    portal = _build_portal(
                        portal_id=portal_id,
                        portal_type=PortalType.OPEN_PASSAGE,
                        room1_id=r1_id,
                        room2_id=r2_id,
                        threshold_line=seg,
                        r1_poly=r1_poly,
                        r2_poly=r2_poly,
                    )
                    if portal is not None:
                        open_portals.append(portal)
                        portal_counter += 1

    return open_portals


def extract_all_portals(
    plan: MetricFloorPlan,
    raw_doors: Optional[Sequence[Any]] = None,
    raw_entrance: Optional[Any] = None,
) -> List[DoorOrPassagePortal]:
    """Extract all interior door and open-passage portals with canonical sequential IDs.

    Args:
        plan: Normalized metric floor plan contract.
        raw_doors: List of door polygons or geometries.
        raw_entrance: Optional entrance/front_door polygon or geometry.

    Returns:
        List[DoorOrPassagePortal]: Complete list of portals linking interior rooms.
    """
    doors = extract_door_portals(plan, raw_doors, raw_entrance)
    passages = extract_open_passages(plan, doors)

    all_portals: List[DoorOrPassagePortal] = []
    # Sort deterministically
    sorted_raw = sorted(
        doors + passages,
        key=lambda p: (p.room1_id, p.room2_id, p.center_point.x, p.center_point.y),
    )

    for idx, p in enumerate(sorted_raw, start=1):
        updated = p.model_copy(update={"portal_id": f"P{idx:02d}"})
        all_portals.append(updated)

    return all_portals
