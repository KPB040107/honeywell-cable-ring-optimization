"""Unit tests for ResPlan Cable Ring Router Public API (Agent 1).

Tests:
1. Package imports, __version__ == "2.0.0", and __all__ exports.
2. Single-plan optimization (MetricFloorPlan, file path, dict, plan ID, different algorithms).
3. SolverStatus enum and custom optimizer injection (OPTIMAL, FEASIBLE, INFEASIBLE, TIMEOUT, ERROR).
4. Error handling and input isolation (non-existent paths, malformed JSON, unhandled exceptions).
5. Validation API (validate_solution) with valid, invalid, empty, and file-based submissions.
6. Batch execution (optimize_batch) sequential, concurrent, checkpointing, and error containment.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest.mock as mock
import pytest

import resplan_router
from resplan_router import (
    BatchResult,
    MetricFloorPlan,
    OptimizationConfig,
    OptimizationResult,
    Point2D,
    SolverStatus,
    SynthesizedRingsBundle,
    SystemConfig,
    ValidationResult,
    optimize_batch,
    optimize_plan,
    validate_solution,
)
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture(scope="module")
def dataset_repo() -> DatasetRepository:
    """Singleton dataset repository."""
    return DatasetRepository.get_instance()


@pytest.fixture(scope="module")
def plan_0(dataset_repo: DatasetRepository) -> MetricFloorPlan:
    """Canonical Plan 0."""
    return dataset_repo.get_floor_plan("0")


@pytest.fixture(scope="module")
def plan_4(dataset_repo: DatasetRepository) -> MetricFloorPlan:
    """Canonical Plan 4."""
    return dataset_repo.get_floor_plan("4")


@pytest.fixture
def fixture_json_path() -> Path:
    """Path to demo three rooms fixture JSON file."""
    p = Path("tests/fixtures/demo_three_rooms_window.json").resolve()
    assert p.is_file(), f"Fixture file not found: {p}"
    return p


# =============================================================================
# 1. PACKAGE EXPORTS & VERSIONING
# =============================================================================


def test_package_exports() -> None:
    """Verify package version is 2.0.0 and all required public symbols are exported."""
    assert resplan_router.__version__ == "2.0.0"

    expected_symbols = [
        "optimize_plan",
        "optimize_batch",
        "validate_solution",
        "OptimizationResult",
        "BatchResult",
        "ValidationResult",
        "SolverStatus",
        "OptimizationConfig",
        "SystemConfig",
        "MetricFloorPlan",
        "Point2D",
        "SynthesizedRingsBundle",
    ]
    for sym in expected_symbols:
        assert hasattr(resplan_router, sym), f"Expected resplan_router to export '{sym}'"


# =============================================================================
# 2. SINGLE PLAN OPTIMIZATION
# =============================================================================


def test_optimize_plan_with_metric_floor_plan(plan_0: MetricFloorPlan) -> None:
    """Verify optimize_plan works with a pre-parsed MetricFloorPlan."""
    config = OptimizationConfig(algorithm="greedy")
    result: OptimizationResult = optimize_plan(plan_0, config=config)

    assert isinstance(result, OptimizationResult)
    assert result.plan_id == plan_0.plan_id
    assert result.is_valid is True
    assert result.status in (SolverStatus.FEASIBLE, SolverStatus.OPTIMAL)
    assert result.hub_count > 0
    assert result.total_cost_J > 0.0
    assert result.total_length_m > 0.0
    assert result.solution is not None
    assert result.bundle is result.solution
    assert result.h_count == result.hub_count
    assert result.slide8_dict is not None
    assert "hubs" in result.slide8_dict

    # Check Slide 3 rules checklist
    for rule_num in range(1, 7):
        assert result.rules_passed.get(f"Rule {rule_num}") is True
    assert len(result.violations) == 0


def test_optimize_plan_with_file_path(fixture_json_path: Path) -> None:
    """Verify optimize_plan works with a string or Path file path."""
    config = {"algorithm": "greedy"}
    result: OptimizationResult = optimize_plan(fixture_json_path, config=config)

    assert isinstance(result, OptimizationResult)
    assert result.is_valid is True
    assert result.status == SolverStatus.FEASIBLE
    assert result.total_cost_J > 0.0


def test_optimize_plan_with_plan_id_str() -> None:
    """Verify optimize_plan resolves integer or string Plan ID via DatasetRepository."""
    result: OptimizationResult = optimize_plan("0", config={"algorithm": "greedy"})

    assert isinstance(result, OptimizationResult)
    assert result.plan_id == "0"
    assert result.is_valid is True


def test_optimize_plan_with_raw_dict(fixture_json_path: Path) -> None:
    """Verify optimize_plan works with raw floor plan dictionary."""
    with open(fixture_json_path, "r", encoding="utf-8") as f:
        raw_dict = json.load(f)

    result: OptimizationResult = optimize_plan(raw_dict, config={"algorithm": "greedy"})
    assert isinstance(result, OptimizationResult)
    assert result.is_valid is True


def test_optimize_plan_pipeline_algorithm(plan_0: MetricFloorPlan) -> None:
    """Verify optimize_plan with algorithm='pipeline' runs end-to-end deterministic pipeline."""
    result = optimize_plan(plan_0, config={"algorithm": "pipeline"})
    assert result.is_valid is True
    assert result.algorithm == "pipeline"
    assert result.slide8_dict is not None


def test_optimization_result_serialization(plan_0: MetricFloorPlan, tmp_path: Path) -> None:
    """Verify to_dict(), to_json(), and save_slide8() methods of OptimizationResult."""
    result = optimize_plan(plan_0, config={"algorithm": "greedy"})
    d = result.to_dict()
    assert isinstance(d, dict)
    assert d["plan_id"] == plan_0.plan_id
    assert d["is_valid"] is True

    json_str = result.to_json()
    assert isinstance(json_str, str)
    parsed = json.loads(json_str)
    assert parsed["plan_id"] == plan_0.plan_id

    # Test save_slide8
    out_file = tmp_path / "slide8_output.json"
    result.save_slide8(out_file)
    assert out_file.is_file()
    with open(out_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert "hubs" in saved_data


# =============================================================================
# 3. SOLVER STATUS REPORTING & OPTIMIZER INJECTION
# =============================================================================


def test_solver_status_values() -> None:
    """Verify all SolverStatus enum variants exist."""
    assert SolverStatus.OPTIMAL == "OPTIMAL"
    assert SolverStatus.FEASIBLE == "FEASIBLE"
    assert SolverStatus.INFEASIBLE == "INFEASIBLE"
    assert SolverStatus.TIMEOUT == "TIMEOUT"
    assert SolverStatus.ERROR == "ERROR"


def test_custom_optimizer_mock_statuses(plan_0: MetricFloorPlan) -> None:
    """Test solver status propagation using mock custom optimizers."""
    # 1. OPTIMAL status
    mock_opt = mock.Mock(spec=BaseFloorPlanOptimizer)
    mock_opt.name = "MockOptimalSolver"
    mock_opt.optimize.return_value = OptimizerResult(
        algorithm_name="MockOptimalSolver",
        plan_id=plan_0.plan_id,
        bundle=None,
        partition=None,
        h_count=1,
        total_length_m=10.0,
        total_bends=2,
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=11.0,
        area_gap_m2=0.0,
        is_valid=True,
        solver_status="OPTIMAL",
        runtime_s=0.1,
    )

    res = optimize_plan(plan_0, config={"optimizer": mock_opt})
    assert res.status == SolverStatus.OPTIMAL
    assert res.is_valid is True

    # 2. TIMEOUT status
    mock_opt.optimize.return_value = OptimizerResult(
        algorithm_name="MockTimeoutSolver",
        plan_id=plan_0.plan_id,
        bundle=None,
        partition=None,
        h_count=0,
        total_length_m=0.0,
        total_bends=0,
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=float("inf"),
        area_gap_m2=0.0,
        is_valid=False,
        solver_status="TIMEOUT",
        runtime_s=5.0,
    )
    res_timeout = optimize_plan(plan_0, config={"optimizer": mock_opt})
    assert res_timeout.status == SolverStatus.TIMEOUT
    assert res_timeout.is_valid is False

    # 3. INFEASIBLE status
    mock_opt.optimize.return_value = OptimizerResult(
        algorithm_name="MockInfeasibleSolver",
        plan_id=plan_0.plan_id,
        bundle=None,
        partition=None,
        h_count=0,
        total_length_m=0.0,
        total_bends=0,
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=float("inf"),
        area_gap_m2=0.0,
        is_valid=False,
        solver_status="INFEASIBLE",
        runtime_s=0.2,
    )
    res_infeasible = optimize_plan(plan_0, config={"optimizer": mock_opt})
    assert res_infeasible.status == SolverStatus.INFEASIBLE
    assert res_infeasible.is_valid is False

    # 4. Exception in optimizer -> SolverStatus.ERROR
    mock_opt.optimize.side_effect = RuntimeError("Hardware fault or out of memory")
    res_err = optimize_plan(plan_0, config={"optimizer": mock_opt})
    assert res_err.status == SolverStatus.ERROR
    assert res_err.is_valid is False
    assert "Hardware fault" in res_err.message


# =============================================================================
# 4. ERROR HANDLING & INPUT NORMALIZATION
# =============================================================================


def test_optimize_plan_non_existent_file() -> None:
    """Verify non-existent file path returns structured ERROR result without raising."""
    result = optimize_plan("c:/non/existent/path/floorplan.json")
    assert isinstance(result, OptimizationResult)
    assert result.status == SolverStatus.ERROR
    assert result.is_valid is False
    assert len(result.violations) > 0


def test_optimize_plan_malformed_json_dict() -> None:
    """Verify malformed floor plan dictionary returns structured ERROR result."""
    result = optimize_plan({"invalid_key": 12345})
    assert isinstance(result, OptimizationResult)
    assert result.status == SolverStatus.ERROR
    assert result.is_valid is False


def test_optimize_plan_unsupported_type() -> None:
    """Verify unsupported type returns structured ERROR result."""
    result = optimize_plan([1, 2, 3])  # type: ignore
    assert isinstance(result, OptimizationResult)
    assert result.status == SolverStatus.ERROR
    assert result.is_valid is False


# =============================================================================
# 5. VALIDATION API (validate_solution)
# =============================================================================


def test_validate_solution_valid_bundle(plan_0: MetricFloorPlan) -> None:
    """Verify validate_solution reports 100% valid on a legal solution."""
    opt_res = optimize_plan(plan_0, config={"algorithm": "greedy"})
    assert opt_res.solution is not None

    val_res: ValidationResult = validate_solution(plan_0, opt_res.solution)
    assert isinstance(val_res, ValidationResult)
    assert val_res.is_valid is True
    assert val_res.status in (SolverStatus.FEASIBLE, SolverStatus.OPTIMAL)
    assert val_res.hub_count > 0
    assert val_res.total_cost_J > 0.0
    assert len(val_res.violations) == 0

    for r_num in range(1, 7):
        assert val_res.rules_passed[f"Rule {r_num}"] is True


def test_validate_solution_from_optimization_result(plan_0: MetricFloorPlan) -> None:
    """Verify validate_solution accepts an OptimizationResult object directly."""
    opt_res = optimize_plan(plan_0, config={"algorithm": "greedy"})
    val_res = validate_solution(plan_0, opt_res)
    assert val_res.is_valid is True


def test_validate_solution_from_slide8_dict(plan_0: MetricFloorPlan) -> None:
    """Verify validate_solution accepts a Slide 8 dictionary."""
    opt_res = optimize_plan(plan_0, config={"algorithm": "greedy"})
    assert opt_res.slide8_dict is not None
    val_res = validate_solution(plan_0, opt_res.slide8_dict)
    assert val_res.is_valid is True


def test_validate_solution_from_file(plan_0: MetricFloorPlan, tmp_path: Path) -> None:
    """Verify validate_solution accepts a path to a Slide 8 JSON file."""
    opt_res = optimize_plan(plan_0, config={"algorithm": "greedy"})
    slide8_file = tmp_path / "slide8.json"
    opt_res.save_slide8(slide8_file)

    val_res = validate_solution(plan_0, slide8_file)
    assert val_res.is_valid is True


def test_validate_solution_invalid_submission(plan_0: MetricFloorPlan) -> None:
    """Verify validate_solution correctly flags violations on invalid/empty solution."""
    empty_submission = {"hubs": []}
    val_res = validate_solution(plan_0, empty_submission)

    assert isinstance(val_res, ValidationResult)
    assert val_res.is_valid is False
    assert val_res.status == SolverStatus.INFEASIBLE
    assert len(val_res.violations) > 0


def test_validate_solution_none_submission(plan_0: MetricFloorPlan) -> None:
    """Verify validate_solution handles None gracefully."""
    val_res = validate_solution(plan_0, None)  # type: ignore
    assert val_res.is_valid is False
    assert val_res.status == SolverStatus.INFEASIBLE


def test_validation_result_serialization(plan_0: MetricFloorPlan) -> None:
    """Verify ValidationResult dictionary and JSON export."""
    opt_res = optimize_plan(plan_0, config={"algorithm": "greedy"})
    val_res = validate_solution(plan_0, opt_res)

    d = val_res.to_dict()
    assert d["is_valid"] is True
    assert "total_cost_J" in d

    s = val_res.to_json()
    parsed = json.loads(s)
    assert parsed["plan_id"] == plan_0.plan_id


# =============================================================================
# 6. BATCH OPTIMIZATION (optimize_batch)
# =============================================================================


def test_optimize_batch_empty() -> None:
    """Verify optimize_batch handles empty list of plans."""
    batch_res = optimize_batch([])
    assert isinstance(batch_res, BatchResult)
    assert batch_res.total_plans == 0
    assert batch_res.successful == 0
    assert len(batch_res) == 0


def test_optimize_batch_sequential(plan_0: MetricFloorPlan, plan_4: MetricFloorPlan) -> None:
    """Verify sequential batch execution (max_workers=1)."""
    plans = [plan_0, plan_4]
    config = OptimizationConfig(algorithm="greedy")
    batch_res = optimize_batch(plans, config=config, max_workers=1)

    assert isinstance(batch_res, BatchResult)
    assert batch_res.total_plans == 2
    assert batch_res.successful == 2
    assert batch_res.failed == 0
    assert len(batch_res) == 2
    assert batch_res.total_cost_J > 0.0

    # Test indexing & lookup
    assert batch_res[0].plan_id == plan_0.plan_id
    assert batch_res[1].plan_id == plan_4.plan_id
    assert batch_res["0"].is_valid is True
    assert batch_res["4"].is_valid is True
    assert batch_res.get("0") is not None
    assert batch_res.get("999999") is None


def test_optimize_batch_concurrent(plan_0: MetricFloorPlan, plan_4: MetricFloorPlan) -> None:
    """Verify concurrent batch execution (max_workers=2)."""
    plans = [plan_0, plan_4]
    config = {"algorithm": "greedy"}
    batch_res = optimize_batch(plans, config=config, max_workers=2)

    assert batch_res.total_plans == 2
    assert batch_res.successful == 2
    assert len(batch_res.results) == 2
    assert batch_res[0].plan_id == plan_0.plan_id
    assert batch_res[1].plan_id == plan_4.plan_id


def test_optimize_batch_with_checkpointing(
    plan_0: MetricFloorPlan, plan_4: MetricFloorPlan, tmp_path: Path
) -> None:
    """Verify optimize_batch writes atomic JSON checkpoints during execution."""
    checkpoint_file = tmp_path / "batch_checkpoint.json"
    plans = [plan_0, plan_4]
    config = {"algorithm": "greedy"}

    batch_res = optimize_batch(
        plans, config=config, max_workers=2, checkpoint_path=checkpoint_file
    )

    assert checkpoint_file.is_file()
    with open(checkpoint_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["total_plans"] == 2
    assert data["completed"] == 2
    assert data["successful"] == 2
    assert len(data["results"]) == 2
    assert batch_res.checkpoint_saved == str(checkpoint_file)


def test_optimize_batch_error_isolation(plan_0: MetricFloorPlan) -> None:
    """Verify batch continues and isolates failures when one plan input is invalid."""
    plans = [plan_0, "c:/invalid/path/nonexistent.json"]
    config = {"algorithm": "greedy"}

    batch_res = optimize_batch(plans, config=config, max_workers=2)
    assert batch_res.total_plans == 2
    assert batch_res.successful == 1
    assert batch_res.failed == 1

    # Plan 0 succeeded
    assert batch_res[0].is_valid is True
    assert batch_res[0].status == SolverStatus.FEASIBLE

    # Invalid plan recorded ERROR without crashing batch
    assert batch_res[1].is_valid is False
    assert batch_res[1].status == SolverStatus.ERROR


def test_batch_result_serialization(plan_0: MetricFloorPlan) -> None:
    """Verify BatchResult to_dict() and to_json()."""
    batch_res = optimize_batch([plan_0], config={"algorithm": "greedy"})
    d = batch_res.to_dict()
    assert d["total_plans"] == 1
    assert d["successful"] == 1
    assert len(d["results"]) == 1

    s = batch_res.to_json()
    parsed = json.loads(s)
    assert parsed["successful"] == 1
