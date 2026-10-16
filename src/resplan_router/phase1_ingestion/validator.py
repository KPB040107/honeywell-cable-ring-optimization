"""Floor plan upload structural validation module.

Validates uploaded floor plan JSON against router specifications:
- JSON syntax and root dictionary structure.
- Required top-level attributes ('area', 'inner', and room definitions).
- Room polygon geometry and category classification.
- Duplicate node ID detection and multiple nodes per room prevention.
- Unsupported data, malformed coordinates, and negative/zero areas.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import shapely

from resplan_router.phase1_ingestion.node_loader import _extract_raw_node_items
from resplan_router.phase1_ingestion.parser import classify_room
from resplan_router.phase1_ingestion.scale_converter import parse_geometry


def validate_floor_plan_json(
    raw_input: Union[str, bytes, Dict[str, Any]],
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Validate uploaded floor plan JSON structure and return diagnostic errors.

    Args:
        raw_input: Raw JSON string, bytes, or parsed dictionary.

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str], parsed_dict: Optional[dict]).
    """
    # 1. Parse JSON if string or bytes
    data: Dict[str, Any]
    if isinstance(raw_input, (bytes, bytearray)):
        try:
            text = raw_input.decode("utf-8")
        except UnicodeDecodeError as exc:
            return False, f"Invalid file encoding: UTF-8 decode failed ({exc})", None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return (
                False,
                f"Invalid JSON syntax: {exc.msg} (line {exc.lineno}, column {exc.colno})",
                None,
            )
        data = parsed
    elif isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return (
                False,
                f"Invalid JSON syntax: {exc.msg} (line {exc.lineno}, column {exc.colno})",
                None,
            )
        data = parsed
    elif isinstance(raw_input, dict):
        data = raw_input
    else:
        return (
            False,
            f"Invalid JSON root: Expected a JSON object/dictionary, got {type(raw_input).__name__}",
            None,
        )

    if not isinstance(data, dict):
        return (
            False,
            f"Invalid JSON root: Expected a JSON object/dictionary, got {type(data).__name__}",
            None,
        )

    # 2. Validate 'area'
    if "area" not in data:
        return (
            False,
            "Missing required field: 'area' (total floor plan area in square metres)",
            None,
        )

    try:
        area_val = float(data["area"])
    except (ValueError, TypeError):
        return (
            False,
            f"Invalid 'area' value: Must be a numeric value, got '{data.get('area')}'",
            None,
        )

    if area_val <= 0.0:
        return (
            False,
            f"Invalid 'area' value: Must be strictly positive (> 0), got {area_val}",
            None,
        )

    # 3. Validate 'inner' boundary geometry
    if "inner" not in data:
        return (
            False,
            "Missing required geometry: 'inner' boundary polygon coordinates",
            None,
        )

    try:
        inner_geom = parse_geometry(data["inner"])
        if inner_geom.is_empty or inner_geom.area <= 0.0:
            return (
                False,
                "Invalid 'inner' boundary geometry: Must be a non-empty polygon with positive area",
                None,
            )
    except Exception as exc:
        return (
            False,
            f"Invalid 'inner' boundary geometry: Could not parse polygon coordinates ({exc})",
            None,
        )

    # 4. Validate Rooms
    has_rooms_list = "rooms" in data and isinstance(data["rooms"], list)
    eligible_categories = {"living", "bedroom", "kitchen", "storage"}
    eligible_count = 0
    seen_room_ids: Set[str] = set()

    if has_rooms_list:
        if len(data["rooms"]) == 0:
            return (
                False,
                "Missing rooms: 'rooms' list cannot be empty",
                None,
            )

        for idx, room_entry in enumerate(data["rooms"]):
            if not isinstance(room_entry, dict):
                return (
                    False,
                    f"Invalid room entry at index {idx}: Room must be a JSON object",
                    None,
                )

            r_id = str(room_entry.get("room_id", room_entry.get("id", f"R{idx + 1:02d}")))
            if r_id in seen_room_ids:
                return (
                    False,
                    f"Duplicate room ID '{r_id}' found in floor plan 'rooms' list",
                    None,
                )
            seen_room_ids.add(r_id)

            cat_str = room_entry.get("category", room_entry.get("name", ""))
            if not cat_str:
                return (
                    False,
                    f"Room '{r_id}' is missing a required 'category' or 'name' string",
                    None,
                )

            try:
                is_elig, _ = classify_room(str(cat_str))
                if is_elig:
                    eligible_count += 1
            except ValueError as exc:
                return (
                    False,
                    f"Unsupported room category in room '{r_id}': {exc}",
                    None,
                )

            # Check room geometry
            geom_raw = room_entry.get(
                "polygon",
                room_entry.get("geometry", room_entry.get("coordinates")),
            )
            if geom_raw is None:
                return (
                    False,
                    f"Room '{r_id}' is missing polygon coordinates ('polygon')",
                    None,
                )

            try:
                r_geom = parse_geometry(geom_raw)
                if r_geom.is_empty:
                    return (
                        False,
                        f"Room '{r_id}' polygon geometry is empty or degenerate",
                        None,
                    )
            except Exception as exc:
                return (
                    False,
                    f"Invalid polygon coordinates for room '{r_id}': {exc}",
                    None,
                )

        if eligible_count == 0:
            return (
                False,
                "No eligible rooms found: Floor plan must contain at least one eligible room "
                "(living, bedroom, kitchen, or storage)",
                None,
            )

    else:
        # Check category-based dictionary format (e.g. "living": [...], "bedroom": [...])
        found_any_category = False
        all_known_cats = [
            "living", "bedroom", "kitchen", "storage",
            "bathroom", "balcony", "stair", "corridor",
        ]
        for c_key in all_known_cats:
            if c_key in data and data[c_key]:
                found_any_category = True
                try:
                    is_elig, _ = classify_room(c_key)
                    if is_elig:
                        eligible_count += 1
                except Exception:
                    pass

        if not found_any_category:
            return (
                False,
                "Missing rooms: Floor plan must define room polygons via 'rooms' array or category keys",
                None,
            )

        if eligible_count == 0:
            return (
                False,
                "No eligible rooms found: Floor plan must contain at least one eligible room "
                "(living, bedroom, kitchen, or storage)",
                None,
            )

    # 5. Validate Fixed Nodes (if provided)
    raw_node_items = _extract_raw_node_items(data)
    if raw_node_items:
        seen_node_ids: Set[str] = set()
        room_node_map: Dict[str, str] = {}

        for idx, n_item in enumerate(raw_node_items):
            if not isinstance(n_item, dict):
                return (
                    False,
                    f"Invalid node format at index {idx}: Node entry must be an object",
                    None,
                )

            n_id = str(n_item.get("node_id", n_item.get("id", f"N{idx + 1}")))
            if n_id in seen_node_ids:
                return (
                    False,
                    f"Duplicate fixed node ID '{n_id}' in input plan",
                    None,
                )
            seen_node_ids.add(n_id)

            r_id = n_item.get("room_id")
            if r_id:
                r_id_str = str(r_id)
                if r_id_str in room_node_map:
                    return (
                        False,
                        f"Room '{r_id_str}' has more than one fixed node in input plan "
                        f"('{room_node_map[r_id_str]}' and '{n_id}')",
                        None,
                    )
                room_node_map[r_id_str] = n_id

            # Validate coordinate formats
            coords_valid = False
            for coord_key in ("location", "coords"):
                if coord_key in n_item and isinstance(n_item[coord_key], (list, tuple)):
                    pts = n_item[coord_key]
                    if len(pts) >= 2 and all(isinstance(v, (int, float)) for v in pts[:2]):
                        coords_valid = True
                        break
            if not coords_valid and "x" in n_item and "y" in n_item:
                if isinstance(n_item["x"], (int, float)) and isinstance(n_item["y"], (int, float)):
                    coords_valid = True

            if not coords_valid:
                return (
                    False,
                    f"Invalid or missing coordinates for node '{n_id}' (expected [x, y] numbers)",
                    None,
                )

    return True, None, data
