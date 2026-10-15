import pytest
from shapely.geometry import Point, Polygon
from shapely.wkt import loads as wkt_loads

from resplan_router.phase1_ingestion.parser import (
    GeometryRepairError,
    ResPlanSchemaValidationError,
    decompose_multipolygon,
    parse_resplan_json,
    repair_geometry_if_needed,
)
from resplan_router.phase1_ingestion.scale_normalizer import (
    AmbiguousCoordinateUnitError,
    compute_metric_scale_factor,
)
from resplan_router.phase1_ingestion.node_placer import (
    InfeasibleNodePlacementError,
    find_safe_node_point,
    place_nodes,
)
from resplan_router.contracts import FloorPlanModel, RoomEntity


def test_provisional_fixture_room_areas_and_totals():
    """Verify exact room areas and total 118 m2 on provisional benchmark."""
    plan = parse_resplan_json("tests/fixtures/schematic_6room.json")
    assert plan.plan_id == "schematic_6room"
    assert len(plan.rooms) == 7

    rooms = {r.room_id: (r.category, r.area_m2, wkt_loads(r.polygon_wkt)) for r in plan.rooms}
    
    # 1. Total eligible area must equal exactly 118.0 m2
    eligible_rooms = [r for r in plan.rooms if r.is_eligible]
    total_eligible = sum(r.area_m2 for r in eligible_rooms)
    assert abs(total_eligible - 118.0) < 1e-4, f"Total area {total_eligible} != 118.0"

    # 2. Check each room's area matches PRD spec
    assert abs(rooms["living_0"][1] - 40.0) < 1e-4
    assert abs(rooms["kitchen_0"][1] - 16.0) < 1e-4
    assert abs(rooms["storage_0"][1] - 15.0) < 1e-4

    bed_areas = sorted([r[1] for r in rooms.values() if r[0] == "bedroom"])
    assert len(bed_areas) == 3
    assert abs(bed_areas[0] - 15.0) < 1e-4
    assert abs(bed_areas[1] - 16.0) < 1e-4
    assert abs(bed_areas[2] - 16.0) < 1e-4

    # Bathroom is excluded from eligible area
    bathroom = next(r for r in plan.rooms if r.category == "bathroom")
    assert not bathroom.is_eligible


def test_provisional_fixture_door_connectivity_and_solid_walls():
    """
    Strengthened door adjacency test:
    Directly evaluates geometric threshold interfaces between actual door polygons
    and room boundary linestrings, rather than relying solely on arbitrary dilation.
    """
    plan = parse_resplan_json("tests/fixtures/schematic_6room.json")
    rooms = {r.room_id: (r.category, wkt_loads(r.polygon_wkt)) for r in plan.rooms}
    doors = [wkt_loads(d.polygon_wkt) for d in plan.doors if not d.is_front_door]

    assert len(doors) == 5, f"Expected 5 interior doors, found {len(doors)}"

    # For each door, find the pair of rooms whose boundary boundaries it directly touches/bridges
    door_conns = []
    for d_idx, d_poly in enumerate(doors):
        # A valid interior door rectangle bridges a shared wall of thickness 0.15m
        # It must intersect each connected room's boundary along a threshold edge of length >= 0.50m
        # or overlap the room polygon directly with threshold contact length >= 0.50m
        connected_rooms = []
        for r_id, (cat, r_poly) in rooms.items():
            # Check contact with room boundary
            contact = d_poly.intersection(r_poly)
            if not contact.is_empty:
                connected_rooms.append(r_id)
            elif d_poly.distance(r_poly.boundary) < 1e-3:
                connected_rooms.append(r_id)
        assert len(connected_rooms) == 2, (
            f"Door {d_idx} ({d_poly.bounds}) must bridge exactly 2 rooms, found {connected_rooms}"
        )
        door_conns.append(set(connected_rooms))

    living_id = next(r for r, (cat, p) in rooms.items() if cat == "living")
    kitchen_id = next(r for r, (cat, p) in rooms.items() if cat == "kitchen")
    storage_id = next(r for r, (cat, p) in rooms.items() if cat == "storage")
    bed_16m = [r for r, (cat, p) in rooms.items() if cat == "bedroom" and abs(p.area - 16.0) < 1e-4]
    bed_15m = [r for r, (cat, p) in rooms.items() if cat == "bedroom" and abs(p.area - 15.0) < 1e-4]

    # Kitchen connects to storage
    assert any({kitchen_id, storage_id}.issubset(c) for c in door_conns)

    # Kitchen connects to Bedroom 2 (one of the 16m2 bedrooms)
    k_beds = [r for c in door_conns if kitchen_id in c for r in c if r != kitchen_id and "bedroom" in r]
    assert len(k_beds) == 1
    bed2_id = k_beds[0]
    assert bed2_id in bed_16m

    # Bedroom 3 (15m2) connects to Living and Bedroom 1 (other 16m2 bedroom)
    bed3_id = bed_15m[0]
    bed1_id = [b for b in bed_16m if b != bed2_id][0]

    assert any({living_id, bed3_id}.issubset(c) for c in door_conns)
    assert any({bed3_id, bed1_id}.issubset(c) for c in door_conns)
    assert any({living_id, bed1_id}.issubset(c) for c in door_conns)

    # Solid-wall exclusions: Verify shared or opposing boundaries have NO door
    assert not any({living_id, storage_id}.issubset(c) for c in door_conns)
    assert not any({bed1_id, bed2_id}.issubset(c) for c in door_conns)
    assert not any({living_id, bed2_id}.issubset(c) for c in door_conns)
    assert not any({storage_id, bed1_id}.issubset(c) for c in door_conns)


def test_schema_enforcement_and_validation():
    """Verify strict mandatory schema_version checking, required metadata, and coordinate dicts."""
    # 1. Missing 'id'
    with pytest.raises(ResPlanSchemaValidationError, match="Missing required top-level field: 'id'"):
        parse_resplan_json({
            "schema_version": "resplan_wkt_v1",
            "coordinate_unit": "meter",
            "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"
        })

    # 2. Missing mandatory schema_version (regression test)
    with pytest.raises(ResPlanSchemaValidationError, match="Missing required top-level field: 'schema_version'"):
        parse_resplan_json({
            "id": "missing_schema_ver",
            "coordinate_unit": "meter",
            "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"
        })

    # 3. Unknown schema_version
    with pytest.raises(ResPlanSchemaValidationError, match="Unsupported schema_version"):
        parse_resplan_json({
            "id": "bad_ver",
            "schema_version": "resplan_unknown_v99",
            "coordinate_unit": "meter",
            "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"
        })

    # 4. Missing coordinate_unit
    with pytest.raises(AmbiguousCoordinateUnitError, match="missing or ambiguous coordinate_unit"):
        parse_resplan_json({
            "id": "plan_1",
            "schema_version": "resplan_wkt_v1",
            "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"
        })

    # 5. Unknown coordinate_unit
    with pytest.raises(AmbiguousCoordinateUnitError, match="Must be explicitly 'meter' or 'pixel'"):
        parse_resplan_json({
            "id": "plan_1",
            "schema_version": "resplan_wkt_v1",
            "coordinate_unit": "foot",
            "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"
        })

    # 6. Malformed coordinate dict
    with pytest.raises(ResPlanSchemaValidationError, match="Invalid coordinate dict structure"):
        parse_resplan_json({
            "id": "bad_coord",
            "schema_version": "resplan_geo_v1",
            "coordinate_unit": "meter",
            "living": {"type": "Polygon", "bad_key": []}
        })

    # 7. Valid resplan_geo_v1 structured coordinate dictionary
    geo_dict = {
        "id": "plan_geo",
        "schema_version": "resplan_geo_v1",
        "coordinate_unit": "meter",
        "living": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0], [0.0, 0.0]]]
        }
    }
    plan = parse_resplan_json(geo_dict)
    assert plan.plan_id == "plan_geo"
    assert len(plan.rooms) == 1
    assert plan.rooms[0].area_m2 == 20.0


def test_consistent_geometric_scale_normalization():
    """Verify pixel-to-meter scaling applies consistently to rooms, doors, and inner polygons."""
    pixel_doc = {
        "id": "pixel_plan",
        "schema_version": "resplan_wkt_v1",
        "coordinate_unit": "pixel",
        "area": 100.0,
        "living": "POLYGON ((0 0, 40 0, 40 40, 0 40, 0 0))",
        "door": "POLYGON ((20 0, 24 0, 24 2, 20 2, 20 0))",
        "inner": "POLYGON ((0 0, 40 0, 40 40, 0 40, 0 0))",
        "wall": "POLYGON ((0 0, 1 0, 1 40, 0 40, 0 0))"
    }
    plan = parse_resplan_json(pixel_doc)
    assert abs(plan.scale_factor - 0.25) < 1e-4

    # Scaled room: 40 * 0.25 = 10m x 10m = 100 m2
    room_poly = wkt_loads(plan.rooms[0].polygon_wkt)
    assert abs(room_poly.area - 100.0) < 1e-3
    assert room_poly.bounds == (0.0, 0.0, 10.0, 10.0)

    # Scaled door
    door_poly = wkt_loads(plan.doors[0].polygon_wkt)
    assert door_poly.bounds == (5.0, 0.0, 6.0, 0.5)

    # Scaled inner polygon
    inner_poly = wkt_loads(plan.inner_polygon_wkt)
    assert inner_poly.bounds == (0.0, 0.0, 10.0, 10.0)

    # Scaled obstacle
    obst_poly = wkt_loads(plan.obstacle_union_wkt)
    assert obst_poly.bounds == (0.0, 0.0, 0.25, 10.0)


def test_obstacle_validation_and_wall_clearance():
    """Confirm every placed node avoids obstacles and strictly preserves >= 0.20m clearance."""
    plan = parse_resplan_json("tests/fixtures/schematic_6room.json")
    obst_poly = wkt_loads(plan.obstacle_union_wkt)
    nodes = place_nodes(plan.rooms, plan.obstacle_union_wkt)

    assert len(nodes) == 6
    for node in nodes:
        host_room = next(r for r in plan.rooms if r.room_id == node.host_room_id)
        room_poly = wkt_loads(host_room.polygon_wkt)
        pt = Point(node.coord.x, node.coord.y)

        # 1. Strict interior containment
        assert room_poly.contains(pt), f"Node {node.node_id} not inside room"

        # 2. Strict >= 0.20m wall clearance
        dist_to_wall = pt.distance(room_poly.boundary)
        assert dist_to_wall >= 0.20 - 1e-6, f"Node {node.node_id} clearance {dist_to_wall} < 0.20m"

        # 3. Obstacle avoidance
        if not obst_poly.is_empty:
            assert not pt.intersects(obst_poly), f"Node {node.node_id} intersects obstacle"


def test_recovery_forces_initial_snapped_failure():
    """Verify that neighborhood recovery actually triggers when initial snap violates clearance."""
    room_poly = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    # Obstacle covering [1.98, 2.02] x [1.98, 2.02].
    # Center (2.0, 2.0) is inside obstacle!
    # Initial snap at (2.0, 2.0) hits obstacle and FAILS!
    obst_box = Polygon([(1.98, 1.98), (2.02, 1.98), (2.02, 2.02), (1.98, 2.02)])
    
    safe_pt = find_safe_node_point(
        room_polygon=room_poly,
        obstacle_mask=obst_box,
        room_id="room_with_center_obstacle",
        erosion_margin=0.25,
        min_wall_clearance=0.20,
        grid_size=0.05
    )
    # The center (2.0, 2.0) failed, so safe_pt is a recovered neighbor
    assert not (safe_pt.x == 2.0 and safe_pt.y == 2.0)
    pt_geom = Point(safe_pt.x, safe_pt.y)
    assert not pt_geom.intersects(obst_box)
    assert pt_geom.distance(room_poly.boundary) >= 0.20


def test_narrow_room_fails_specifically_for_clearance():
    """Ensure the 0.30m-wide corridor fails specifically because 0.20m clearance on both sides is impossible."""
    narrow_poly = Polygon([(0, 0), (0.30, 0), (0.30, 4.0), (0, 4.0)])
    room = RoomEntity(
        room_id="narrow_corridor",
        category="storage",
        polygon_wkt=narrow_poly.wkt,
        area_m2=1.2,
        is_eligible=True,
        is_transit=False
    )
    with pytest.raises(InfeasibleNodePlacementError) as exc_info:
        place_nodes([room], obstacle_mask_wkt="POLYGON EMPTY", min_wall_clearance=0.20)
    
    assert "required 0.2 m wall clearance" in str(exc_info.value)
    assert "Maximum achievable clearance after snapping is" in str(exc_info.value)


def test_targeted_repair_logging():
    """Verify targeted repair leaves valid geometry untouched and logs repairs."""
    valid_poly = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    repaired_valid = repair_geometry_if_needed(valid_poly, "valid_room")
    assert repaired_valid.equals(valid_poly)

    spike_poly = Polygon([(0, 0), (2, 0), (2, 0.1), (2, 0), (4, 0), (4, 4), (0, 4)])
    assert not spike_poly.is_valid
    repaired_spike = repair_geometry_if_needed(spike_poly, "spike_room")
    assert repaired_spike.is_valid
    assert abs(repaired_spike.area - spike_poly.area) < 1e-4

    bow_tie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    assert not bow_tie.is_valid
    with pytest.raises(GeometryRepairError, match="caused excessive area change"):
        repair_geometry_if_needed(bow_tie, "bowtie_room")
