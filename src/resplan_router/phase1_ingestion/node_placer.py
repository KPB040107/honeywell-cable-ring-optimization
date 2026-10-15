import logging
from typing import List, Optional
import shapely
from shapely.geometry import Point, Polygon
from shapely.ops import polylabel
from shapely.wkt import loads as wkt_loads

from resplan_router.contracts import DeviceNode, Point2D, RoomEntity

logger = logging.getLogger(__name__)


class InfeasibleNodePlacementError(Exception):
    """Raised when a room cannot safely accommodate a device node respecting clearance constraints."""
    pass


def find_safe_node_point(
    room_polygon: Polygon,
    obstacle_mask: Polygon,
    room_id: str,
    erosion_margin: float = 0.25,
    min_wall_clearance: float = 0.20,
    grid_size: float = 0.05
) -> Point2D:
    """
    Deterministically computes a safe interior device point using polylabel and grid snapping.
    Independently verifies post-snap clearance >= min_wall_clearance (0.20 m) and obstacle avoidance.
    """
    if room_polygon.is_empty:
        raise InfeasibleNodePlacementError(f"Room '{room_id}' has empty geometry.")

    # 1. Erode room by candidate erosion margin (0.25 m)
    safe_poly = room_polygon.buffer(-erosion_margin)
    candidate_point: Optional[Point] = None

    if not safe_poly.is_empty:
        centroid = room_polygon.centroid
        if safe_poly.contains(centroid):
            candidate_point = centroid
        else:
            candidate_point = polylabel(safe_poly, tolerance=0.01)
    else:
        # If eroded polygon is empty, room may be narrow; test polylabel on raw polygon
        candidate_point = polylabel(room_polygon, tolerance=0.01)

    if candidate_point is None or candidate_point.is_empty:
        raise InfeasibleNodePlacementError(f"Unable to find interior candidate point for '{room_id}'")

    raw_pt = Point2D(x=candidate_point.x, y=candidate_point.y)
    snapped_pt = raw_pt.snapped(grid_size)

    # 2. Helper to validate hard invariants
    def is_valid_placement(pt: Point2D) -> bool:
        geom_pt = Point(pt.x, pt.y)
        if not room_polygon.contains(geom_pt):
            return False
        dist_to_wall = geom_pt.distance(room_polygon.boundary)
        if dist_to_wall < min_wall_clearance - 1e-6:
            return False
        if not obstacle_mask.is_empty and geom_pt.intersects(obstacle_mask):
            return False
        return True

    # 3. Check snapped point
    if is_valid_placement(snapped_pt):
        return snapped_pt

    # 4. Deterministic neighborhood search (8-connected grid at grid_size)
    logger.warning("Snapped point %s violated clearance in room %s; testing grid neighborhood...", snapped_pt, room_id)
    offsets = [
        (0, grid_size), (0, -grid_size), (grid_size, 0), (-grid_size, 0),
        (grid_size, grid_size), (grid_size, -grid_size), (-grid_size, grid_size), (-grid_size, -grid_size)
    ]
    
    # Sort neighbors by proximity to raw candidate point
    candidate_neighbors = [
        Point2D(x=round(snapped_pt.x + dx, 4), y=round(snapped_pt.y + dy, 4))
        for dx, dy in offsets
    ]
    candidate_neighbors.sort(key=lambda p: p.euclidean_distance(raw_pt))

    for neighbor_pt in candidate_neighbors:
        if is_valid_placement(neighbor_pt):
            logger.info("Found valid neighbor placement at %s for room %s", neighbor_pt, room_id)
            return neighbor_pt

    # 5. Infeasible placement
    dist = Point(snapped_pt.x, snapped_pt.y).distance(room_polygon.boundary)
    raise InfeasibleNodePlacementError(
        f"Room '{room_id}' cannot accommodate a device node with required {min_wall_clearance} m wall clearance. "
        f"Maximum achievable clearance after snapping is {dist:.3f} m."
    )


def place_nodes(
    rooms: List[RoomEntity],
    obstacle_mask_wkt: str = "POLYGON EMPTY",
    min_wall_clearance: float = 0.20,
    erosion_margin: float = 0.25,
    grid_size: float = 0.05
) -> List[DeviceNode]:
    """Places exactly 1 safe node per eligible room. Excluded rooms receive 0 nodes."""
    obstacle_mask = wkt_loads(obstacle_mask_wkt) if obstacle_mask_wkt else Polygon()
    nodes: List[DeviceNode] = []
    node_idx = 1

    for room in rooms:
        if not room.is_eligible:
            continue
        poly = wkt_loads(room.polygon_wkt)
        safe_pt = find_safe_node_point(
            room_polygon=poly,
            obstacle_mask=obstacle_mask,
            room_id=room.room_id,
            erosion_margin=erosion_margin,
            min_wall_clearance=min_wall_clearance,
            grid_size=grid_size
        )
        nodes.append(
            DeviceNode(
                node_id=f"N{node_idx}",
                host_room_id=room.room_id,
                coord=safe_pt
            )
        )
        node_idx += 1

    return nodes
