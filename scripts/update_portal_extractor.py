import sys

with open("src/resplan_router/phase2_topology/portal_extractor.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update _flatten_geometries
old_flatten = """def _flatten_geometries(geoms: Optional[Sequence[Any]]) -> List[BaseGeometry]:
    \"\"\"Flatten and validate a sequence of input geometries.\"\"\"
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
    return flat"""

new_flatten = """def _flatten_geometries(geoms: Any) -> List[BaseGeometry]:
    \"\"\"Flatten and validate a sequence or single input geometry.\"\"\"
    if geoms is None:
        return []
    if isinstance(geoms, BaseGeometry):
        if geoms.is_empty:
            return []
        if isinstance(geoms, (MultiPolygon, shapely.GeometryCollection)):
            return [g for g in geoms.geoms if not g.is_empty]
        return [geoms]
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
    return flat"""

# 2. Update extract_door_portals matching and threshold logic
old_matching = """        # 2. Query STRtree with buffered door (0.05m expansion)
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
            threshold_raw = shared.intersection(buffered_door)"""

new_matching = """        # 2. Query STRtree with buffered door (0.18m expansion to bridge wall thickness)
        buffered_door = door.buffer(0.18)
        candidate_idxs = tree.query(buffered_door)

        matching_rooms: List[Tuple[str, BaseGeometry, float]] = []
        for idx in candidate_idxs:
            r_id, r_obj = room_items[idx]
            r_poly = room_geoms[idx]
            inter = r_poly.intersection(buffered_door)
            if inter.area > 1e-4:
                matching_rooms.append((r_id, r_poly, inter.area))

        if len(matching_rooms) < 2:
            continue

        # Select top two rooms by overlap area with the buffered door
        matching_rooms.sort(key=lambda m: m[2], reverse=True)
        r1_id, r1_poly = matching_rooms[0][0], matching_rooms[0][1]
        r2_id, r2_poly = matching_rooms[1][0], matching_rooms[1][1]

        if r1_id > r2_id:
            r1_id, r2_id = r2_id, r1_id
            r1_poly, r2_poly = r2_poly, r1_poly

        # 3. Compute shared boundary or doorway threshold bridging the opening
        shared = _get_shared_boundary(r1_poly, r2_poly)
        if shared is not None and not shared.is_empty:
            threshold_raw = shared.intersection(door)
            if threshold_raw.is_empty or threshold_raw.length < 1e-4:
                threshold_raw = shared.intersection(buffered_door)
        else:
            # Fallback for rooms separated by partition wall thickness:
            c = door.centroid
            p1, _ = shapely.ops.nearest_points(r1_poly.boundary, c)
            p2, _ = shapely.ops.nearest_points(r2_poly.boundary, c)
            dx = p2.x - p1.x
            dy = p2.y - p1.y
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                nx = dx / dist
                ny = dy / dist
                tx = -ny
                ty = nx
                line_span = LineString([(c.x - 2.0 * tx, c.y - 2.0 * ty), (c.x + 2.0 * tx, c.y + 2.0 * ty)])
                threshold_raw = door.intersection(line_span)
            else:
                threshold_raw = None

        if threshold_raw is None or threshold_raw.is_empty:
            continue"""

norm_code = code.replace("\r\n", "\n")
norm_old_flatten = old_flatten.replace("\r\n", "\n")
norm_new_flatten = new_flatten.replace("\r\n", "\n")
norm_old_matching = old_matching.replace("\r\n", "\n")
norm_new_matching = new_matching.replace("\r\n", "\n")

if norm_old_flatten in norm_code and norm_old_matching in norm_code:
    norm_code = norm_code.replace(norm_old_flatten, norm_new_flatten)
    norm_code = norm_code.replace(norm_old_matching, norm_new_matching)
    with open("src/resplan_router/phase2_topology/portal_extractor.py", "w", encoding="utf-8") as f:
        f.write(norm_code)
    print("Updated portal_extractor.py successfully!")
else:
    print("Match failed:", norm_old_flatten in norm_code, norm_old_matching in norm_code)
    sys.exit(1)
