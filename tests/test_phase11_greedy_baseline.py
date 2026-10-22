"""Unit tests for Phase 11 deterministic greedy baseline solver."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase11_optimization.greedy_baseline import solve_greedy_baseline
from resplan_router.phase6_output.checker import FloorPlanChecker


def test_greedy_baseline_on_dataset_plan():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)

    res = solve_greedy_baseline(plan=plan, seed=42)

    assert res.is_valid is True
    assert res.h_count >= 1
    assert res.total_length_m > 0
    assert res.total_cost_J > 0
    assert res.runtime_s < 10.0

    # Independent checker verification
    report = FloorPlanChecker.evaluate(plan, res.bundle)
    assert report.is_valid is True
    assert all(report.rules_passed.values())
    assert abs(report.total_cost_J - res.total_cost_J) < 1e-3
