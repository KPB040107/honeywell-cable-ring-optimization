import json, pickle, shapely, math
from shapely.geometry.base import BaseGeometry
from shapely.geometry import Polygon, MultiPolygon, Point, LineString
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record
from resplan_router.phase2_topology.portal_extractor import (
    _ensure_geometry,
    _extract_linestrings,
    _get_shared_boundary,
    _build_portal,
    _normalize_geom_units,
    PortalType,
    DoorOrPassagePortal,
)

# Test on Slide 5
with open('tests/fixtures/slide5_schematic.json', 'r') as f:
    s5_data = json.load(f)
s5_plan = parse_floor_plan(s5_data)
s5_doors = s5_data.get('doors', [])

# Test function
def new_extract_door_portals(plan, raw_doors, raw_entrance=None):
    if raw_doors is None:
        return []
    if isinstance(raw_doors, BaseGeometry):
        flat_doors = [g for g in raw_doors.geoms if not g.is_empty] if isinstance(raw_doors, (MultiPolygon, shapely.GeometryCollection)) else [raw_doors]
    else:
        flat_doors = []
        for g in raw_doors:
            norm = _ensure_geometry(g)
            if norm is not None and not norm.is_empty:
                if isinstance(norm, (MultiPolygon, shapely.GeometryCollection)):
                    flat_doors.extend([p for p in norm.geoms if not p.is_empty])
                else:
                    flat_doors.append(norm)

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

    portals = []
    seen = set()

    for door in normalized_doors:
        if entrance_geom is not None and not entrance_geom.is_empty:
            if door.intersects(entrance_geom) or door.distance(entrance_geom) < 0.05:
                continue
        if door.distance(inner_boundary) < 0.05 or door.intersects(inner_boundary):
            continue

        buffered_door = door.buffer(0.18)
        cand_idxs = tree.query(buffered_door)

        matching = []
        for idx in cand_idxs:
            r_id, _ = room_items[idx]
            r_poly = room_geoms[idx]
            inter = r_poly.intersection(buffered_door)
            if inter.area > 1e-4:
                matching.append((r_id, r_poly, inter.area))

        if len(matching) < 2:
            continue

        matching.sort(key=lambda m: m[2], reverse=True)
        r1_id, r1_poly = matching[0][0], matching[0][1]
        r2_id, r2_poly = matching[1][0], matching[1][1]

        if r1_id > r2_id:
            r1_id, r2_id = r2_id, r1_id
            r1_poly, r2_poly = r2_poly, r1_poly

        shared = _get_shared_boundary(r1_poly, r2_poly)
        if shared is not None and not shared.is_empty:
            threshold_raw = shared.intersection(door)
            if threshold_raw.is_empty or threshold_raw.length < 1e-4:
                threshold_raw = shared.intersection(buffered_door)
        else:
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
            continue

        threshold_free = threshold_raw.intersection(plan.free_space_geom)
        lines = _extract_linestrings(threshold_free)
        if not lines:
            continue

        thresh_line = max(lines, key=lambda l: l.length)
        if thresh_line.length < 0.05:
            continue

        p0 = thresh_line.interpolate(0.5, normalized=True)
        key = (r1_id, r2_id, (round(p0.x, 2), round(p0.y, 2)))
        if key in seen:
            continue
        seen.add(key)

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

s5_ports = new_extract_door_portals(s5_plan, s5_doors, s5_data.get('front_door'))
print(f"Slide 5 new extraction: {len(s5_ports)} portals")
for p in s5_ports:
    print(f"  {p.portal_id}: {p.room1_id} <-> {p.room2_id}")

# Test on Plan 64
with open('ResPlan.pkl', 'rb') as f:
    records = pickle.load(f)
rec = next(r for r in records if str(r.get('id', r.get('plan_id'))) == '64')
p64 = adapt_resplan_record(rec)
p64_ports = new_extract_door_portals(p64, rec.get('door'), rec.get('front_door'))
print(f"Plan 64 new extraction: {len(p64_ports)} portals")
for p in p64_ports:
    print(f"  {p.portal_id}: {p.room1_id} <-> {p.room2_id}")
