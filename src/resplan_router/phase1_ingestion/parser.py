"""Polygon healing, room classification, free-space extraction, and floor plan parsing.

Enforces:
- Shapely make_valid() on all input geometry.
- Sliver polygon filtering (< 2.0 m^2 per Slide 3 benchmark note, unless structural feature).
- Eligible vs. excluded room classification per Slide 2.1.
- Free-space F = inner \ (wall ∪ window) and obstacles O = wall ∪ window ∪ front_door.
"""

from typing import Any, Dict, List, Optional, Tuple, Union
import shapely
import shapely.affinity
import shapely.geometry.polygon
import shapely.ops
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    EligibleOrExcludedRoom,
    MetricFloorPlan,
    RoomCategory,
)
from resplan_router.phase1_ingestion.scale_converter import (
    compute_scale_factor,
    normalize_to_metric,
    parse_geometry,
)


def parse_and_heal_polygon(
    raw_coords: Any,
    min_area: float = 2.0,
    is_structural_feature: bool = False,
) -> Union[Polygon, MultiPolygon]:
    """Convert raw coordinates or geometry into a valid Shapely Polygon or MultiPolygon.

    Applies shapely.make_valid() to resolve self-intersections and duplicate vertices.
    Filters out degenerate polygonal slivers with area < min_area (default 2.0 m^2),
    unless is_structural_feature is True (e.g. walls, windows, doors).

    Args:
        raw_coords: Raw vertex array, WKT, dict, or Shapely geometry.
        min_area: Minimum area in metres squared to retain for rooms (default 2.0 m^2).
        is_structural_feature: True if geometry is a wall, window, or door.

    Returns:
        Union[Polygon, MultiPolygon]: Healed valid 2D geometry.
    """
    geom = parse_geometry(raw_coords)
    if not geom.is_valid:
        geom = shapely.make_valid(geom)

    # Extract 2D polygon components (strip points/linestrings from collapsed self-intersections)
    if isinstance(geom, Polygon):
        polygons = [geom] if not geom.is_empty else []
    elif isinstance(geom, (MultiPolygon, GeometryCollection)):
        polygons = [
            g for g in geom.geoms
            if isinstance(g, Polygon) and not g.is_empty
        ]
    else:
        polygons = []

    if not polygons:
        return Polygon()

    # Apply sliver filtering for non-structural geometries
    if not is_structural_feature and min_area > 0.0:
        polygons = [p for p in polygons if p.area >= min_area]

    if not polygons:
        return Polygon()

    if len(polygons) == 1:
        return shapely.geometry.polygon.orient(polygons[0], sign=1.0)
    else:
        oriented = [shapely.geometry.polygon.orient(p, sign=1.0) for p in polygons]
        return MultiPolygon(oriented)


def classify_room(category_str: str) -> Tuple[bool, RoomCategory]:
    """Classify a room category string into eligible vs. excluded taxonomy.

    Eligible rooms (Slide 2.1):
        - living, bedroom, kitchen, storage
    Excluded rooms/spaces (Slide 2.1):
        - bathroom, balcony, stair, garden, pool, parking, land, corridor

    Args:
        category_str: Input room category name (case-insensitive, handles typos/suffixes).

    Returns:
        Tuple[bool, RoomCategory]: (is_eligible, room_category_enum)

    Raises:
        ValueError: If category cannot be mapped to any known room category.
    """
    s = category_str.strip().lower()

    if "living" in s:
        cat = RoomCategory.LIVING
    elif "bed" in s:
        cat = RoomCategory.BEDROOM
    elif "kitchen" in s:
        cat = RoomCategory.KITCHEN
    elif "stor" in s:
        cat = RoomCategory.STORAGE
    elif "bath" in s or "wc" in s or "toilet" in s:
        cat = RoomCategory.BATHROOM
    elif "balc" in s:
        cat = RoomCategory.BALCONY
    elif "stair" in s or "step" in s:
        cat = RoomCategory.STAIR
    elif "garden" in s:
        cat = RoomCategory.GARDEN
    elif "pool" in s:
        cat = RoomCategory.POOL
    elif "park" in s or "garage" in s:
        cat = RoomCategory.PARKING
    elif "land" in s:
        cat = RoomCategory.LAND
    elif "corridor" in s or "hall" in s or "passage" in s or "aisle" in s or "entrance" in s:
        cat = RoomCategory.CORRIDOR
    else:
        try:
            cat = RoomCategory(s)
        except ValueError as exc:
            raise ValueError(f"Unrecognized room category string: '{category_str}'") from exc

    is_eligible = cat in (
        RoomCategory.LIVING,
        RoomCategory.BEDROOM,
        RoomCategory.KITCHEN,
        RoomCategory.STORAGE,
    )
    return is_eligible, cat


def build_obstacles_and_freespace(
    scaled_inner: BaseGeometry,
    walls: List[BaseGeometry],
    windows: List[BaseGeometry],
    doors: Optional[List[BaseGeometry]] = None,
    front_doors: Optional[List[BaseGeometry]] = None,
) -> Tuple[BaseGeometry, BaseGeometry]:
    """Construct impassable obstacle union O and routable free space F.

    Formula:
        Obstacle Union O: unary_union(walls ∪ windows ∪ front_doors)
        Routable Free Space F: scaled_inner \ unary_union(walls ∪ windows)

    Note:
        Interior doors are routable and remain in free space.
        Building entrances (front_door) are impassable boundaries added to obstacles.

    Args:
        scaled_inner: Enclosing inner boundary polygon in metric coordinates.
        walls: List of wall geometries in metres.
        windows: List of window geometries in metres.
        doors: Optional list of interior door geometries in metres.
        front_doors: Optional list of front_door / entrance geometries in metres.

    Returns:
        Tuple[BaseGeometry, BaseGeometry]: (obstacles, free_space)
    """
    # 1. Obstacle union O = wall ∪ window ∪ front_door
    obs_components: List[BaseGeometry] = []
    obs_components.extend([w for w in walls if w and not w.is_empty])
    obs_components.extend([w for w in windows if w and not w.is_empty])
    if front_doors:
        obs_components.extend([fd for fd in front_doors if fd and not fd.is_empty])

    if obs_components:
        obstacles = shapely.unary_union(obs_components)
    else:
        obstacles = Polygon()
    obstacles = shapely.make_valid(obstacles)

    # 2. Free space F = inner \ (wall ∪ window)
    wall_win_components: List[BaseGeometry] = []
    wall_win_components.extend([w for w in walls if w and not w.is_empty])
    wall_win_components.extend([w for w in windows if w and not w.is_empty])

    if wall_win_components:
        wall_win_union = shapely.unary_union(wall_win_components)
        free_space = scaled_inner.difference(wall_win_union)
    else:
        free_space = scaled_inner
    free_space = shapely.make_valid(free_space)

    return obstacles, free_space


def _is_single_polygon_coords(val: Any) -> bool:
    """True if val represents a single polygon's exterior ring [[x1, y1], [x2, y2], ...]."""
    if isinstance(val, (list, tuple)) and len(val) > 0:
        first = val[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            if isinstance(first[0], (int, float)):
                return True
    return False


def _extract_geometry_list(
    raw_json: Dict[str, Any],
    keys: List[str],
) -> List[Any]:
    """Extract raw geometries from raw_json under any matching key."""
    results: List[Any] = []
    for k in keys:
        if k not in raw_json:
            continue
        val = raw_json[k]
        if val is None:
            continue
        if _is_single_polygon_coords(val):
            results.append(val)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if item is not None:
                    results.append(item)
        else:
            results.append(val)
    return results


def parse_floor_plan(
    raw_json: Dict[str, Any],
    strict_clearance: bool = True,
) -> MetricFloorPlan:
    """Parse, heal, scale normalize, and assemble a complete MetricFloorPlan.

    Args:
        raw_json: Floor plan dictionary in ResPlan or competition schema.
        strict_clearance: If True, raise ValueError on node distance < 0.499m; if False, retain coordinates.

    Returns:
        MetricFloorPlan: Immutable validated Pydantic model in metres.
    """
    from resplan_router.phase1_ingestion.node_loader import load_fixed_nodes

    plan_id = str(raw_json.get("plan_id", raw_json.get("id", "plan_0")))
    scale = compute_scale_factor(raw_json)
    total_area_m2 = float(raw_json["area"])

    # 1. Scale and heal inner polygon
    raw_inner = parse_geometry(raw_json["inner"])
    min_x, min_y, _, _ = raw_inner.bounds
    origin_offset = (min_x, min_y)

    scaled_inner = normalize_to_metric(raw_inner, scale, origin_offset)
    healed_inner = parse_and_heal_polygon(scaled_inner, min_area=0.0, is_structural_feature=True)

    # 2. Extract structural features (walls, windows, doors, front_doors)
    raw_walls = _extract_geometry_list(raw_json, ["wall", "walls"])
    raw_windows = _extract_geometry_list(raw_json, ["window", "windows"])
    raw_doors = _extract_geometry_list(raw_json, ["door", "doors", "interior_door", "interior_doors"])
    raw_front_doors = _extract_geometry_list(raw_json, ["front_door", "front_doors", "entrance"])

    metric_walls = [
        parse_and_heal_polygon(normalize_to_metric(parse_geometry(w), scale, origin_offset), min_area=0.0, is_structural_feature=True)
        for w in raw_walls
    ]
    metric_windows = [
        parse_and_heal_polygon(normalize_to_metric(parse_geometry(w), scale, origin_offset), min_area=0.0, is_structural_feature=True)
        for w in raw_windows
    ]
    metric_doors = [
        parse_and_heal_polygon(normalize_to_metric(parse_geometry(d), scale, origin_offset), min_area=0.0, is_structural_feature=True)
        for d in raw_doors
    ]
    metric_front_doors = [
        parse_and_heal_polygon(normalize_to_metric(parse_geometry(fd), scale, origin_offset), min_area=0.0, is_structural_feature=True)
        for fd in raw_front_doors
    ]

    metric_walls = [w for w in metric_walls if not w.is_empty]
    metric_windows = [w for w in metric_windows if not w.is_empty]
    metric_doors = [d for d in metric_doors if not d.is_empty]
    metric_front_doors = [fd for fd in metric_front_doors if not fd.is_empty]

    # 3. Build obstacles and free space
    obstacles, free_space = build_obstacles_and_freespace(
        healed_inner,
        metric_walls,
        metric_windows,
        metric_doors,
        metric_front_doors,
    )

    # 4. Extract and heal rooms
    rooms_dict: Dict[str, EligibleOrExcludedRoom] = {}
    room_idx = 1

    if "rooms" in raw_json and isinstance(raw_json["rooms"], list):
        for room_entry in raw_json["rooms"]:
            if isinstance(room_entry, dict):
                cat_str = room_entry.get("category", room_entry.get("name", "living"))
                geom_raw = room_entry.get("polygon", room_entry.get("geometry", room_entry.get("coordinates")))
                r_id = str(room_entry.get("room_id", room_entry.get("id", f"R{room_idx:02d}")))
            else:
                continue

            is_eligible, room_cat = classify_room(cat_str)
            raw_room_geom = parse_geometry(geom_raw)
            scaled_room = normalize_to_metric(raw_room_geom, scale, origin_offset)
            healed_room = parse_and_heal_polygon(scaled_room, min_area=2.0, is_structural_feature=False)

            if healed_room.is_empty or healed_room.area < 2.0:
                continue

            rooms_dict[r_id] = EligibleOrExcludedRoom(
                room_id=r_id,
                category=room_cat.value,
                is_eligible=is_eligible,
                area_m2=round(healed_room.area, 4),
                polygon_wkt=healed_room.wkt,
            )
            room_idx += 1
    else:
        all_room_categories = [
            "living", "bedroom", "kitchen", "storage",
            "bathroom", "balcony", "balacony", "stair",
            "garden", "pool", "parking", "land", "corridor",
        ]
        for cat_key in all_room_categories:
            if cat_key not in raw_json:
                continue
            cat_geoms = _extract_geometry_list(raw_json, [cat_key])
            for geom_item in cat_geoms:
                is_eligible, room_cat = classify_room(cat_key)
                raw_room_geom = parse_geometry(geom_item)
                scaled_room = normalize_to_metric(raw_room_geom, scale, origin_offset)
                healed_room = parse_and_heal_polygon(scaled_room, min_area=2.0, is_structural_feature=False)

                if healed_room.is_empty or healed_room.area < 2.0:
                    continue

                r_id = f"R{room_idx:02d}"
                rooms_dict[r_id] = EligibleOrExcludedRoom(
                    room_id=r_id,
                    category=room_cat.value,
                    is_eligible=is_eligible,
                    area_m2=round(healed_room.area, 4),
                    polygon_wkt=healed_room.wkt,
                )
                room_idx += 1

    # 5. Load fixed nodes for eligible rooms
    eligible_rooms_geom = {
        r.room_id: shapely.from_wkt(r.polygon_wkt)
        for r in rooms_dict.values()
        if r.is_eligible
    }
    nodes_dict = load_fixed_nodes(
        raw_json=raw_json,
        scale=scale,
        eligible_rooms=eligible_rooms_geom,
        obstacles=obstacles,
        origin_offset=origin_offset,
        strict_clearance=strict_clearance,
    )

    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=scale,
        total_area_m2=round(total_area_m2, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )
