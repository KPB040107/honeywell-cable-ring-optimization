"""Unit tests for device candidate generation and wall clearance."""

import pytest
from shapely.geometry import Polygon
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.device_candidates import (
    generate_room_device_candidates,
    generate_all_room_devices,
)


def test_generate_room_device_candidates():
    # Simple 6x6 room with obstacle at border
    room_poly = Polygon([(0, 0), (6, 0), (6, 6), (0, 6)])
    cands = generate_room_device_candidates(
        room_id="bedroom_01",
        room_geom=room_poly,
        obstacles=None,
    )
    assert len(cands) > 0
    # Polylabel should be centered
    p0 = cands[0]
    assert 1.0 <= p0.x <= 5.0
    assert 1.0 <= p0.y <= 5.0


def test_device_setback_clearance_on_dataset_plan():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    obs = plan.obstacles_geom

    for nid, node in plan.nodes.items():
        # Node must be in eligible room
        room = plan.rooms[node.room_id]
        assert room.is_eligible

        # Clearance must be >= 0.50 m (within 0.499 m tolerance)
        if obs is not None and not obs.is_empty:
            from shapely.geometry import Point
            pt = Point(node.location.x, node.location.y)
            dist = obs.distance(pt)
            assert dist >= 0.499, f"Node {nid} has clearance {dist} < 0.499m"
