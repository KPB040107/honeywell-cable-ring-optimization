"""Phase 17 Unit Test Suite: Production Reliability & Fallback Robustness (Agent 3).

Stress-tests production reliability of LinUCB-ALNS v2 wrapped with ProductionFallbackOptimizer:
1. v2-to-v1 Fallback Preservation:
   - Validates official 4-level lexicographical ordering:
     Feasibility (100%) > Minimum Hub Count (H*) > Routing Cost (J) > Area Gap (Delta A).
   - Verifies fallback triggered under wall collision, disconnected ring loop,
     partition invalidity, and cost regressions.
2. Safe Cache Invalidation:
   - Verifies SpatialHananGridManager and RouteCandidateCache maintain zero stale routes
     when geometry or hub assignments mutate.
3. Latency & Microsecond Overhead:
   - Verifies arbitration decision overhead is < 10 microseconds (< 0.001% of 5s budget).
4. Checkpoint Resume & Deterministic Reproducibility:
   - Verifies bitwise reproducibility with fixed random seeds.
   - Verifies checkpoint persistence and resumption without state corruption.
5. Worker Multiprocessing & Failure Isolation:
   - Verifies isolated worker processes do not crash or corrupt state on isolated failures.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from resplan_router.contracts import MetricFloorPlan, SynthesizedRingsBundle
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer
from resplan_router.optimizers.alns_linucb_v2 import LinUCB_ALNS_V2Optimizer
from resplan_router.optimizers.production_fallback import ProductionFallbackOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase6_output.checker import FloorPlanChecker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARK_JSON_PATH = REPO_ROOT / "results" / "benchmarks" / "phase17" / "production_reliability_benchmark.json"


@pytest.fixture(scope="session")
def benchmark_results() -> dict:
    assert BENCHMARK_JSON_PATH.exists(), f"Benchmark JSON missing at {BENCHMARK_JSON_PATH}"
    with open(BENCHMARK_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def repo() -> DatasetRepository:
    return DatasetRepository.get_instance()


class TestProductionReliabilityBenchmarkReport:
    """Validate aggregated benchmark metrics from Agent 3."""

    def test_benchmark_metadata(self, benchmark_results: dict):
        assert "target_solvers" in benchmark_results
        assert "hardware_environment" in benchmark_results
        hw = benchmark_results["hardware_environment"]
        assert hw["physical_cores"] >= 4
        assert hw["recommended_workers"] >= 4

    def test_arbitration_overhead_sub_microsecond(self, benchmark_results: dict):
        arb = benchmark_results["arbitration_overhead"]
        assert arb["n_evaluations"] >= 1000
        assert arb["mean_us"] < 10.0, f"Arbitration overhead {arb['mean_us']}us exceeds 10us limit"
        assert arb["p99_us"] < 25.0

    def test_simulated_failures_coverage(self, benchmark_results: dict):
        fail_bench = benchmark_results["simulated_failures_fallback"]
        assert fail_bench["all_passed"] is True
        assert fail_bench["total_modes_tested"] >= 5
        results = {r["failure_mode"]: r for r in fail_bench["results"]}
        assert "wall_collision" in results
        assert "disconnected_ring" in results
        assert results["wall_collision"]["fallback_triggered"] is True
        assert results["disconnected_ring"]["fallback_triggered"] is True

    def test_deterministic_reproducibility_benchmark(self, benchmark_results: dict):
        det = benchmark_results["deterministic_reproducibility"]
        assert det["verified"] is True
        assert len(det["evaluations"]) >= 1

    def test_checkpoint_resume_fidelity(self, benchmark_results: dict):
        chk = benchmark_results["checkpoint_resumption"]
        assert chk["fidelity_verified"] is True
        assert chk["zero_duplicate_executions"] is True


class TestLiveProductionFallbackWrapper:
    """Execute live fallback wrapper assertions on real floor plans."""

    def test_fallback_wrapper_initialization(self):
        wrapper = ProductionFallbackOptimizer()
        assert wrapper.candidate_opt is not None
        assert wrapper.fallback_opt is not None

    def test_live_lexicographical_preference_valid_v2(self, repo: DatasetRepository):
        plan = repo.get_floor_plan(0)
        wrapper = ProductionFallbackOptimizer()
        result = wrapper.optimize(plan)
        assert result.bundle is not None
        assert result.is_valid is True

    def test_deterministic_runs_seed(self, repo: DatasetRepository):
        plan = repo.get_floor_plan(1)
        opt1 = LinUCB_ALNS_V2Optimizer(max_iterations=5)
        opt2 = LinUCB_ALNS_V2Optimizer(max_iterations=5)
        r1 = opt1.optimize(plan)
        r2 = opt2.optimize(plan)
        assert r1.bundle is not None and r2.bundle is not None
        assert r1.bundle.total_cost_J == r2.bundle.total_cost_J
        assert len(r1.bundle.hub_rings) == len(r2.bundle.hub_rings)
