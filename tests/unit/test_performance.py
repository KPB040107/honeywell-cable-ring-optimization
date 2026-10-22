"""Unit tests for Performance Profiler and Multi-Worker Scaling (Agent 4).

Validates:
1. Host CPU and GPU telemetry metric collection.
2. SQLite BaselineCache WAL mode verification and latency measurement.
3. Micro-benchmark profiling of the 5 algorithmic components:
   - Hanan grid generation
   - A* rectilinear path search
   - Held-Karp TSP tour solver
   - FloorPlanChecker constraint validation
   - Observation vector extraction
4. Memory footprint and garbage collection behavior.
5. Multi-process worker scaling execution without race conditions.
6. Integrity and schema of generated JSON profiling reports.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from benchmarks.profiling.profiler import (
    PipelineProfiler,
    query_cpu_telemetry,
    query_gpu_telemetry,
    run_full_pipeline_profiling,
)
from benchmarks.profiling.worker_scaling import (
    _worker_process_task,
    benchmark_worker_scaling,
)
from resplan_router.optimizers.alns_operators import ALNSState, NUM_OPERATORS
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from rl.baseline_cache import BaselineCache
from rl.observations import OBSERVATION_DIM


@pytest.fixture(scope="module")
def dataset_repo():
    return DatasetRepository.get_instance()


@pytest.fixture(scope="module")
def baseline_cache():
    return BaselineCache.get_instance()


@pytest.fixture(scope="module")
def canonical_plan_and_baseline(dataset_repo, baseline_cache):
    plan = dataset_repo.get_floor_plan(14433)
    is_valid, base_res, _ = baseline_cache.get(14433)
    assert is_valid is True
    assert base_res is not None
    return plan, base_res


def test_cpu_and_gpu_telemetry():
    """Verify hardware telemetry correctly extracts CPU, RAM, and GPU stats."""
    cpu_meta = query_cpu_telemetry()
    assert cpu_meta["cpu_count_physical"] is not None and cpu_meta["cpu_count_physical"] > 0
    assert cpu_meta["cpu_count_logical"] >= cpu_meta["cpu_count_physical"]
    assert cpu_meta["system_ram_total_gb"] > 0
    assert cpu_meta["process_rss_mb"] > 0
    assert isinstance(cpu_meta["per_core_cpu_percent"], list)
    assert len(cpu_meta["per_core_cpu_percent"]) == cpu_meta["cpu_count_logical"]

    gpu_meta = query_gpu_telemetry()
    assert "available" in gpu_meta
    if gpu_meta["available"]:
        assert gpu_meta["total_memory_mb"] > 0
        assert "GeForce" in gpu_meta["device_name"] or "RTX" in gpu_meta["device_name"]


def test_sqlite_cache_wal_and_metrics(baseline_cache):
    """Verify BaselineCache runs in WAL mode with low latency."""
    profiler = PipelineProfiler()
    cache_meta = profiler.profile_sqlite_cache([14433], n_reads=20)

    assert cache_meta["wal_enabled"] is True
    assert cache_meta["journal_mode"] == "WAL"
    assert cache_meta["busy_timeout_ms"] == 30000
    assert cache_meta["read_latency_ms"]["mean"] > 0.0
    assert cache_meta["read_latency_ms"]["mean"] < 50.0  # Under 50ms read latency
    assert cache_meta["write_latency_ms"]["mean"] > 0.0
    assert cache_meta["throughput_reads_per_sec"] > 10.0


def test_hanan_grid_profiler(canonical_plan_and_baseline):
    """Verify Hanan grid profiler executes, collects timings, and dumps cProfile stats."""
    plan, _ = canonical_plan_and_baseline
    profiler = PipelineProfiler()
    res = profiler.profile_hanan_grid(plan, n_iters=3)

    assert res["component"] == "Hanan grid generation"
    assert res["grid_vertices"] > 0
    assert res["grid_edges"] > 0
    assert res["latency_ms"]["mean"] > 0.0
    assert Path(res["profile_dump"]).exists()
    assert len(res["top_call_breakdown"]) > 0


def test_astar_search_profiler(canonical_plan_and_baseline):
    """Verify A* search profiler executes and returns valid metrics."""
    plan, _ = canonical_plan_and_baseline
    profiler = PipelineProfiler()
    res = profiler.profile_astar_search(plan, n_iters=3)

    assert res["component"] == "A* rectilinear path search"
    assert res["latency_ms"]["mean"] > 0.0
    assert Path(res["profile_dump"]).exists()
    assert len(res["top_call_breakdown"]) > 0


def test_held_karp_tsp_profiler(canonical_plan_and_baseline):
    """Verify Held-Karp TSP profiler executes with sub-millisecond latency."""
    plan, _ = canonical_plan_and_baseline
    profiler = PipelineProfiler()
    res = profiler.profile_held_karp_tsp(plan, n_iters=10)

    assert res["component"] == "Held-Karp TSP tour solver"
    assert res["terminal_count"] == len(plan.nodes)
    assert res["latency_ms"]["mean"] > 0.0
    assert res["latency_ms"]["mean"] < 5.0  # Held-Karp for M<=6 is very fast
    assert Path(res["profile_dump"]).exists()


def test_floorplan_checker_profiler(canonical_plan_and_baseline):
    """Verify FloorPlanChecker profiler validates candidate solution bundle."""
    plan, base_res = canonical_plan_and_baseline
    profiler = PipelineProfiler()
    res = profiler.profile_floorplan_checker(plan, base_res.bundle, n_iters=3)

    assert res["component"] == "FloorPlanChecker constraint validation"
    assert res["latency_ms"]["mean"] > 0.0
    assert Path(res["profile_dump"]).exists()


def test_observation_vector_profiler(canonical_plan_and_baseline):
    """Verify observation extraction profiler outputs 24-dim telemetry."""
    plan, base_res = canonical_plan_and_baseline
    profiler = PipelineProfiler()
    state = ALNSState(
        zones=[set(h.assigned_rooms) for h in base_res.partition.hubs],
        bundle=base_res.bundle,
        partition=base_res.partition,
        cost_J=base_res.total_cost_J,
        h_count=base_res.h_count,
        area_gap=base_res.area_gap_m2,
        is_valid=True,
    )
    res = profiler.profile_observation_extraction(plan, state, n_iters=15)

    assert res["component"] == "Observation vector extraction"
    assert res["observation_dim"] == OBSERVATION_DIM
    assert res["latency_ms"]["mean"] < 5.0  # Observation extraction must be fast
    assert Path(res["profile_dump"]).exists()


def test_memory_and_gc_profiler():
    """Verify memory and GC profiling measures RSS and cycle collection."""
    profiler = PipelineProfiler()
    mem_res = profiler.profile_memory_and_gc(plan_id=14433, num_steps=10)

    assert mem_res["steps_profiled"] == 10
    assert mem_res["rss_start_mb"] > 0.0
    assert mem_res["rss_mid_mb"] > 0.0
    assert mem_res["tracemalloc_peak_mb"] > 0.0
    assert mem_res["has_memory_leak"] is False


def test_profiler_json_report_structure():
    """Verify full profiler report structure on disk matches spec."""
    report_file = Path("results/profiling_breakdown.json")
    assert report_file.exists(), "Profiler report must exist on disk"

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "host_hardware" in data
    assert "cpu" in data["host_hardware"]
    assert "gpu" in data["host_hardware"]
    assert "sqlite_cache_metrics" in data
    assert "component_breakdowns" in data
    assert "memory_and_gc_profile" in data
    assert "bottleneck_mitigations" in data


def test_isolated_worker_process_task():
    """Verify isolated worker process task steps environment cleanly."""
    report = _worker_process_task(
        worker_id=0,
        plan_ids=[14433],
        steps_per_env=4,
        seed=42,
    )
    assert report["worker_id"] == 0
    assert report["steps_completed"] == 4
    assert report["total_time_s"] > 0.0
    assert len(report["step_latencies_ms"]) == 4
    assert report["rss_mb"] > 0.0


def test_worker_scaling_benchmark_execution():
    """Verify multi-process worker scaling runs without deadlocks or race conditions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_report = Path(tmp_dir) / "test_scaling.json"
        res = benchmark_worker_scaling(
            worker_counts=[1, 2],
            steps_per_worker=4,
            plans=[14433, 51],
            output_path=tmp_report,
        )

        assert "1_workers" in res["scaling_configurations"]
        assert "2_workers" in res["scaling_configurations"]
        w1 = res["scaling_configurations"]["1_workers"]
        w2 = res["scaling_configurations"]["2_workers"]

        assert w1["total_steps"] == 4
        assert w2["total_steps"] == 8
        assert w1["actual_sps"] > 0.0
        assert w2["actual_sps"] > 0.0
        assert w2["speedup_factor"] > 0.5
        assert tmp_report.exists()
