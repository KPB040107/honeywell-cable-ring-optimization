"""Unit tests for ResPlan geometry adapter."""

import json
import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.resplan_adapter import (
    adapt_resplan_record,
    floor_plan_to_preview_geojson,
    classify_room,
)
from resplan_router.contracts import RoomCategory


def test_classify_room():
    is_el, cat = classify_room("bedroom")
    assert is_el is True
    assert cat == RoomCategory.BEDROOM

    is_el, cat = classify_room("living")
    assert is_el is True
    assert cat == RoomCategory.LIVING

    is_el, cat = classify_room("kitchen")
    assert is_el is True
    assert cat == RoomCategory.KITCHEN

    is_el, cat = classify_room("bathroom")
    assert is_el is False
    assert cat == RoomCategory.BATHROOM

    is_el, cat = classify_room("balcony")
    assert is_el is False
    assert cat == RoomCategory.BALCONY


def test_adapt_resplan_record_structure():
    repo = DatasetRepository.get_instance()
    rec = repo.get_record(14433)
    plan = adapt_resplan_record(rec)

    assert plan.plan_id == "14433"
    assert plan.total_area_m2 > 0
    assert plan.scale_units_per_m > 0
    assert plan.free_space_geom is not None
    assert plan.obstacles_geom is not None

    # Verify deterministic naming
    room_ids = list(plan.rooms.keys())
    for r_id in room_ids:
        assert "_" in r_id
        prefix, num = r_id.split("_", 1)
        assert num.isdigit()

    # Verify eligible rooms exist
    eligible = [r for r in plan.rooms.values() if r.is_eligible]
    assert len(eligible) >= 3

    # Verify nodes generated (1 per eligible room)
    assert len(plan.nodes) == len(eligible)
    for n in plan.nodes.values():
        assert n.room_id in plan.rooms
        assert plan.rooms[n.room_id].is_eligible


def test_floor_plan_to_preview_geojson_serializable():
    repo = DatasetRepository.get_instance()
    rec = repo.get_record(14433)
    plan = adapt_resplan_record(rec)
    preview = floor_plan_to_preview_geojson(plan, rec)

    # Must be valid json-serializable
    dumped = json.dumps(preview)
    loaded = json.loads(dumped)

    assert loaded["plan_id"] == "14433"
    assert loaded["eligible_rooms_count"] == len([r for r in plan.rooms.values() if r.is_eligible])
    assert loaded["device_count"] == len(plan.nodes)
    assert loaded["preview_mode"] is True
    assert isinstance(loaded["rooms"], list)
    assert isinstance(loaded["obstacles"], list)
    assert isinstance(loaded["nodes"], list)
