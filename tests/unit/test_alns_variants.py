"""Unit tests for ALNS Variants (Subagent 5)."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.optimizers.alns_variants import ALNSRandomOptimizer, ALNSBanditOptimizer
from resplan_router.phase6_output.checker import FloorPlanChecker


@pytest.fixture(scope="module")
def sample_plan():
    repo = DatasetRepository.get_instance()
    return repo.get_floor_plan("14433")


def test_alns_random_optimizer(sample_plan):
    opt = ALNSRandomOptimizer(max_iterations=5)
    res = opt.optimize(sample_plan, time_limit_seconds=2.0)
    assert res.is_valid
    assert res.bundle is not None
    assert res.h_count >= 1
    assert res.total_cost_J > 0
    report = FloorPlanChecker.evaluate(sample_plan, res.bundle)
    assert report.is_valid


def test_alns_bandit_optimizer(sample_plan):
    opt = ALNSBanditOptimizer(max_iterations=5)
    res = opt.optimize(sample_plan, time_limit_seconds=2.0)
    assert res.is_valid
    assert res.bundle is not None
    assert res.h_count >= 1
    assert res.total_cost_J > 0
    report = FloorPlanChecker.evaluate(sample_plan, res.bundle)
    assert report.is_valid
