"""Unit tests for experimental performance tuning module."""

import math
import time
import pytest
from shapely.geometry import Point as ShapelyPoint

from resplan_router.contracts import (
    CompassDirection,
    PairRouteMetric,
    Point2D,
    RouteSegment,
    CompletedHubRing,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.experimental.performance_tuning import (
    RouteCandidateCache,
    CachedDirectionStateRouter,
    SpatialHananGridManager,
    IncrementalCheckerEvaluator,
    detect_optimal_hardware_affinity,
)


def test_route_candidate_cache_lru() -> None:
    cache = RouteCandidateCache(maxsize=3)
    r1 = PairRouteMetric(start_id="N1", end_id="N2", length_m=10.0, bends=2, cost_J=11.0, polyline=[Point2D(x=0, y=0), Point2D(x=10, y=0)])
    r2 = PairRouteMetric(start_id="N2", end_id="N3", length_m=15.0, bends=3, cost_J=16.5, polyline=[Point2D(x=0, y=0), Point2D(x=15, y=0)])
    r3 = PairRouteMetric(start_id="N3", end_id="N4", length_m=20.0, bends=4, cost_J=22.0, polyline=[Point2D(x=0, y=0), Point2D(x=20, y=0)])
    r4 = PairRouteMetric(start_id="N4", end_id="N5", length_m=25.0, bends=5, cost_J=27.5, polyline=[Point2D(x=0, y=0), Point2D(x=25, y=0)])

    cache.put("N1", "N2", r1)
    cache.put("N2", "N3", r2)
    cache.put("N3", "N4", r3)

    assert len(cache) == 3
    assert cache.get("N1", "N2") is not None
    assert cache.stats.hits == 1

    # Adding 4th item should evict N2->N3 (since N1->N2 was touched last)
    cache.put("N4", "N5", r4)
    assert len(cache) == 3
    assert cache.get("N2", "N3") is None
    assert cache.get("N1", "N2") is not None
    assert cache.stats.evictions == 1


def test_spatial_hanan_grid_manager() -> None:
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")
    portals = extract_all_portals(plan)
    topo = MacroTopologyGraph.build_topology(plan, portals)

    mgr = SpatialHananGridManager(plan, topo)
    t0 = time.perf_counter()
    g1 = mgr.get_base_grid()
    t_first = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    g2 = mgr.get_base_grid()
    t_second = (time.perf_counter() - t1) * 1000

    # Second call should be practically instantaneous (cached pointer)
    assert g1 is g2
    assert t_second < t_first
    assert len(g1._vertices) > 100
    assert len(g1._edges) > 100


def test_incremental_checker_evaluation_parity() -> None:
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")
    opt = PortfolioHybridOptimizer()
    res = opt.optimize(plan)
    assert res.is_valid and res.bundle is not None

    # Full evaluation
    rep = FloorPlanChecker.evaluate(plan, res.bundle)

    # Precompute ring data
    cached_rings = [
        IncrementalCheckerEvaluator.precompute_ring(r, plan)
        for r in res.bundle.hub_rings
    ]

    tot_L, tot_X, tot_O, tot_B, tot_J, area_gap = (
        IncrementalCheckerEvaluator.evaluate_bundle_from_cached_rings(cached_rings, plan)
    )

    # Verify exact parity with official FloorPlanChecker
    assert math.isclose(tot_L, rep.total_length_m, abs_tol=1e-3)
    assert tot_B == rep.total_bends
    assert tot_X == rep.total_crossings
    assert math.isclose(tot_O, rep.total_overlap_m, abs_tol=1e-3)
    assert math.isclose(tot_J, rep.total_cost_J, abs_tol=1e-3)
    assert math.isclose(area_gap, rep.area_gap_m2, abs_tol=1e-3)


def test_hardware_affinity_detection() -> None:
    hw = detect_optimal_hardware_affinity()
    assert hw.cpu_physical_cores >= 1
    assert hw.cpu_logical_cores >= hw.cpu_physical_cores
    assert hw.system_memory_total_gb > 0
    assert hw.recommended_worker_count >= 1
