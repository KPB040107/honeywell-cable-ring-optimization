"""Comprehensive unit test suite for Phase 2 portal extraction.

Tests door and open-passage portal extraction, parallel lane synthesis (>= 20mm),
and negative assertions for solid walls, windows, and building entrance (front_door).
"""

import json
import math
from pathlib import Path
import pytest
import shapely
from shapely.geometry import box, LineString, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    DoorOrPassagePortal,
    EligibleOrExcludedRoom,
    MetricFloorPlan,
    Point2D,
    PortalType,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.portal_extractor import (
    extract_all_portals,
    extract_door_portals,
    extract_open_passages,
)


def _create_synthetic_two_room_plan(
    wall_gap: tuple[float, float] | None = None,
    has_window: bool = False,
) -> tuple[MetricFloorPlan, list[BaseGeometry]]:
    """Helper creating a 10m x 5m two-room plan (R01 and R02 split at x = 5.0)."""
    inner = box(0.0, 0.0, 10.0, 5.0)
    r1 = box(0.0, 0.0, 5.0, 5.0)
    r2 = box(5.0, 0.0, 10.0, 5.0)

    # Wall along x = 5.0 with optional gap
    walls: list[BaseGeometry] = []
    windows: list[BaseGeometry] = []

    if wall_gap is None:
        # Solid continuous wall from y=0 to y=5
        walls.append(box(4.95, 0.0, 5.05, 5.0))
    else:
        gap_y1, gap_y2 = wall_gap
        if gap_y1 > 0.0:
            walls.append(box(4.95, 0.0, 5.05, gap_y1))
        if gap_y2 < 5.0:
            walls.append(box(4.95, gap_y2, 5.05, 5.0))
        if has_window:
            windows.append(box(4.95, gap_y1, 5.05, gap_y2))

    wall_union = shapely.unary_union(walls) if walls else shapely.Polygon()
    win_union = shapely.unary_union(windows) if windows else shapely.Polygon()
    free_space = inner.difference(shapely.union(wall_union, win_union))
    obstacles = shapely.union(wall_union, win_union)

    rooms = {
        "R01": EligibleOrExcludedRoom(
            room_id="R01",
            category="living",
            is_eligible=True,
            area_m2=25.0,
            polygon_wkt=r1.wkt,
        ),
        "R02": EligibleOrExcludedRoom(
            room_id="R02",
            category="bedroom",
            is_eligible=True,
            area_m2=25.0,
            polygon_wkt=r2.wkt,
        ),
    }

    plan = MetricFloorPlan(
        plan_id="synthetic_2room",
        scale_units_per_m=1.0,
        total_area_m2=50.0,
        inner_wkt=inner.wkt,
        free_space_wkt=free_space.wkt,
        obstacles_wkt=obstacles.wkt,
        rooms=rooms,
        nodes={},
    )
    return plan, walls


# ---------------------------------------------------------------------------
# Test 1: Door extraction on synthetic two-room plan
# ---------------------------------------------------------------------------
def test_two_room_door_extraction() -> None:
    """Verify door portal extraction between two adjacent rooms with door polygon."""
    # Door gap between y = 2.0 and y = 3.0 (width = 1.0m)
    plan, _ = _create_synthetic_two_room_plan(wall_gap=(2.0, 3.0))
    door = box(4.9, 2.0, 5.1, 3.0)

    portals = extract_door_portals(plan, raw_doors=[door])
    assert len(portals) == 1

    p = portals[0]
    assert p.portal_type == PortalType.DOOR
    assert p.room1_id == "R01"
    assert p.room2_id == "R02"
    assert p.width_m == pytest.approx(1.0, abs=0.01)
    assert p.center_point.x == pytest.approx(5.0, abs=0.01)
    assert p.center_point.y == pytest.approx(2.5, abs=0.01)

    # Offset delta = min(0.10, 1.0 / 4) = 0.10 m (>= 0.02 m)
    assert p.lateral_offset_m == pytest.approx(0.10, abs=0.001)

    # Parallel lanes separation = 2 * delta = 0.20 m (200 mm >> 1 mm)
    dx = p.lane_b_point.x - p.lane_a_point.x
    dy = p.lane_b_point.y - p.lane_a_point.y
    lane_sep = math.hypot(dx, dy)
    assert lane_sep == pytest.approx(0.20, abs=0.01)
    assert lane_sep >= 0.040  # >= 40 mm >> 1 mm checker tolerance

    # Room step points: step_room1 in R01 (x < 5.0), step_room2 in R02 (x > 5.0)
    assert p.step_room1.x < 5.0
    assert p.step_room2.x > 5.0
    assert plan.rooms["R01"].geometry.contains(p.step_room1.as_shapely())
    assert plan.rooms["R02"].geometry.contains(p.step_room2.as_shapely())

    # Threshold lies inside free space
    assert plan.free_space_geom.covers(p.threshold_geom)


# ---------------------------------------------------------------------------
# Test 2: Entrance and exterior doors strictly rejected
# ---------------------------------------------------------------------------
def test_front_door_and_exterior_door_ignored() -> None:
    """Verify doors intersecting raw_entrance or outer wall boundary produce 0 portals."""
    plan, _ = _create_synthetic_two_room_plan(wall_gap=(2.0, 3.0))

    # Door 1: Exterior door on outer perimeter at y = 0
    exterior_door = box(2.0, -0.05, 3.0, 0.05)

    # Door 2: Door intersecting front_door / entrance
    front_door = box(7.0, -0.05, 8.0, 0.05)
    entrance_geom = box(6.95, -0.1, 8.05, 0.1)

    portals = extract_door_portals(
        plan,
        raw_doors=[exterior_door, front_door],
        raw_entrance=entrance_geom,
    )
    assert len(portals) == 0


# ---------------------------------------------------------------------------
# Test 3: Solid walls without doors produce 0 portals
# ---------------------------------------------------------------------------
def test_solid_wall_produces_zero_portals() -> None:
    """Verify that adjacent rooms sharing only a solid continuous wall produce 0 portals."""
    # Continuous solid wall along x = 5.0
    plan, _ = _create_synthetic_two_room_plan(wall_gap=None)

    # 1. No doors passed -> 0 portals
    portals = extract_all_portals(plan, raw_doors=[])
    assert len(portals) == 0

    # 2. Fake door placed over solid wall -> 0 portals because free space has no opening
    fake_door = box(4.9, 2.0, 5.1, 3.0)
    portals_fake = extract_all_portals(plan, raw_doors=[fake_door])
    assert len(portals_fake) == 0


# ---------------------------------------------------------------------------
# Test 4: Window adjacency strictly produces 0 portals
# ---------------------------------------------------------------------------
def test_window_produces_zero_portals() -> None:
    """Verify that a window in a shared wall is an obstacle and produces 0 portals."""
    plan, _ = _create_synthetic_two_room_plan(wall_gap=(2.0, 3.5), has_window=True)

    # Open passage extractor must reject window because it is subtracted from free space
    portals = extract_all_portals(plan, raw_doors=[])
    assert len(portals) == 0


# ---------------------------------------------------------------------------
# Test 5: Open passage detection for gaps >= 0.50m
# ---------------------------------------------------------------------------
def test_open_passage_detection() -> None:
    """Verify that an unobstructed gap >= 0.50m without a door creates an OPEN_PASSAGE portal."""
    # Open gap between y = 1.5 and y = 3.5 (width = 2.0m)
    plan, _ = _create_synthetic_two_room_plan(wall_gap=(1.5, 3.5))

    portals = extract_all_portals(plan, raw_doors=[])
    assert len(portals) == 1

    p = portals[0]
    assert p.portal_type == PortalType.OPEN_PASSAGE
    assert p.room1_id == "R01"
    assert p.room2_id == "R02"
    assert p.width_m == pytest.approx(2.0, abs=0.01)
    assert p.center_point.x == pytest.approx(5.0, abs=0.01)
    assert p.center_point.y == pytest.approx(2.5, abs=0.01)
    assert plan.free_space_geom.covers(p.threshold_geom)


# ---------------------------------------------------------------------------
# Test 6: Narrow gap (< 0.50m) rejected as open passage
# ---------------------------------------------------------------------------
def test_narrow_gap_rejected_as_open_passage() -> None:
    """Verify that an open gap < 0.50m (e.g. 0.40m slit) is ignored as too narrow."""
    # Gap between y = 2.0 and y = 2.4 (width = 0.40m < 0.50m)
    plan, _ = _create_synthetic_two_room_plan(wall_gap=(2.0, 2.4))

    portals = extract_all_portals(plan, raw_doors=[])
    assert len(portals) == 0


# ---------------------------------------------------------------------------
# Test 7: Slide 5 6-room schematic portal extraction and verification
# ---------------------------------------------------------------------------
def test_slide5_schematic_portal_extraction() -> None:
    """Verify portal extraction on Slide 5 schematic floor plan."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))

    # Exactly 6 interior doors connecting adjacent rooms
    assert len(portals) == 6

    # Verify all expected room connections
    connections = {(p.room1_id, p.room2_id) for p in portals}
    expected_connections = {
        ("R01", "R02"),  # Living - Bedroom 1
        ("R01", "R03"),  # Living - Bedroom 3
        ("R02", "R03"),  # Bedroom 1 - Bedroom 3
        ("R04", "R05"),  # Bedroom 2 - Kitchen
        ("R05", "R06"),  # Kitchen - Storage
        ("R05", "R07"),  # Kitchen - Bathroom
    }
    assert connections == expected_connections

    # Verify that all portals lie inside free space and exceed 20mm separation
    for p in portals:
        assert plan.free_space_geom.covers(p.threshold_geom)
        dx = p.lane_b_point.x - p.lane_a_point.x
        dy = p.lane_b_point.y - p.lane_a_point.y
        sep = math.hypot(dx, dy)
        assert sep >= 0.040, f"Portal {p.portal_id} lane separation {sep} < 40mm"
        assert p.lateral_offset_m >= 0.02


# ---------------------------------------------------------------------------
# Test 8: Pydantic Data Contracts and Validations
# ---------------------------------------------------------------------------
def test_portal_contract_validation() -> None:
    """Verify Pydantic validation rules on DoorOrPassagePortal."""
    # 1. Invalid threshold WKT (not LineString)
    with pytest.raises(ValueError):
        DoorOrPassagePortal(
            portal_id="P01",
            portal_type=PortalType.DOOR,
            room1_id="R01",
            room2_id="R02",
            center_point=Point2D(x=1.0, y=1.0),
            threshold_wkt="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
            width_m=1.0,
            lane_a_point=Point2D(x=0.9, y=1.0),
            lane_b_point=Point2D(x=1.1, y=1.0),
            step_room1=Point2D(x=1.0, y=0.85),
            step_room2=Point2D(x=1.0, y=1.15),
            lateral_offset_m=0.10,
        )

    # 2. Offset < 0.02m
    with pytest.raises(ValueError):
        DoorOrPassagePortal(
            portal_id="P01",
            portal_type=PortalType.DOOR,
            room1_id="R01",
            room2_id="R02",
            center_point=Point2D(x=1.0, y=1.0),
            threshold_wkt="LINESTRING(1 0, 1 2)",
            width_m=2.0,
            lane_a_point=Point2D(x=1.0, y=0.99),
            lane_b_point=Point2D(x=1.0, y=1.01),
            step_room1=Point2D(x=0.85, y=1.0),
            step_room2=Point2D(x=1.15, y=1.0),
            lateral_offset_m=0.01,  # < 0.02m constraint violation
        )

    # 3. Width <= 0.0
    with pytest.raises(ValueError):
        DoorOrPassagePortal(
            portal_id="P01",
            portal_type=PortalType.DOOR,
            room1_id="R01",
            room2_id="R02",
            center_point=Point2D(x=1.0, y=1.0),
            threshold_wkt="LINESTRING(1 0, 1 2)",
            width_m=0.0,  # <= 0.0 constraint violation
            lane_a_point=Point2D(x=1.0, y=0.9),
            lane_b_point=Point2D(x=1.0, y=1.1),
            step_room1=Point2D(x=0.85, y=1.0),
            step_room2=Point2D(x=1.15, y=1.0),
            lateral_offset_m=0.05,
        )
