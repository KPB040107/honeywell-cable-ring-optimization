"""Scale normalization and coordinate transformation for ResPlan floor plans.

Converts raw coordinates in drawing units to metric coordinates (metres)
using Slide 4 exact formula: scale = sqrt(inner.area / area).
Ignores net_area completely.
"""

import math
from typing import Any, Optional, Tuple, Union
import shapely
import shapely.affinity
import shapely.geometry
from shapely.geometry.base import BaseGeometry


def parse_geometry(raw_geom: Any) -> BaseGeometry:
    """Parse geometry from various possible input formats into a valid Shapely geometry.

    Supports:
    - Shapely BaseGeometry instances
    - WKT strings
    - GeoJSON-like dicts ({'type': ..., 'coordinates': ...})
    - Dicts with 'coordinates' or 'exterior'
    - Coordinate lists/tuples [[x, y], ...] or [[[x, y], ...]]
    """
    if isinstance(raw_geom, BaseGeometry):
        geom = raw_geom
    elif isinstance(raw_geom, str):
        geom = shapely.from_wkt(raw_geom)
    elif isinstance(raw_geom, dict):
        if "type" in raw_geom and "coordinates" in raw_geom:
            geom = shapely.geometry.shape(raw_geom)
        elif "coordinates" in raw_geom:
            coords = raw_geom["coordinates"]
            if coords and isinstance(coords[0], (list, tuple)) and isinstance(coords[0][0], (list, tuple)):
                geom = shapely.Polygon(coords[0], coords[1:])
            else:
                geom = shapely.Polygon(coords)
        elif "exterior" in raw_geom:
            geom = shapely.Polygon(raw_geom["exterior"], raw_geom.get("interiors", []))
        else:
            raise ValueError(f"Unrecognized geometry dictionary format: {list(raw_geom.keys())}")
    elif isinstance(raw_geom, (list, tuple)):
        if len(raw_geom) == 0:
            raise ValueError("Empty coordinate list cannot form a geometry")
        if isinstance(raw_geom[0], (list, tuple)) and len(raw_geom[0]) > 0 and isinstance(raw_geom[0][0], (list, tuple)):
            geom = shapely.Polygon(raw_geom[0], raw_geom[1:])
        else:
            geom = shapely.Polygon(raw_geom)
    else:
        raise TypeError(f"Unsupported geometry type: {type(raw_geom)}")

    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    return geom


def compute_scale_factor(plan_dict: dict[str, Any]) -> float:
    """Compute the metric scale factor in drawing units per metre.

    Following Slide 4:
        scale = sqrt(inner.area / area)

    Where inner.area is in drawing units squared and plan_dict['area'] is in m^2.
    Strictly ignores 'net_area'.

    Args:
        plan_dict: Dictionary representing the floor plan. Must contain 'area' and 'inner'.

    Returns:
        float: Scale factor in units/m.

    Raises:
        ValueError: If 'area' or 'inner' is missing, invalid, or non-positive.
    """
    if "area" not in plan_dict:
        raise ValueError("Input plan_dict must contain 'area' key")

    try:
        area_val = float(plan_dict["area"])
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid 'area' value in plan_dict: {plan_dict.get('area')}") from exc

    if area_val <= 0.0:
        raise ValueError(f"Floor plan 'area' must be strictly positive (> 0), got {area_val}")

    if "inner" not in plan_dict:
        raise ValueError("Input plan_dict must contain 'inner' key")

    inner_geom = parse_geometry(plan_dict["inner"])
    if inner_geom.is_empty or inner_geom.area <= 0.0:
        raise ValueError(f"Invalid 'inner' polygon: area is non-positive ({inner_geom.area})")

    # Slide 4 formula: scale = sqrt(inner.area / area)
    # Strictly ignores plan_dict.get("net_area")
    scale = math.sqrt(inner_geom.area / area_val)
    return scale


def normalize_to_metric(
    geom: BaseGeometry,
    scale: float,
    origin_offset: Tuple[float, float] = (0.0, 0.0),
) -> BaseGeometry:
    """Scale geometry to metres and translate relative to the origin offset.

    Scales coordinates by 1.0 / scale and translates by -origin_offset so that
    the floor plan bounding box lower-left aligns to (0, 0) with y pointing upwards.

    Args:
        geom: Shapely geometry in drawing units.
        scale: Metric scale factor (drawing units per metre). Must be > 0.
        origin_offset: (x_min, y_min) offset of the floor plan bounding box to align to (0, 0).
                       Defaults to (0.0, 0.0).

    Returns:
        BaseGeometry: Transformed geometry in metres.
    """
    if scale <= 0.0:
        raise ValueError(f"Scale factor must be strictly positive, got {scale}")

    if geom.is_empty:
        return geom

    x_off, y_off = origin_offset
    # Translate so lower-left shifts by -origin_offset
    translated = shapely.affinity.translate(geom, xoff=-x_off, yoff=-y_off)

    # Scale coordinates by 1.0 / scale around origin (0.0, 0.0)
    scaled = shapely.affinity.scale(
        translated,
        xfact=1.0 / scale,
        yfact=1.0 / scale,
        origin=(0.0, 0.0),
    )
    return scaled
