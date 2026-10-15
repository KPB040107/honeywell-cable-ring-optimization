import pytest
from shapely.geometry import Point, Polygon
from shapely.wkt import loads as wkt_loads

from resplan_router.phase1_ingestion.parser import (
    AmbiguousCoordinateUnitError,
    GeometryRepairError,
    ResPlanSchemaValidationError,
    decompose_multipolygon,
    parse_resplan_json,
    repair_geometry_if_needed,
)
from resplan_router.phase1_ingestion.scale_normalizer import compute_metric_scale_factor
from resplan_router.phase1_ingestion.node_placer import (
    InfeasibleNodePlacementError,
    find_safe_node_point,
    place_nodes,
)
from resplan_router.contracts import FloorPlanModel, RoomEntity


def test_provisional_fixture_ingestion():
    """Verify parsing of provisional 6-room benchmark fixture."""
    plan = parse_resplan_json("tests/fixtures/schematic_6room.json")
    assert plan.plan_id == "schematic_6room"
    # Exactly 7 rooms: 6 eligible + 1 bathroom
    assert len(plan.rooms) == 7
    eligible_rooms = [r for r in plan.rooms if r.is_eligible]
    excluded_rooms = [r for r in plan.rooms if not r.is_eligible]
    assert len(eligible_rooms) == 6
    assert len(excluded_rooms) == 1
    assert excluded_rooms[0].category == "bathroom"

    # Place nodes
    nodes = place_nodes(plan.rooms, plan.obstacle_union_wkt)
    assert len(nodes) == 6
    for node in nodes:
        # Check node is inside its host room
        host_room = next(r for r in plan.rooms if r.room_id == node.host_room_id)
        host_poly = wkt_loads(host_room.polygon_wkt)
        pt = Point(node.coord.x, node.coord.y)
        assert host_poly.contains(pt), f"Node {node.node_id} outside host room"
        # Check clearance >= 0.20m
        assert pt.distance(host_poly.boundary) >= 0.20 - 1e-4, f"Clearance violation for {node.node_id}"


def test_schema_validation_and_unit_rejection():
    """Verify explicit unit handling and schema validation."""
    # 1. Missing 'id'
    with pytest.raises(ResPlanSchemaValidationError, match="Missing required top-level field: 'id'"):
        parse_resplan_json({"coordinate_unit": "meter", "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"})

    # 2. Missing coordinate_unit
    with pytest.raises(AmbiguousCoordinateUnitError, match="missing or ambiguous coordinate_unit"):
        parse_resplan_json({"id": "plan_1", "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"})

    # 3. Invalid coordinate_unit
    with pytest.raises(AmbiguousCoordinateUnitError, match="Must be explicitly 'meter' or 'pixel'"):
        parse_resplan_json({"id": "plan_1", "coordinate_unit": "inch", "living": "POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))"})

    # 4. Valid resplan_geo_v1 structured coordinate dictionary
    geo_dict = {
        "id": "plan_geo",
        "coordinate_unit": "meter",
        "living": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [5.0, 0.0], [5.0, 4.0], [0.0, 4.0], [0.0, 0.0]]]
        }
    }
    plan = parse_resplan_json(geo_dict)
    assert len(plan.rooms) == 1
    assert plan.rooms[0].category == "living"
    assert plan.rooms[0].area_m2 == 20.0


def test_targeted_repair_logging_and_drift_rejection():
    """Verify targeted geometry repair leaves valid untouched and catches excessive drift."""
    # Valid geometry is untouched
    valid_poly = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    repaired_valid = repair_geometry_if_needed(valid_poly, "valid_room")
    assert repaired_valid.equals(valid_poly)

    # Self-touching spike polygon: valid repair with 0% area change
    spike_poly = Polygon([(0, 0), (2, 0), (2, 0.1), (2, 0), (4, 0), (4, 4), (0, 4)])
    assert not spike_poly.is_valid
    repaired_spike = repair_geometry_if_needed(spike_poly, "spike_room")
    assert repaired_spike.is_valid
    assert abs(repaired_spike.area - spike_poly.area) < 1e-4

    # Catastrophic self-intersecting bow-tie where repair creates an unacceptably large relative change
    bow_tie = Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])
    assert not bow_tie.is_valid
    with pytest.raises(GeometryRepairError, match="caused excessive area change"):
        repair_geometry_if_needed(bow_tie, "bowtie_room")


def test_scale_normalization_explicit():
    """Verify metric scale factor computation based on explicit metadata."""
    # Meter unit -> scale = 1.0
    scale, msg = compute_metric_scale_factor({"coordinate_unit": "meter"}, 100.0)
    assert scale == 1.0

    # Pixel unit with valid GFA metadata: 100 m2 metadata / 400 pixel raw area -> scale = 0.5
    scale, msg = compute_metric_scale_factor({"coordinate_unit": "pixel", "area": 100.0}, 400.0)
    assert scale == 0.5

    # Pixel unit missing GFA area -> error
    with pytest.raises(AmbiguousCoordinateUnitError, match="valid 'area'"):
        compute_metric_scale_factor({"coordinate_unit": "pixel"}, 400.0)


def test_l_shaped_nonconvex_room_node_containment():
    """Verify polylabel places node strictly inside L-shaped room with clearance >= 0.20m."""
    l_poly = Polygon([(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)])
    room = RoomEntity(
        room_id="l_shaped_0",
        category="living",
        polygon_wkt=l_poly.wkt,
        area_m2=l_poly.area,
        is_eligible=True,
        is_transit=False
    )
    nodes = place_nodes([room], obstacle_mask_wkt="POLYGON EMPTY")
    assert len(nodes) == 1
    node_pt = Point(nodes[0].coord.x, nodes[0].coord.y)
    assert l_poly.contains(node_pt)
    assert node_pt.distance(l_poly.boundary) >= 0.20 - 1e-4


def test_post_snap_clearance_and_recovery():
    """Verify grid snapping maintains >= 0.20m clearance or triggers neighborhood recovery."""
    room_poly = Polygon([(0, 0), (2.0, 0), (2.0, 2.0), (0, 2.0)])
    room = RoomEntity(
        room_id="box_0",
        category="bedroom",
        polygon_wkt=room_poly.wkt,
        area_m2=4.0,
        is_eligible=True,
        is_transit=False
    )
    nodes = place_nodes([room], obstacle_mask_wkt="POLYGON EMPTY")
    assert len(nodes) == 1
    pt = Point(nodes[0].coord.x, nodes[0].coord.y)
    assert room_poly.contains(pt)
    assert pt.distance(room_poly.boundary) >= 0.20


def test_infeasible_narrow_room_rejection():
    """Verify that a 0.30m-wide corridor is rejected with InfeasibleNodePlacementError."""
    narrow_poly = Polygon([(0, 0), (0.30, 0), (0.30, 5.0), (0, 5.0)])
    room = RoomEntity(
        room_id="narrow_0",
        category="storage",
        polygon_wkt=narrow_poly.wkt,
        area_m2=1.5,
        is_eligible=True,
        is_transit=False
    )
    with pytest.raises(InfeasibleNodePlacementError, match="cannot accommodate a device node with required 0.2"):
        place_nodes([room], obstacle_mask_wkt="POLYGON EMPTY")


def test_serialization_roundtrip_with_placed_nodes():
    """Verify full FloorPlanModel serialization and deserialization with placed nodes."""
    plan = parse_resplan_json("tests/fixtures/schematic_6room.json")
    nodes = place_nodes(plan.rooms, plan.obstacle_union_wkt)
    plan_with_nodes = FloorPlanModel(
        plan_id=plan.plan_id,
        scale_factor=plan.scale_factor,
        inner_polygon_wkt=plan.inner_polygon_wkt,
        obstacle_union_wkt=plan.obstacle_union_wkt,
        rooms=plan.rooms,
        doors=plan.doors,
        nodes=nodes
    )
    dumped = plan_with_nodes.model_dump_json()
    restored = FloorPlanModel.model_validate_json(dumped)
    assert restored.plan_id == plan.plan_id
    assert len(restored.nodes) == 6
    assert restored.nodes[0].coord.x == nodes[0].coord.x
