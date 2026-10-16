"""Unit tests for ResPlan dataset repository."""

import pytest
from pathlib import Path
from resplan_router.phase1_ingestion.dataset_repository import (
    DatasetRepository,
    DatasetUnavailableError,
    InvalidPlanIdError,
    MalformedRecordError,
    PlanNotFoundError,
)
from resplan_router.contracts import MetricFloorPlan


def test_dataset_repository_singleton():
    DatasetRepository.reset_instance()
    repo1 = DatasetRepository.get_instance()
    repo2 = DatasetRepository.get_instance()
    assert repo1 is repo2
    assert repo1.is_ready
    assert repo1.record_count == 17000


def test_dataset_repository_status():
    repo = DatasetRepository.get_instance()
    status = repo.get_status()
    assert status["ready"] is True
    assert status["record_count"] == 17000
    assert status["dataset"] == "ResPlan.pkl"
    assert status["error"] is None


def test_parse_plan_id_valid_and_invalid():
    assert DatasetRepository.parse_plan_id(14433) == 14433
    assert DatasetRepository.parse_plan_id("14433") == 14433
    assert DatasetRepository.parse_plan_id("  14433  ") == 14433

    with pytest.raises(InvalidPlanIdError):
        DatasetRepository.parse_plan_id(-5)

    with pytest.raises(InvalidPlanIdError):
        DatasetRepository.parse_plan_id("abc")

    with pytest.raises(InvalidPlanIdError):
        DatasetRepository.parse_plan_id("")

    with pytest.raises(InvalidPlanIdError):
        DatasetRepository.parse_plan_id(None)


def test_get_record_and_caching():
    repo = DatasetRepository.get_instance()
    rec = repo.get_record(14433)
    assert rec["id"] == 14433
    assert "area" in rec
    assert "inner" in rec
    assert "bedroom" in rec

    with pytest.raises(PlanNotFoundError):
        repo.get_record(99999999)


def test_get_floor_plan_and_preview_geojson():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    assert isinstance(plan, MetricFloorPlan)
    assert plan.plan_id == "14433"
    assert len(plan.rooms) > 0
    assert len(plan.nodes) > 0

    # Test LRU cache returns same plan
    plan2 = repo.get_floor_plan("14433")
    assert plan is plan2

    # Test preview GeoJSON
    preview = repo.get_preview_geojson(14433)
    assert preview["status"] == "ok"
    assert preview["plan_id"] == "14433"
    assert preview["total_area_m2"] > 0
    assert preview["net_area_m2"] > 0
    assert len(preview["rooms"]) > 0
    assert len(preview["nodes"]) > 0
    assert preview["preview_mode"] is True


def test_dataset_unavailable_error(tmp_path):
    missing_pkl = tmp_path / "nonexistent.pkl"
    repo = DatasetRepository(dataset_path=missing_pkl, auto_load=False)
    assert not repo.is_ready

    with pytest.raises(DatasetUnavailableError):
        repo.get_record(14433)
