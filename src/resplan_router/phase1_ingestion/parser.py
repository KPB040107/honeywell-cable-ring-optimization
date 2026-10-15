import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import shapely
from shapely import MultiPolygon, Polygon
from shapely.wkt import loads as wkt_loads

from resplan_router.contracts import DoorEntity, FloorPlanModel, RoomEntity

logger = logging.getLogger(__name__)

ELIGIBLE_CATEGORIES = {"living", "bedroom", "kitchen", "storage"}
EXCLUDED_CATEGORIES = {"bathroom", "balcony", "parking", "pool", "stair", "garden", "land"}
TRANSIT_CATEGORIES = {"corridor", "hallway", "passage"}


class ResPlanSchemaValidationError(Exception):
    """Raised when an input document does not conform to supported ResPlan schemas."""
    pass


class AmbiguousCoordinateUnitError(Exception):
    """Raised when the coordinate unit cannot be verified deterministically."""
    pass


class GeometryRepairError(Exception):
    """Raised when a geometry repair alters area or topology past acceptable tolerance."""
    pass


def repair_geometry_if_needed(geom: Union[Polygon, MultiPolygon], room_id: str) -> Union[Polygon, MultiPolygon]:
    """Applies targeted geometry repair only if invalid, with strict area drift checks."""
    if geom.is_empty:
        return geom
    if geom.is_valid:
        return geom

    initial_area = geom.area
    logger.warning("Geometry for %s is invalid. Applying targeted repair (make_valid)...", room_id)
    repaired = shapely.make_valid(geom)
    
    # If make_valid produced a geometry collection, extract polygons
    if repaired.geom_type == "GeometryCollection":
        polys = [g for g in repaired.geoms if isinstance(g, (Polygon, MultiPolygon))]
        repaired = shapely.unary_union(polys)

    if not repaired.is_valid:
        repaired = repaired.buffer(0)

    if not repaired.is_valid:
        raise GeometryRepairError(f"Targeted repair failed to produce valid geometry for {room_id}")

    final_area = repaired.area
    area_delta_pct = abs(final_area - initial_area) / max(initial_area, 1e-6) * 100.0
    logger.info(
        "Repaired %s: initial_area=%.3f, final_area=%.3f (delta=%.2f%%)",
        room_id, initial_area, final_area, area_delta_pct
    )
    if area_delta_pct > 1.0:
        raise GeometryRepairError(
            f"Repair of {room_id} caused excessive area change: {area_delta_pct:.2f}% > 1.0% tolerance"
        )
    return repaired


def decompose_multipolygon(geom: Union[Polygon, MultiPolygon], min_area_m2: float = 0.5) -> List[Polygon]:
    """Decomposes a Polygon or MultiPolygon into sorted, sliver-filtered Polygons."""
    if geom.is_empty:
        return []
    polys: List[Polygon] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    else:
        return []

    valid_polys = [p for p in polys if p.area >= min_area_m2]
    # Deterministic spatial sort: primary min_x, secondary min_y
    valid_polys.sort(key=lambda p: (round(p.bounds[0], 4), round(p.bounds[1], 4)))
    return valid_polys


def parse_geometry_input(val: Any) -> Union[Polygon, MultiPolygon]:
    """Parses WKT string or GeoJSON-like coordinate dictionary into Shapely geometry."""
    if isinstance(val, (Polygon, MultiPolygon)):
        return val
    if isinstance(val, str):
        val = val.strip()
        if not val or val.upper() == "MULTIPOLYGON EMPTY" or val.upper() == "POLYGON EMPTY":
            return Polygon()
        return wkt_loads(val)
    if isinstance(val, dict):
        if "type" in val and "coordinates" in val:
            return shapely.geometry.shape(val)
        raise ResPlanSchemaValidationError(f"Invalid coordinate dict structure: {val.keys()}")
    raise ResPlanSchemaValidationError(f"Unsupported geometry type: {type(val)}")


def parse_resplan_json(
    file_path_or_dict: Union[str, Path, Dict[str, Any]],
    scale_factor: float = 1.0
) -> FloorPlanModel:
    """Ingests and validates a ResPlan floor plan document into a FloorPlanModel."""
    if isinstance(file_path_or_dict, (str, Path)):
        p = Path(file_path_or_dict)
        if not p.is_file():
            raise FileNotFoundError(f"Input file not found: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(file_path_or_dict, dict):
        data = file_path_or_dict
    else:
        raise ResPlanSchemaValidationError(f"Invalid input type: {type(file_path_or_dict)}")

    # 1. Schema Validation: Check required top-level metadata
    if "id" not in data:
        raise ResPlanSchemaValidationError("Missing required top-level field: 'id'")
    plan_id = str(data["id"])

    # Explicit unit validation
    coord_unit = data.get("coordinate_unit")
    if not coord_unit or coord_unit not in {"meter", "pixel"}:
        raise AmbiguousCoordinateUnitError(
            f"Document '{plan_id}' has missing or ambiguous coordinate_unit: '{coord_unit}'. "
            "Must be explicitly 'meter' or 'pixel'."
        )

    # 2. Extract and Repair Rooms
    rooms: List[RoomEntity] = []
    semantic_room_keys = list(ELIGIBLE_CATEGORIES) + list(EXCLUDED_CATEGORIES) + list(TRANSIT_CATEGORIES)
    
    for category in semantic_room_keys:
        if category not in data:
            continue
        raw_geom = parse_geometry_input(data[category])
        if raw_geom.is_empty:
            continue
        
        repaired = repair_geometry_if_needed(raw_geom, f"raw_{category}")
        polys = decompose_multipolygon(repaired)
        
        for idx, poly in enumerate(polys):
            room_id = f"{category}_{idx}"
            is_elig = category in ELIGIBLE_CATEGORIES
            is_trans = category in TRANSIT_CATEGORIES
            rooms.append(
                RoomEntity(
                    room_id=room_id,
                    category=category,
                    polygon_wkt=poly.wkt,
                    area_m2=round(poly.area * (scale_factor ** 2), 4),
                    is_eligible=is_elig,
                    is_transit=is_trans,
                )
            )

    if not rooms:
        raise ResPlanSchemaValidationError(f"No valid rooms found in plan '{plan_id}'")

    # 3. Extract Doors and Front Doors
    doors: List[DoorEntity] = []
    if "door" in data:
        door_geom = parse_geometry_input(data["door"])
        if not door_geom.is_empty:
            door_polys = decompose_multipolygon(door_geom, min_area_m2=0.01)
            for d_idx, d_poly in enumerate(door_polys):
                doors.append(
                    DoorEntity(
                        door_id=f"door_{d_idx}",
                        polygon_wkt=d_poly.wkt,
                        is_front_door=False
                    )
                )

    if "front_door" in data:
        fd_geom = parse_geometry_input(data["front_door"])
        if not fd_geom.is_empty:
            fd_polys = decompose_multipolygon(fd_geom, min_area_m2=0.01)
            for fd_idx, fd_poly in enumerate(fd_polys):
                doors.append(
                    DoorEntity(
                        door_id=f"front_door_{fd_idx}",
                        polygon_wkt=fd_poly.wkt,
                        is_front_door=True
                    )
                )

    # 4. Obstacle Mask (wall + window + front_door)
    obstacle_geoms: List[Union[Polygon, MultiPolygon]] = []
    for obst_key in ["wall", "window", "front_door"]:
        if obst_key in data:
            g = parse_geometry_input(data[obst_key])
            if not g.is_empty:
                obstacle_geoms.append(g)
    
    if obstacle_geoms:
        obstacle_union = shapely.unary_union(obstacle_geoms)
        obstacle_union_wkt = obstacle_union.wkt
    else:
        obstacle_union_wkt = "POLYGON EMPTY"

    # 5. Inner Polygon Boundary
    if "inner" in data and data["inner"]:
        inner_geom = parse_geometry_input(data["inner"])
        inner_wkt = inner_geom.wkt
    else:
        # Fallback: unary union of all rooms and doors
        all_interior = [wkt_loads(r.polygon_wkt) for r in rooms] + [wkt_loads(d.polygon_wkt) for d in doors]
        inner_geom = shapely.unary_union(all_interior)
        inner_wkt = inner_geom.wkt

    return FloorPlanModel(
        plan_id=plan_id,
        scale_factor=scale_factor,
        inner_polygon_wkt=inner_wkt,
        obstacle_union_wkt=obstacle_union_wkt,
        rooms=rooms,
        doors=doors,
        nodes=[]
    )
