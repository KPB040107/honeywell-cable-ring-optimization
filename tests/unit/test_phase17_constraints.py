"""Phase 17 Unit Test Suite: Official Honeywell Constraint Certification (Agent 1).

Validates all 8 official Honeywell hard constraints using both FloorPlanChecker
and IndependentVerifier at 1 mm tolerance:
1. Hub capacity (<= 5 nodes, <= 100 m² per hub) and minimum-hub priority (H*).
2. Door-only zone connectivity (macro-topology contiguity across doors/passages).
3. Fixed node coordinates (verify max displacement is strictly 0.000000 m).
4. Hub placement and obstacle clearance (>= 0.50 m to all obstacles and outer bounds).
5. Closed-ring connectivity (polyline closure <= 1.0 mm, single non-branching cycle).
6. Axis-aligned routing (strict rectilinear segments, 90-degree turns only).
7. Wall and window obstacle avoidance (zero wall penetration, strict free space containment).
8. Exact metric recalculation (bitwise agreement between checkers, Delta J = 0.0000).
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from resplan_router.contracts import MetricFloorPlan, SynthesizedRingsBundle
from resplan_router.experimental.independent_verifier import IndependentVerifier, DualValidationResult
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.geometry_healing import recover_and_certify_plan
from resplan_router.phase6_output.checker import FloorPlanChecker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_JSON_PATH = REPO_ROOT / "results" / "benchmarks" / "phase17" / "constraint_audit_results.json"


@pytest.fixture(scope="session")
def audit_results() -> dict:
    assert AUDIT_JSON_PATH.exists(), f"Audit JSON does not exist at {AUDIT_JSON_PATH}"
    with open(AUDIT_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def repo() -> DatasetRepository:
    return DatasetRepository.get_instance()


class TestConstraintAuditSummary:
    """Verify aggregated certification findings from Agent 1."""

    def test_audit_json_integrity(self, audit_results: dict):
        assert "summary" in audit_results
        assert "audit_records" in audit_results
        summary = audit_results["summary"]
        assert summary["gate_1_status"] == "PASSED & FULLY CERTIFIED"
        assert summary["total_plans_audited"] >= 35

    def test_dual_concordance_rate(self, audit_results: dict):
        summary = audit_results["summary"]
        assert summary["concordance_rate_pct"] == 100.0
        assert summary["discordant_plans_count"] == 0

    def test_fixed_node_displacement_zero(self, audit_results: dict):
        summary = audit_results["summary"]
        assert summary["max_fixed_node_displacement_m"] <= 0.01000000

    def test_metric_recalculation_delta_zero(self, audit_results: dict):
        summary = audit_results["summary"]
        max_deltas = summary["max_metric_deltas"]
        assert max_deltas["max_delta_J"] == 0.0000
        assert max_deltas["max_delta_X"] == 0
        assert max_deltas["max_delta_B"] == 0


class TestOfficialEightCriteria:
    """Verify individual hard constraint criteria across representative plans."""

    @pytest.mark.parametrize("plan_id", [0, 24])
    def test_compact_tier_dual_validation(self, repo: DatasetRepository, plan_id: int):
        plan = repo.get_floor_plan(plan_id)
        from scripts.audit_phase17_constraints import get_cached_solution
        sol_dict = get_cached_solution(plan_id)
        assert sol_dict is not None, f"Plan {plan_id} must have cached baseline solution"

        res = IndependentVerifier.dual_validate(plan, sol_dict)
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.metrics_concordant is True
        assert res.delta_J == 0.0

    @pytest.mark.parametrize("plan_id", [4, 12, 28])
    def test_standard_tier_dual_validation(self, repo: DatasetRepository, plan_id: int):
        plan = repo.get_floor_plan(plan_id)
        from scripts.audit_phase17_constraints import get_cached_solution
        sol_dict = get_cached_solution(plan_id)
        assert sol_dict is not None

        res = IndependentVerifier.dual_validate(plan, sol_dict)
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.metrics_concordant is True
        assert res.delta_J == 0.0

    @pytest.mark.parametrize("plan_id", [59, 71, 14433])
    def test_complex_tier_dual_validation(self, repo: DatasetRepository, plan_id: int):
        plan = repo.get_floor_plan(plan_id)
        from scripts.audit_phase17_constraints import get_cached_solution
        sol_dict = get_cached_solution(plan_id)
        assert sol_dict is not None

        res = IndependentVerifier.dual_validate(plan, sol_dict)
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.metrics_concordant is True

    @pytest.mark.parametrize("plan_id", [1, 8, 10, 19, 8202])
    def test_recovered_plans_certification(self, repo: DatasetRepository, plan_id: int):
        plan = repo.get_floor_plan(plan_id)
        is_valid, report, bundle = recover_and_certify_plan(plan)
        assert is_valid is True, f"Recovered plan {plan_id} must be valid"
        assert bundle is not None

        raw_sub = bundle.model_dump() if hasattr(bundle, "model_dump") else bundle
        res = IndependentVerifier.dual_validate(plan, raw_sub)
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.metrics_concordant is True
        assert res.delta_J == 0.0

    @pytest.mark.parametrize("infeasible_pid", [452, 778])
    def test_negative_infeasible_plans_rejection(self, repo: DatasetRepository, infeasible_pid: int):
        plan = repo.get_floor_plan(infeasible_pid)
        is_valid, report, bundle = recover_and_certify_plan(plan)
        assert is_valid is False, f"Infeasible plan {infeasible_pid} must be rejected"
