"""Comprehensive unit and integration test suite for Phase 1 Ingestion.

Implements the official verification gates:
- Test 1.1 (test_slide4_exact_scale_conversion): Scale factor ~18.09068, metric dimensions 11.055m x 9.950m.
- Test 1.2 (test_net_area_ignored): net_area = 0 strictly ignored, plan area used.
- Test 1.3 (test_eligible_and_excluded_rooms): 6 eligible rooms have exactly 1 node each (118 m^2), excluded Bathroom has 0 nodes.
- Test 1.4 (test_fixed_node_immutability_and_clearance): Nodes immutable, strictly inside rooms, clearance >= 0.50m - 0.001m.
- Test 1.5 (test_precision_and_contract_export): MetricFloorPlan coordinates preserve 0.1mm precision, frozen contracts.
"""

import copy
import json
from pathlib import Path
import pytest
import shapely
from shapely.geometry import Point

from resplan_router.contracts import FixedNode, MetricFloorPlan, Point2D
from resplan_router.phase1_ingestion.parser import (
    build_obstacles_and_freespace,
    classify_room,
    parse_and_heal_polygon,
    parse_floor_plan,
)
from resplan_router.phase1_ingestion.scale_converter import (
    compute_scale_factor,
    normalize_to_metric,
    parse_geometry,
)


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def slide4_data(fixtures_dir: Path) -> dict:
    with open(fixtures_dir / "slide4_scale_plan.json", "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def slide5_data(fixtures_dir: Path) -> dict:
    with open(fixtures_dir / "slide5_schematic.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Test 1.1: Exact Slide 4 Metric Scale Conversion
# ---------------------------------------------------------------------------
def test_slide4_exact_scale_conversion(slide4_data: dict) -> None:
    """Verify Slide 4 numerical specifications:

    - 200 x 180 drawing units
    - inner.area = 36000
    - plan area = 110.0 m^2
    - scale = sqrt(36000 / 110.0) = 18.09068 +- 1e-4
    - metric width ~ 11.055 m, height ~ 9.950 m
    - 0.5 m clearance in drawing units ~ 9.045 units
    """
    scale = compute_scale_factor(slide4_data)
    expected_scale = 18.09068
    assert scale == pytest.approx(expected_scale, rel=1e-4)

    inner_geom = parse_geometry(slide4_data["inner"])
    metric_geom = normalize_to_metric(inner_geom, scale)

    min_x, min_y, max_x, max_y = metric_geom.bounds
    width_m = max_x - min_x
    height_m = max_y - min_y

    assert width_m == pytest.approx(11.055, abs=0.005)
    assert height_m == pytest.approx(9.950, abs=0.005)
    assert min_x == pytest.approx(0.0, abs=1e-6)
    assert min_y == pytest.approx(0.0, abs=1e-6)
    assert metric_geom.area == pytest.approx(110.0, rel=1e-5)

    drawing_units_0_5m = 0.5 * scale
    assert drawing_units_0_5m == pytest.approx(9.045, abs=0.01)


# ---------------------------------------------------------------------------
# Test 1.2: Immunity to net_area (0 in 32% of ResPlan plans)
# ---------------------------------------------------------------------------
def test_net_area_ignored(slide4_data: dict) -> None:
    """Verify that net_area is strictly ignored whether 0, huge, or omitted."""
    data_zero = copy.deepcopy(slide4_data)
    data_zero["net_area"] = 0.0

    data_huge = copy.deepcopy(slide4_data)
    data_huge["net_area"] = 999999.0

    data_none = copy.deepcopy(slide4_data)
    del data_none["net_area"]

    scale_zero = compute_scale_factor(data_zero)
    scale_huge = compute_scale_factor(data_huge)
    scale_none = compute_scale_factor(data_none)

    assert scale_zero == scale_huge == scale_none
    assert scale_zero == pytest.approx(18.09068, rel=1e-4)


# ---------------------------------------------------------------------------
# Test 1.3: Eligible (118 m^2, 6 rooms, 6 nodes) vs Excluded (Bathroom: 0 nodes)
# ---------------------------------------------------------------------------
def test_eligible_and_excluded_rooms(slide5_data: dict) -> None:
    """Verify Slide 5 benchmark room partition and node assignment:

    - 6 eligible rooms: Living, Bedroom 1, Bedroom 2, Bedroom 3, Kitchen, Storage
    - Exactly 1 node per eligible room
    - Excluded Bathroom has 0 nodes
    - Total eligible area is exactly 118 m^2
    """
    plan = parse_floor_plan(slide5_data)
    assert isinstance(plan, MetricFloorPlan)
    assert plan.scale_units_per_m == pytest.approx(10.0, rel=1e-4)

    # Classify rooms into eligible and excluded
    eligible_rooms = [r for r in plan.rooms.values() if r.is_eligible]
    excluded_rooms = [r for r in plan.rooms.values() if not r.is_eligible]

    assert len(eligible_rooms) == 6
    assert len(excluded_rooms) >= 1

    eligible_categories = {r.category for r in eligible_rooms}
    assert eligible_categories == {"living", "bedroom", "kitchen", "storage"}

    # Slide 5 specification: Total eligible area = 118 m^2
    total_eligible_area = sum(r.area_m2 for r in eligible_rooms)
    assert total_eligible_area == pytest.approx(118.0, abs=0.01)

    # Individual room areas from Slide 5
    room_areas_by_id = {r.room_id: r.area_m2 for r in plan.rooms.values()}
    assert room_areas_by_id["R01"] == pytest.approx(40.0, abs=0.01)  # Living
    assert room_areas_by_id["R02"] == pytest.approx(16.0, abs=0.01)  # Bedroom 1
    assert room_areas_by_id["R03"] == pytest.approx(15.0, abs=0.01)  # Bedroom 3
    assert room_areas_by_id["R04"] == pytest.approx(16.0, abs=0.01)  # Bedroom 2
    assert room_areas_by_id["R05"] == pytest.approx(16.0, abs=0.01)  # Kitchen
    assert room_areas_by_id["R06"] == pytest.approx(15.0, abs=0.01)  # Storage

    # Excluded room: Bathroom (R07)
    bathroom = plan.rooms["R07"]
    assert bathroom.category == "bathroom"
    assert bathroom.is_eligible is False
    assert bathroom.area_m2 == pytest.approx(8.0, abs=0.01)

    # Node count: 6 nodes total, mapped 1:1 to eligible rooms
    assert len(plan.nodes) == 6
    nodes_per_room = {}
    for node in plan.nodes.values():
        nodes_per_room[node.room_id] = nodes_per_room.get(node.room_id, 0) + 1

    for r in eligible_rooms:
        assert nodes_per_room.get(r.room_id, 0) == 1

    # Bathroom must have exactly 0 nodes
    assert nodes_per_room.get("R07", 0) == 0


# ---------------------------------------------------------------------------
# Test 1.4: Fixed Node Immutability & Clearance Verification
# ---------------------------------------------------------------------------
def test_fixed_node_immutability_and_clearance(slide5_data: dict) -> None:
    """Verify:

    1. Input node coordinates are never modified/shifted.
    2. FixedNode instances are frozen (immutable).
    3. Every node lies inside its host room.
    4. Every node satisfies distance(N_i, obstacles) >= 0.50 m - 0.001 m.
    5. Fallback polylabel generates valid nodes satisfying clearance.
    """
    plan = parse_floor_plan(slide5_data)
    obstacles_geom = plan.obstacles_geom

    # Expected metric coordinates from slide5 input JSON (drawing units / 10.0)
    expected_locations = {
        "N1": (7.0, 2.5),
        "N2": (2.0, 7.0),
        "N3": (1.5, 2.5),
        "N4": (6.0, 7.0),
        "N5": (10.0, 7.0),
        "N6": (12.5, 2.5),
    }

    clearance_threshold = 0.50 - 0.001  # 0.499 m

    for node_id, node in plan.nodes.items():
        exp_x, exp_y = expected_locations[node_id]
        # Immutability of pre-set coordinates
        assert node.location.x == pytest.approx(exp_x, abs=1e-4)
        assert node.location.y == pytest.approx(exp_y, abs=1e-4)

        # Host room containment
        host_room = plan.rooms[node.room_id]
        room_poly = host_room.geometry
        node_pt = Point(node.location.x, node.location.y)
        assert room_poly.contains(node_pt) or room_poly.distance(node_pt) < 1e-4

        # Clearance to obstacles >= 0.50m - 0.001m
        dist_to_obs = obstacles_geom.distance(node_pt)
        assert dist_to_obs >= clearance_threshold, (
            f"Node {node_id} clearance violation: {dist_to_obs:.4f} m < {clearance_threshold:.4f} m"
        )

    # Immutability check: modifying frozen model raises error
    n1 = plan.nodes["N1"]
    with pytest.raises(Exception):
        n1.location = Point2D(x=0.0, y=0.0)

    # Deterministic polylabel fallback test: remove nodes and verify generation
    data_no_nodes = copy.deepcopy(slide5_data)
    del data_no_nodes["nodes"]

    fallback_plan = parse_floor_plan(data_no_nodes)
    assert len(fallback_plan.nodes) == 6

    for fallback_node_id, fallback_node in fallback_plan.nodes.items():
        fb_pt = Point(fallback_node.location.x, fallback_node.location.y)
        fb_host = fallback_plan.rooms[fallback_node.room_id]
        assert fb_host.geometry.contains(fb_pt) or fb_host.geometry.distance(fb_pt) < 1e-4

        fb_dist = fallback_plan.obstacles_geom.distance(fb_pt)
        assert fb_dist >= clearance_threshold, (
            f"Fallback node {fallback_node_id} clearance violation: {fb_dist:.4f} m"
        )


# ---------------------------------------------------------------------------
# Test 1.5: Precision and Contract Export
# ---------------------------------------------------------------------------
def test_precision_and_contract_export(slide5_data: dict) -> None:
    """Verify that MetricFloorPlan preserves 0.1 mm precision and exports cleanly."""
    plan = parse_floor_plan(slide5_data)

    # 1. 0.1 mm precision verification (at most 4 decimal places)
    for node in plan.nodes.values():
        x_str = f"{node.location.x:.8f}".rstrip("0")
        y_str = f"{node.location.y:.8f}".rstrip("0")
        x_decimals = len(x_str.split(".")[1]) if "." in x_str else 0
        y_decimals = len(y_str.split(".")[1]) if "." in y_str else 0
        assert x_decimals <= 4, f"X coordinate {node.location.x} exceeds 0.1 mm precision"
        assert y_decimals <= 4, f"Y coordinate {node.location.y} exceeds 0.1 mm precision"
        assert node.location.x >= 0.0
        assert node.location.y >= 0.0

    # 2. Free space geometry validity
    assert not plan.free_space_geom.is_empty
    assert plan.free_space_geom.is_valid
    assert plan.free_space_geom.area > 0.0

    # 3. Serialization to dict and deserialization
    exported = plan.model_dump()
    assert exported["plan_id"] == "slide5_schematic"
    assert len(exported["rooms"]) == 7
    assert len(exported["nodes"]) == 6

    re_imported = MetricFloorPlan.model_validate(exported)
    assert re_imported.plan_id == plan.plan_id
    assert len(re_imported.nodes) == len(plan.nodes)
    assert re_imported == plan
