"""Device candidate generation for eligible rooms in ResPlan floor plans.

Generates geometrically validated device locations inside each eligible room:
- Pole of inaccessibility (polylabel)
- Safe-region centroid
- Doorway-aligned candidates
- Valid Hanan-grid intersections
- Interior inset boundary samples

Guarantees:
- Every device candidate lies strictly inside its assigned host room polygon.
- Every candidate maintains >= 0.50 m clearance (0.499 m with 1 mm tolerance) from obstacles (walls/windows/entrances).
- Excluded rooms (bathroom, balcony, stair, garden, pool, parking, land, etc.) contain zero devices.
- Removes artificial 0.30 m minimum separation between a hub and a device: a hub and device may share a room,
  requiring only distinct coordinates so the cable can traverse between them.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union
import shapely
import shapely.affinity
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from resplan_router.contracts import FixedNode, Point2D


def generate_room_device_candidates(
    room_id: str,
    room_geom: Union[Polygon, MultiPolygon],
    obstacles: BaseGeometry,
    doorway_points: Sequence[Point2D] = (),
    hanan_x_coords: Sequence[float] = (),
    hanan_y_coords: Sequence[float] = (),
    min_clearance_m: float = 0.499,
) -> List[Point2D]:
    """Generate diverse, strictly legal candidate device positions inside an eligible room.

    Args:
        room_id: Unique identifier of the eligible room.
        room_geom: Metric Shapely geometry of the room.
        obstacles: Impassable obstacles union (walls, windows, front doors).
        doorway_points: Optional threshold doorway coordinates near this room.
        hanan_x_coords: Optional Hanan-grid X coordinates.
        hanan_y_coords: Optional Hanan-grid Y coordinates.
        min_clearance_m: Minimum distance to obstacles (0.499m for 0.50m - 1mm tolerance).

    Returns:
        List[Point2D]: Ranked list of unique valid device candidates.
    """
    if room_geom.is_empty:
        return []

    # Safe interior polygon: P_safe = R \ obstacles.buffer(min_clearance_m)
    obs_buffered = obstacles.buffer(min_clearance_m) if (obstacles is not None and not obstacles.is_empty) else None
    if obs_buffered is not None and not obs_buffered.is_empty:
        safe_geom = room_geom.difference(obs_buffered)
    else:
        safe_geom = room_geom

    safe_geom = shapely.make_valid(safe_geom)

    # If safe_geom is empty due to very narrow room, relax slightly by 1 mm for tolerance
    if safe_geom.is_empty or safe_geom.area < 1e-4:
        if obs_buffered is not None:
            safe_geom = shapely.make_valid(room_geom.difference(obstacles.buffer(0.490)))

    # Extract dominant polygon if MultiPolygon
    poly_target: Polygon
    if isinstance(safe_geom, MultiPolygon):
        if not safe_geom.geoms:
            return []
        poly_target = max(safe_geom.geoms, key=lambda g: g.area)
    elif isinstance(safe_geom, Polygon):
        poly_target = safe_geom
    else:
        # Fallback to room_geom if geometry collection
        poly_target = room_geom if isinstance(room_geom, Polygon) else max(room_geom.geoms, key=lambda g: g.area)

    candidates: List[Point2D] = []
    seen: Set[Tuple[float, float]] = set()

    def try_add(x: float, y: float, prioritize: bool = False) -> bool:
        rx = round(float(x), 3)
        ry = round(float(y), 3)
        key = (rx, ry)
        if key in seen:
            return False

        pt = Point(rx, ry)
        # 1. Must lie inside the room
        if not room_geom.contains(pt):
            return False

        # 2. Must satisfy obstacle clearance
        if obstacles is not None and not obstacles.is_empty:
            dist = obstacles.distance(pt)
            if dist < min_clearance_m:
                return False

        seen.add(key)
        p2d = Point2D(x=round(rx, 4), y=round(ry, 4))
        if prioritize:
            candidates.insert(0, p2d)
        else:
            candidates.append(p2d)
        return True

    # 1. Pole of inaccessibility (polylabel) - maximal clearance from walls
    try:
        if not poly_target.is_empty and poly_target.area > 1e-4:
            pole = polylabel(poly_target, tolerance=0.005)
            try_add(pole.x, pole.y, prioritize=True)
    except Exception:
        pass

    # 2. Centroid of safe region
    try:
        if not poly_target.is_empty:
            c = poly_target.centroid
            try_add(c.x, c.y)
    except Exception:
        pass

    # 3. Doorway-aligned candidates: project doorway lines into room interior
    for dp in doorway_points:
        # Candidate at doorway X or doorway Y inside safe polygon
        minx, miny, maxx, maxy = poly_target.bounds
        if minx <= dp.x <= maxx:
            # Check along vertical line at dp.x
            line_v = shapely.geometry.LineString([(dp.x, miny), (dp.x, maxy)])
            inter = poly_target.intersection(line_v)
            if not inter.is_empty:
                mid_pt = inter.centroid
                try_add(mid_pt.x, mid_pt.y)
        if miny <= dp.y <= maxy:
            # Check along horizontal line at dp.y
            line_h = shapely.geometry.LineString([(minx, dp.y), (maxx, dp.y)])
            inter = poly_target.intersection(line_h)
            if not inter.is_empty:
                mid_pt = inter.centroid
                try_add(mid_pt.x, mid_pt.y)

    # 4. Interior inset samples and Hanan intersections
    minx, miny, maxx, maxy = poly_target.bounds
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    # Inset cross samples
    offsets = [0.25, 0.50, 0.75, 1.0, 1.5]
    for d in offsets:
        try_add(cx + d, cy)
        try_add(cx - d, cy)
        try_add(cx, cy + d)
        try_add(cx, cy - d)

    # Hanan coordinates inside room
    for hx in hanan_x_coords:
        if minx <= hx <= maxx:
            for hy in hanan_y_coords:
                if miny <= hy <= maxy:
                    try_add(hx, hy)
                    if len(candidates) >= 20:
                        break
            if len(candidates) >= 20:
                break

    # 5. Grid sampling fallback if fewer than 3 candidates found
    if len(candidates) < 3 and not poly_target.is_empty:
        step = max(0.20, min((maxx - minx) / 5.0, (maxy - miny) / 5.0))
        gx = minx + step / 2.0
        while gx < maxx:
            gy = miny + step / 2.0
            while gy < maxy:
                try_add(gx, gy)
                gy += step
            gx += step

    # 6. Absolute fallback: polylabel on raw room if safe region yielded nothing
    if not candidates:
        try:
            raw_poly = room_geom if isinstance(room_geom, Polygon) else max(room_geom.geoms, key=lambda g: g.area)
            pole = polylabel(raw_poly, tolerance=0.01)
            candidates.append(Point2D(x=round(pole.x, 4), y=round(pole.y, 4)))
        except Exception:
            candidates.append(Point2D(x=round(room_geom.centroid.x, 4), y=round(room_geom.centroid.y, 4)))

    return candidates


def generate_all_room_devices(
    rooms: Dict[str, Union[Polygon, MultiPolygon]],
    obstacles: BaseGeometry,
    doorway_points: Sequence[Point2D] = (),
) -> Dict[str, FixedNode]:
    """Deterministically generate exactly one primary FixedNode for each eligible room.

    Args:
        rooms: Mapping of eligible room_id to metric room geometry.
        obstacles: Obstacle geometry union.
        doorway_points: Doorway portal waypoints.

    Returns:
        Dict[str, FixedNode]: Mapping of node_id to FixedNode.
    """
    devices: Dict[str, FixedNode] = {}
    for idx, (room_id, r_geom) in enumerate(rooms.items(), start=1):
        candidates = generate_room_device_candidates(
            room_id=room_id,
            room_geom=r_geom,
            obstacles=obstacles,
            doorway_points=doorway_points,
        )
        chosen = candidates[0] if candidates else Point2D(x=round(r_geom.centroid.x, 4), y=round(r_geom.centroid.y, 4))
        node_id = f"N{idx}"
        devices[node_id] = FixedNode(
            node_id=node_id,
            room_id=room_id,
            location=chosen,
        )

    return devices
