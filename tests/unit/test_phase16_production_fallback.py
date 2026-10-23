"""Phase 16 Unit & Stress Test Suite: Production Fallback Mechanism (Agent 4).

Rigorously verifies and stress-tests:
1. Strict Preserved Lexicographical Ordering:
   Feasibility (100%) > Minimum Hub Count (H*) > Routing Cost (J) > Area Gap (Delta A).
2. Adversarial Edge Cases:
   - v2 produces an invalid solution with wall/obstacle collision (Rule 4).
   - v2 produces an open / disconnected ring topology (Rule 1).
   - v2 encounters search stagnation or execution timeout.
   - v2 produces a solution with higher J cost than incumbent v1 baseline.
   - v2 attempts an illegal multi-zone partition (>100 m^2, >5 devices, or disconnected rooms).
   - v2 raises an unhandled exception or crashes during search.
3. Fallback Invocation Overhead & Microsecond Latency Accounting.
4. End-to-end execution across Compact, Standard, and Complex architectural floor plans.
5. Production fallback operational modes: ARBITRATED, SEEDED, and ON_DEMAND.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
import random
import time
from typing import Any, Dict, List
import unittest.mock as mock

import numpy as np
import pytest

from resplan_router.contracts import (
    CompletedHubRing,
    EvaluationReport,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    RouteSegment,
    RuleViolation,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer
from resplan_router.optimizers.alns_linucb_v2 import LinUCB_ALNS_V2Optimizer
from resplan_router.optimizers.base import (
    BaseFloorPlanOptimizer,
    OptimizerResult,
    create_optimizer_result,
)
from resplan_router.optimizers.production_fallback import (
    FallbackReason,
    ProductionFallbackMode,
    ProductionFallbackOptimizer,
    evaluate_lexicographical_preference,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase6_output.checker import FloorPlanChecker


# =========================================================================
# FIXTURES
# =========================================================================

@pytest.fixture(scope="module")
def dataset_repo() -> DatasetRepository:
    """Obtain DatasetRepository singleton."""
    return DatasetRepository.get_instance()


@pytest.fixture(scope="module")
def canonical_plan_0(dataset_repo: DatasetRepository) -> MetricFloorPlan:
    """Load canonical benchmark Plan 0."""
    return dataset_repo.get_floor_plan("0")


@pytest.fixture(scope="module")
def canonical_plan_4(dataset_repo: DatasetRepository) -> MetricFloorPlan:
    """Load canonical benchmark Plan 4."""
    return dataset_repo.get_floor_plan("4")


@pytest.fixture
def mock_optimizer_results() -> Dict[str, OptimizerResult]:
    """Create a suite of mock OptimizerResults for formal lexicographical auditing."""
    def make_res(
        name: str,
        valid: bool,
        h: int,
        j: float,
        area_gap: float,
        violations: Optional[List[str]] = None,
    ) -> OptimizerResult:
        return OptimizerResult(
            algorithm_name=name,
            plan_id="mock_plan",
            bundle=None,
            partition=None,
            h_count=h,
            total_length_m=j * 0.7,
            total_bends=int(j * 0.2),
            total_crossings=int(j * 0.05),
            total_overlap_m=j * 0.05,
            total_cost_J=j,
            area_gap_m2=area_gap,
            is_valid=valid,
            solver_status="OPTIMAL" if valid else "INFEASIBLE",
            runtime_s=0.5,
            diagnostic_info={},
            rules_passed={f"Rule {i}": valid for i in range(1, 7)},
            violations=violations or ([] if valid else ["Rule 4 (Wall / Obstacle Collision): cable collides with wall"]),
        )

    return {
        "v1_baseline": make_res("v1_champion", valid=True, h=2, j=100.0, area_gap=15.0),
        "v2_better_j": make_res("v2_cand", valid=True, h=2, j=92.5, area_gap=15.0),
        "v2_worse_j": make_res("v2_cand", valid=True, h=2, j=108.0, area_gap=15.0),
        "v2_better_h": make_res("v2_cand", valid=True, h=1, j=120.0, area_gap=25.0),
        "v2_worse_h": make_res("v2_cand", valid=True, h=3, j=80.0, area_gap=10.0),
        "v2_tie_better_gap": make_res("v2_cand", valid=True, h=2, j=100.0, area_gap=8.0),
        "v2_tie_worse_gap": make_res("v2_cand", valid=True, h=2, j=100.0, area_gap=22.0),
        "v2_exact_tie": make_res("v2_cand", valid=True, h=2, j=100.0, area_gap=15.0),
        "v2_invalid_wall": make_res(
            "v2_cand", valid=False, h=1, j=50.0, area_gap=0.0,
            violations=["Rule 4 (Wall / Obstacle Collision): Segment crosses exterior boundary wall"],
        ),
        "v2_invalid_ring": make_res(
            "v2_cand", valid=False, h=1, j=50.0, area_gap=0.0,
            violations=["Rule 1 (Closed Ring Topology): Ring polyline open (gap 0.045m > 0.001m)"],
        ),
        "v2_invalid_partition": make_res(
            "v2_cand", valid=False, h=2, j=60.0, area_gap=0.0,
            violations=["Rule 2 (Zone Area & Capacity): Zone 1 area 114.2 m^2 exceeds 100 m^2 limit"],
        ),
        "v2_invalid_door": make_res(
            "v2_cand", valid=False, h=2, j=70.0, area_gap=0.0,
            violations=["Rule 3 (Door Clearance & Retrace): Retrace polyline does not match entry path"],
        ),
        "v2_invalid_clearance": make_res(
            "v2_cand", valid=False, h=2, j=75.0, area_gap=0.0,
            violations=["Rule 6 (Hub Clearance): Hub H1 clearance 0.32m < 0.50m"],
        ),
    }


# =========================================================================
# TEST SUITE 1: FORMAL VERIFICATION OF LEXICOGRAPHICAL HIERARCHY
# =========================================================================

class TestLexicographicalHierarchy:
    """Formally audit the 4-level lexicographical comparison decision engine."""

    def test_level1_feasibility_dominates_all(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 1: Feasibility (100%) strictly dominates H, J, and Delta A."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_invalid_wall"]

        # Candidate has H=1 < H=2, J=50 < J=100, but is invalid
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is False
        assert reason == FallbackReason.WALL_COLLISION
        assert audit["candidate_valid"] is False
        assert audit["baseline_valid"] is True

    def test_level2_hub_minimization_dominates_routing_cost(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 2: Minimum Hub Count (H*) strictly dominates routing cost J."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_better_h"]

        # Candidate has H=1 < H=2, but J=120 > J=100
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is True
        assert reason == FallbackReason.NONE
        assert audit["delta_h"] == 1  # Baseline H - Cand H = 2 - 1 = 1 (positive is improvement)

    def test_level2_hub_regression_triggers_fallback(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 2: Higher hub count triggers immediate fallback, even with lower J."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_worse_h"]

        # Candidate has lower cost (J=80 vs J=100), but higher hub count (H=3 vs H=2)
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is False
        assert reason == FallbackReason.HUB_COUNT_REGRESSION
        assert audit["delta_h"] == -1

    def test_level3_cost_reduction_when_equal_hubs(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 3: Lower routing cost J wins when hub count is identical."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_better_j"]

        # Same H=2, J=92.5 < J=100.0
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is True
        assert reason == FallbackReason.NONE
        assert audit["delta_j"] == 7.5

    def test_level3_cost_regression_triggers_fallback(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 3: Higher routing cost J triggers immediate fallback when H is equal."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_worse_j"]

        # Same H=2, J=108.0 > J=100.0
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is False
        assert reason == FallbackReason.COST_REGRESSION
        assert audit["delta_j"] == -8.0

    def test_level4_area_gap_tie_breaking(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 4: When H and J are equal, smaller Area Gap (Delta A) wins."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_tie_better_gap"]

        # Same H=2, same J=100.0, Delta A=8.0 < Delta A=15.0
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is True
        assert reason == FallbackReason.NONE

    def test_level4_area_gap_regression_triggers_fallback(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Level 4: When H and J are equal, larger Area Gap triggers fallback."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_tie_worse_gap"]

        # Same H=2, same J=100.0, Delta A=22.0 > Delta A=15.0
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is False
        assert reason == FallbackReason.AREA_GAP_REGRESSION

    def test_exact_tie_preserves_champion_baseline(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Exact tie across all 4 levels deterministically preserves the champion baseline."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_exact_tie"]

        # Identical H, J, AreaGap
        is_sup, reason, audit = evaluate_lexicographical_preference(cand, base)
        assert is_sup is False
        assert reason == FallbackReason.EXACT_TIE


# =========================================================================
# TEST SUITE 2: ADVERSARIAL EDGE CASES & FAULT INJECTION
# =========================================================================

class TestAdversarialEdgeCases:
    """Stress-test production fallback under simulated and synthetic adversarial failures."""

    def test_adversarial_wall_collision_fallback(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 1: v2 candidate incurs wall/obstacle collision."""
        corrupted_v2 = mock_optimizer_results["v2_invalid_wall"]

        # Mock candidate optimizer returning the corrupted solution
        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.return_value = corrupted_v2

        # Frozen v1 baseline returning valid baseline
        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        # Monotonicity & Validity Assertion
        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.WALL_COLLISION.value
        assert res.total_cost_J == valid_v1.total_cost_J

    def test_adversarial_open_ring_fallback(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 2: v2 candidate produces open/disconnected ring topology."""
        corrupted_v2 = mock_optimizer_results["v2_invalid_ring"]

        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.return_value = corrupted_v2

        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.OPEN_RING.value

    def test_adversarial_illegal_zone_partition_fallback(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 3: v2 candidate attempts illegal multi-zone partition."""
        corrupted_v2 = mock_optimizer_results["v2_invalid_partition"]

        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.return_value = corrupted_v2

        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.ILLEGAL_PARTITION.value

    def test_adversarial_cost_regression_interception(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 4: v2 produces higher routing cost J than baseline."""
        regr_v2 = mock_optimizer_results["v2_worse_j"]

        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.return_value = regr_v2

        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.COST_REGRESSION.value
        assert res.total_cost_J == valid_v1.total_cost_J
        assert res.total_cost_J < regr_v2.total_cost_J

    def test_adversarial_stagnation_and_timeout(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 5: v2 encounters search stagnation (25 stagnant iterations)."""
        stagnant_v2 = copy.deepcopy(mock_optimizer_results["v2_better_j"])
        stagnant_v2.diagnostic_info["stagnation_steps"] = 25
        stagnant_v2.diagnostic_info["incumbent_updates"] = 0

        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.return_value = stagnant_v2

        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ON_DEMAND,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.SEARCH_STAGNATION.value

    def test_adversarial_v2_crash_exception_recovery(
        self,
        canonical_plan_0: MetricFloorPlan,
        mock_optimizer_results: Dict[str, OptimizerResult],
    ) -> None:
        """Adversarial Case 6: v2 raises an unhandled RuntimeError."""
        mock_v2_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v2_opt.optimize.side_effect = RuntimeError("Adversarial memory fault in v2 operator 8")

        valid_v1 = mock_optimizer_results["v1_baseline"]
        mock_v1_opt = mock.MagicMock(spec=BaseFloorPlanOptimizer)
        mock_v1_opt.optimize.return_value = valid_v1

        guarded = ProductionFallbackOptimizer(
            candidate_opt=mock_v2_opt,
            fallback_opt=mock_v1_opt,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["fallback_triggered"] is True
        assert res.diagnostic_info["fallback_reason"] == FallbackReason.EXCEPTION.value
        assert "v2_exception" in res.diagnostic_info
        assert "Adversarial memory fault" in res.diagnostic_info["v2_exception"]


# =========================================================================
# TEST SUITE 3: ARBITRATION OVERHEAD MICROBENCHMARK
# =========================================================================

class TestArbitrationOverhead:
    """Benchmark the execution overhead of the fallback arbitration logic."""

    def test_arbitration_microsecond_latency(self, mock_optimizer_results: Dict[str, OptimizerResult]) -> None:
        """Arbitration decision overhead must be under 0.100 ms (<100 microseconds)."""
        base = mock_optimizer_results["v1_baseline"]
        cand = mock_optimizer_results["v2_better_j"]

        # Warm up
        for _ in range(50):
            evaluate_lexicographical_preference(cand, base)

        # Microbenchmark 1,000 evaluations
        n_iters = 1000
        t0 = time.perf_counter()
        for _ in range(n_iters):
            evaluate_lexicographical_preference(cand, base)
        total_time_s = time.perf_counter() - t0

        avg_latency_ms = (total_time_s / n_iters) * 1000.0
        assert avg_latency_ms < 0.100, f"Arbitration latency {avg_latency_ms:.4f} ms exceeds 0.100 ms budget"


# =========================================================================
# TEST SUITE 4: END-TO-END EXECUTION ON CANONICAL FLOOR PLANS
# =========================================================================

class TestProductionFallbackEndToEnd:
    """End-to-end integration tests using concrete v1 and v2 optimizers on real floor plans."""

    def test_end_to_end_guarded_execution_plan_0(self, canonical_plan_0: MetricFloorPlan) -> None:
        """Verify ProductionFallbackOptimizer on Plan 0 returns 100% valid solution."""
        guarded = ProductionFallbackOptimizer(
            name="ProductionGuarded_LinUCB_v2",
            v2_iterations=6,
            v1_iterations=6,
            mode=ProductionFallbackMode.ARBITRATED,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=5.0, random_seed=42)

        # 1. Oracle Verification
        assert res.is_valid is True
        report = FloorPlanChecker.evaluate(canonical_plan_0, res.bundle)
        assert report.is_valid is True

        # 2. Telemetry Verification
        diag = res.diagnostic_info
        assert "fallback_triggered" in diag
        assert "arbitration_overhead_ms" in diag
        assert diag["arbitration_overhead_ms"] < 5.0
        assert "v2_metrics" in diag
        assert "v1_metrics" in diag
        assert "lexicographical_audit" in diag

    def test_end_to_end_seeded_mode_plan_4(self, canonical_plan_4: MetricFloorPlan) -> None:
        """Verify SEEDED mode where v2 is initialized from v1 on Plan 4."""
        guarded = ProductionFallbackOptimizer(
            name="ProductionGuarded_Seeded",
            v2_iterations=5,
            v1_iterations=5,
            mode=ProductionFallbackMode.SEEDED,
        )

        res = guarded.optimize(canonical_plan_4, time_limit_seconds=6.0, random_seed=123)

        assert res.is_valid is True
        report = FloorPlanChecker.evaluate(canonical_plan_4, res.bundle)
        assert report.is_valid is True
        assert res.diagnostic_info["production_mode"] == ProductionFallbackMode.SEEDED.value

    def test_end_to_end_on_demand_mode_plan_0(self, canonical_plan_0: MetricFloorPlan) -> None:
        """Verify ON_DEMAND mode where v2 runs first and skips v1 if valid."""
        guarded = ProductionFallbackOptimizer(
            name="ProductionGuarded_OnDemand",
            v2_iterations=6,
            mode=ProductionFallbackMode.ON_DEMAND,
        )

        res = guarded.optimize(canonical_plan_0, time_limit_seconds=4.0, random_seed=42)

        assert res.is_valid is True
        assert res.diagnostic_info["production_mode"] == ProductionFallbackMode.ON_DEMAND.value
        # If v2 was valid, v1 should not have been executed, saving wall-clock time
        if not res.diagnostic_info["fallback_triggered"]:
            assert res.diagnostic_info["v1_runtime_s"] == 0.0
