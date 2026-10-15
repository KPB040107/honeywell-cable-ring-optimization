import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import shapely
from shapely import MultiPolygon, Polygon
from shapely.affinity import scale as shapely_scale
from shapely.wkt import loads as wkt_loads

from resplan_router.contracts import DoorEntity, FloorPlanModel, RoomEntity
from resplan_router.phase1_ingestion.scale_normalizer import (
    AmbiguousCoordinateUnitError,
    compute_metric_scale_factor,
)

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = {"resplan_wkt_v1", "resplan_geo_v1"}
ELIGIBLE_CATEGORIES = {"living", "bedroom", "kitchen", "storage"}
EXCLUDED_CATEGORIES = {"bathroom", "balcony", "parking", "pool", "stair", "garden", "land"}
TRANSIT_CATEGORIES = {"corridor", "hallway", "passage"}


class ResPlanSchemaValidationError(Exception):
    """Raised when an input document does not conform to supported ResPlan schemas."""
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


def apply_metric_scaling(geom: Union[Polygon, MultiPolygon], scale_factor: float) -> Union[Polygon, MultiPolygon]:
    """Scales a Shapely geometry consistently across all coordinates."""
    if geom.is_empty or abs(scale_factor - 1.0) < 1e-6:
        return geom
    return shapely_scale(geom, xfact=scale_factor, yfact=scale_factor, origin=(0, 0))


def parse_resplan_json(
    file_path_or_dict: Union[str, Path, Dict[str, Any]]
) -> FloorPlanModel:
    """Ingests, validates, and normalizes a ResPlan floor plan document into a FloorPlanModel."""
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

    # Mandatory schema_version validation
    if "schema_version" not in data or not data["schema_version"]:
        raise ResPlanSchemaValidationError(
            f"Missing required top-level field: 'schema_version'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    schema_version = str(data["schema_version"])
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ResPlanSchemaValidationError(
            f"Unsupported schema_version: '{schema_version}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    # Explicit unit validation
    coord_unit = data.get("coordinate_unit")
    if not coord_unit or coord_unit not in {"meter", "pixel"}:
        raise AmbiguousCoordinateUnitError(
            f"Document '{plan_id}' has missing or ambiguous coordinate_unit: '{coord_unit}'. "
            "Must be explicitly 'meter' or 'pixel'."
        )

    # 2. Compute Raw Areas to determine Scale Factor
    semantic_room_keys = list(ELIGIBLE_CATEGORIES) + list(EXCLUDED_CATEGORIES) + list(TRANSIT_CATEGORIES)
    raw_room_polys: List[Tuple[str, Polygon]] = []
    total_raw_room_area = 0.0

    for category in semantic_room_keys:
        if category not in data:
            continue
        raw_geom = parse_geometry_input(data[category])
        if raw_geom.is_empty:
            continue
        repaired = repair_geometry_if_needed(raw_geom, f"raw_{category}")
        polys = decompose_multipolygon(repaired)
        for poly in polys:
            raw_room_polys.append((category, poly))
            total_raw_room_area += poly.area

    if not raw_room_polys:
        raise ResPlanSchemaValidationError(f"No valid rooms found in plan '{plan_id}'")

    scale_factor, scale_msg = compute_metric_scale_factor(data, total_raw_room_area)

    # 3. Apply Scaling to All Room Geometries
    rooms: List[RoomEntity] = []
    category_counts: Dict[str, int] = {}
    for category, poly in raw_room_polys:
        scaled_poly = apply_metric_scaling(poly, scale_factor)
        idx = category_counts.get(category, 0)
        category_counts[category] = idx + 1
        room_id = f"{category}_{idx}"
        is_elig = category in ELIGIBLE_CATEGORIES
        is_trans = category in TRANSIT_CATEGORIES
        rooms.append(
            RoomEntity(
                room_id=room_id,
                category=category,
                polygon_wkt=scaled_poly.wkt,
                area_m2=round(scaled_poly.area, 4),
                is_eligible=is_elig,
                is_transit=is_trans,
            )
        )

    # 4. Extract and Scale Doors and Front Doors
    doors: List[DoorEntity] = []
    if "door" in data:
        door_geom = parse_geometry_input(data["door"])
        if not door_geom.is_empty:
            scaled_doors = apply_metric_scaling(door_geom, scale_factor)
            door_polys = decompose_multipolygon(scaled_doors, min_area_m2=0.005)
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
            scaled_fd = apply_metric_scaling(fd_geom, scale_factor)
            fd_polys = decompose_multipolygon(scaled_fd, min_area_m2=0.005)
            for fd_idx, fd_poly in enumerate(fd_polys):
                doors.append(
                    DoorEntity(
                        door_id=f"front_door_{fd_idx}",
                        polygon_wkt=fd_poly.wkt,
                        is_front_door=True
                    )
                )

    # 5. Extract, Scale, and Build Obstacle Mask (wall + window + front_door)
    obstacle_geoms: List[Union[Polygon, MultiPolygon]] = []
    for obst_key in ["wall", "window", "front_door"]:
        if obst_key in data:
            g = parse_geometry_input(data[obst_key])
            if not g.is_empty:
                scaled_obst = apply_metric_scaling(g, scale_factor)
                obstacle_geoms.append(scaled_obst)
    
    if obstacle_geoms:
        obstacle_union = shapely.unary_union(obstacle_geoms)
        obstacle_union_wkt = obstacle_union.wkt
    else:
        obstacle_union_wkt = "POLYGON EMPTY"

    # 6. Extract and Scale Inner Polygon Boundary
    if "inner" in data and data["inner"]:
        inner_geom = parse_geometry_input(data["inner"])
        scaled_inner = apply_metric_scaling(inner_geom, scale_factor)
        inner_wkt = scaled_inner.wkt
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
