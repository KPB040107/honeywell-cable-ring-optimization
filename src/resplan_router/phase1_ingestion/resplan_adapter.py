"""ResPlan Geometry Adapter.

Directly transforms raw Shapely dataset records from ResPlan.pkl into metric
contracts (MetricFloorPlan) without intermediate JSON serialization:
- Computes exact scale factor: scale = sqrt(inner.area / record["area"]).
- Supports Polygon and MultiPolygon inner boundaries.
- Splits room categories into deterministic room instances (bedroom_01, bedroom_02, living_01, etc.).
- Sorts components deterministically by centroid (y, x) and bounds so room IDs are perfectly repeatable.
- Classifies rooms:
    Eligible: bedroom, living, kitchen, storage.
    Excluded: bathroom, balcony, stair, garden, pool, parking, land, corridor, etc.
- Filters out sub-2 m² components after metric scaling.
- Constructs free-space and obstacle geometries from inner, wall, window, and front_door.
- Derives doorway geometry for interior portals (front_door treated as exterior entrance only).
- Generates compliant in-room device positions (one per eligible room, >= 0.50 m clearance).
- Serializes geometries to browser-safe GeoJSON via shapely.geometry.mapping.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import shapely
import shapely.affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
import shapely.ops

from resplan_router.contracts import (
    EligibleOrExcludedRoom,
    FixedNode,
    MetricFloorPlan,
    Point2D,
    RoomCategory,
)
from resplan_router.phase1_ingestion.device_candidates import generate_all_room_devices
from resplan_router.phase1_ingestion.parser import (
    build_obstacles_and_freespace,
    classify_room,
    parse_and_heal_polygon,
)
from resplan_router.phase1_ingestion.scale_converter import normalize_to_metric


def _split_polygon_components(geom: Any) -> List[Polygon]:
    """Extract individual non-empty Polygon components from Shapely geometry."""
    if geom is None:
        return []
    if isinstance(geom, Polygon):
        return [geom] if not geom.is_empty else []
    if isinstance(geom, (MultiPolygon, GeometryCollection)):
        polys: List[Polygon] = []
        for g in geom.geoms:
            if isinstance(g, Polygon) and not g.is_empty:
                polys.append(g)
            elif isinstance(g, MultiPolygon):
                polys.extend([p for p in g.geoms if not p.is_empty])
        return polys
    return []


def adapt_resplan_record(record: Dict[str, Any]) -> MetricFloorPlan:
    """Consume a raw ResPlan record dictionary containing Shapely objects and return MetricFloorPlan.

    Args:
        record: Dictionary with raw Shapely geometry objects and metadata.

    Returns:
        MetricFloorPlan: Fully validated metric floor plan model in metres.
    """
    plan_id = str(record.get("id", record.get("plan_id", "0")))
    raw_area = float(record["area"])
    if raw_area <= 0:
        raise ValueError(f"Plan {plan_id} has invalid non-positive area: {raw_area}")

    raw_inner = record.get("inner")
    if raw_inner is None or raw_inner.is_empty or raw_inner.area <= 0:
        raise ValueError(f"Plan {plan_id} lacks valid 'inner' floor boundary")

    # Heal inner boundary
    if not raw_inner.is_valid:
        raw_inner = shapely.make_valid(raw_inner)

    # Scale calculation: scale = sqrt(inner.area / area)
    scale = math.sqrt(raw_inner.area / raw_area)
    if scale <= 0:
        raise ValueError(f"Calculated scale factor for plan {plan_id} is non-positive: {scale}")

    # Bounding box origin offset
    min_x, min_y, _, _ = raw_inner.bounds
    origin_offset = (min_x, min_y)

    # 1. Scale inner boundary
    scaled_inner = normalize_to_metric(raw_inner, scale, origin_offset)
    healed_inner = parse_and_heal_polygon(scaled_inner, min_area=0.0, is_structural_feature=True)

    # 2. Scale walls, windows, doors, front_doors
    def _transform_components(raw_val: Any) -> List[Polygon]:
        comps = _split_polygon_components(raw_val)
        metric_comps: List[Polygon] = []
        for c in comps:
            scaled = normalize_to_metric(c, scale, origin_offset)
            healed = parse_and_heal_polygon(scaled, min_area=0.0, is_structural_feature=True)
            if not healed.is_empty:
                if isinstance(healed, Polygon):
                    metric_comps.append(healed)
                elif isinstance(healed, MultiPolygon):
                    metric_comps.extend([p for p in healed.geoms if not p.is_empty])
        return metric_comps

    metric_walls = _transform_components(record.get("wall"))
    metric_windows = _transform_components(record.get("window"))
    metric_doors = _transform_components(record.get("door"))
    metric_front_doors = _transform_components(record.get("front_door"))

    # 3. Build obstacles and free space
    obstacles, free_space = build_obstacles_and_freespace(
        healed_inner,
        metric_walls,
        metric_windows,
        metric_doors,
        metric_front_doors,
    )

    # 4. Extract and split room categories into deterministic instances
    eligible_categories = ["bedroom", "living", "kitchen", "storage"]
    excluded_categories = [
        "bathroom",
        "balcony",
        "balacony",
        "stair",
        "garden",
        "pool",
        "parking",
        "land",
        "corridor",
    ]

    all_categories = eligible_categories + excluded_categories

    rooms_dict: Dict[str, EligibleOrExcludedRoom] = {}

    for cat_name in all_categories:
        raw_val = record.get(cat_name)
        if raw_val is None:
            continue

        raw_components = _split_polygon_components(raw_val)
        if not raw_components:
            continue

        metric_room_components: List[Polygon] = []
        for comp in raw_components:
            scaled = normalize_to_metric(comp, scale, origin_offset)
            healed = parse_and_heal_polygon(scaled, min_area=2.0, is_structural_feature=False)
            if not healed.is_empty:
                if isinstance(healed, Polygon) and healed.area >= 2.0:
                    metric_room_components.append(healed)
                elif isinstance(healed, MultiPolygon):
                    for p in healed.geoms:
                        if p.area >= 2.0:
                            metric_room_components.append(p)

        if not metric_room_components:
            continue

        # Sort components deterministically by centroid y, centroid x, minx, miny
        metric_room_components.sort(
            key=lambda p: (
                round(p.centroid.y, 4),
                round(p.centroid.x, 4),
                round(p.bounds[0], 4),
                round(p.bounds[1], 4),
            )
        )

        is_eligible, room_cat = classify_room(cat_name)

        # Standard prefix: e.g. bedroom_01, living_01
        norm_cat_name = "balcony" if cat_name == "balacony" else cat_name
        for idx, p_comp in enumerate(metric_room_components, start=1):
            room_id = f"{norm_cat_name}_{idx:02d}"
            rooms_dict[room_id] = EligibleOrExcludedRoom(
                room_id=room_id,
                category=room_cat.value,
                is_eligible=is_eligible,
                area_m2=round(p_comp.area, 4),
                polygon_wkt=p_comp.wkt,
            )

    # 5. Extract doorway centroids as door waypoints
    doorway_points: List[Point2D] = []
    for d_poly in metric_doors:
        c = d_poly.centroid
        doorway_points.append(Point2D(x=round(c.x, 4), y=round(c.y, 4)))

    # 6. Generate deterministic device locations for eligible rooms
    eligible_rooms_geom = {
        r.room_id: shapely.from_wkt(r.polygon_wkt)
        for r in rooms_dict.values()
        if r.is_eligible
    }

    nodes_dict = generate_all_room_devices(
        rooms=eligible_rooms_geom,
        obstacles=obstacles,
        doorway_points=doorway_points,
    )

    return MetricFloorPlan(
        plan_id=plan_id,
        scale_units_per_m=round(scale, 6),
        total_area_m2=round(raw_area, 4),
        inner_wkt=healed_inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms=rooms_dict,
        nodes=nodes_dict,
    )


def floor_plan_to_preview_geojson(
    plan: MetricFloorPlan,
    raw_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert MetricFloorPlan to browser-safe GeoJSON and metadata dictionary."""
    net_area = float(raw_record.get("net_area", plan.total_area_m2)) if raw_record else plan.total_area_m2

    rooms_list: List[Dict[str, Any]] = []
    for r in plan.rooms.values():
        poly_geom = shapely.from_wkt(r.polygon_wkt)
        rooms_list.append({
            "room_id": r.room_id,
            "category": r.category,
            "is_eligible": r.is_eligible,
            "area_m2": r.area_m2,
            "geometry": shapely.geometry.mapping(poly_geom),
            "polygon": [[round(pt[0], 4), round(pt[1], 4)] for pt in poly_geom.exterior.coords]
            if isinstance(poly_geom, Polygon)
            else [],
        })

    obs_geom = plan.obstacles_geom
    obstacles_coords: List[List[List[float]]] = []
    if obs_geom is not None and not obs_geom.is_empty:
        geoms = obs_geom.geoms if hasattr(obs_geom, "geoms") else [obs_geom]
        for g in geoms:
            if hasattr(g, "exterior") and g.exterior is not None:
                obstacles_coords.append([[round(p[0], 4), round(p[1], 4)] for p in g.exterior.coords])

    nodes_list = [
        {
            "node_id": n.node_id,
            "room_id": n.room_id,
            "x": n.location.x,
            "y": n.location.y,
        }
        for n in plan.nodes.values()
    ]

    eligible_count = sum(1 for r in plan.rooms.values() if r.is_eligible)

    return {
        "status": "ok",
        "plan_id": plan.plan_id,
        "total_area_m2": plan.total_area_m2,
        "net_area_m2": round(net_area, 4),
        "eligible_rooms_count": eligible_count,
        "device_count": len(nodes_list),
        "scale_units_per_m": plan.scale_units_per_m,
        "rooms": rooms_list,
        "obstacles": obstacles_coords,
        "nodes": nodes_list,
        "preview_mode": True,
        "hubs": [],
        "routes": [],
    }
