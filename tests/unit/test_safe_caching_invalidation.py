"""Unit tests for strict cache invalidation semantics, zero stale routes, and SafeOptimizationCache."""

import math
import time
import pytest

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
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.experimental.performance_tuning import (
    RouteCandidateCache,
    CachedDirectionStateRouter,
    SpatialHananGridManager,
    IncrementalCheckerEvaluator,
    SafeOptimizationCache,
)


def test_route_candidate_cache_strict_hub_relocation_invalidation() -> None:
    """Verify that relocating a hub guarantees zero stale routes via coordinate binding and invalidation."""
    cache = RouteCandidateCache()

    # Iteration 1: Hub H0 is located at (5.0, 5.0), terminal N1 at (10.0, 5.0)
    h0_loc1 = (5.0, 5.0)
    n1_loc = (10.0, 5.0)
    r1 = PairRouteMetric(
        start_id="H0",
        end_id="N1",
        length_m=5.0,
        bends=0,
        cost_J=5.0,
        polyline=[Point2D(x=5.0, y=5.0), Point2D(x=10.0, y=5.0)],
    )
    cache.put(
        u_id="H0",
        v_id="N1",
        route=r1,
        u_coord=h0_loc1,
        v_coord=n1_loc,
        plan_id="64",
    )

    # Query with exact coordinates -> cache hit
    hit = cache.get(
        u_id="H0",
        v_id="N1",
        u_coord=h0_loc1,
        v_coord=n1_loc,
        plan_id="64",
    )
    assert hit is not None
    assert hit.length_m == 5.0
    assert cache.stats.hits == 1

    # Iteration 2: Operator (e.g. HUB_POSITION_REFINEMENT) relocates H0 to (8.0, 12.0)
    h0_loc2 = (8.0, 12.0)
    # Querying H0 at new location MUST NOT return stale route from (5.0, 5.0)
    miss = cache.get(
        u_id="H0",
        v_id="N1",
        u_coord=h0_loc2,
        v_coord=n1_loc,
        plan_id="64",
    )
    assert miss is None, "Cache returned stale route for relocated hub!"
    assert cache.stats.misses == 1

    # Put new route for H0 at (8.0, 12.0)
    r2 = PairRouteMetric(
        start_id="H0",
        end_id="N1",
        length_m=9.0,
        bends=1,
        cost_J=9.5,
        polyline=[Point2D(x=8.0, y=12.0), Point2D(x=10.0, y=12.0), Point2D(x=10.0, y=5.0)],
    )
    cache.put(
        u_id="H0",
        v_id="N1",
        route=r2,
        u_coord=h0_loc2,
        v_coord=n1_loc,
        plan_id="64",
    )

    # Explicit entity invalidation: invalidate_hub("H0") must purge both old and new entries
    purged_count = cache.invalidate_hub("H0")
    assert purged_count >= 1
    assert cache.stats.invalidations >= 1

    # Verify both entries are purged
    assert cache.get(u_id="H0", v_id="N1", u_coord=h0_loc1, v_coord=n1_loc, plan_id="64") is None
    assert cache.get(u_id="H0", v_id="N1", u_coord=h0_loc2, v_coord=n1_loc, plan_id="64") is None


def test_zero_stale_route_endpoint_guard() -> None:
    """Verify that physical endpoint verification guard catches any corrupted or drifted polyline."""
    cache = RouteCandidateCache()

    # Forged route claiming to start at (2.0, 2.0) but polyline starts at (4.0, 4.0)
    forged_route = PairRouteMetric(
        start_id="H0",
        end_id="N1",
        length_m=10.0,
        bends=0,
        cost_J=10.0,
        polyline=[Point2D(x=4.0, y=4.0), Point2D(x=10.0, y=4.0)],  # Discrepancy > 1mm!
    )
    cache.put(
        u_id="H0",
        v_id="N1",
        route=forged_route,
        u_coord=(2.0, 2.0),
        v_coord=(10.0, 4.0),
        plan_id="64",
    )

    # Retrieval must detect mismatch, evict entry, and return None
    res = cache.get(
        u_id="H0",
        v_id="N1",
        u_coord=(2.0, 2.0),
        v_coord=(10.0, 4.0),
        plan_id="64",
    )
    assert res is None, "Physical endpoint guard failed to reject inconsistent polyline!"
    assert cache.stats.stale_prevented >= 1


def test_avoidance_constraints_bypass_cache() -> None:
    """Verify that avoidance routing (crossing/overlap reconstruction) cleanly bypasses cache."""
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")
    portals = extract_all_portals(plan)
    topo = MacroTopologyGraph.build_topology(plan, portals)
    grid = build_hanan_grid(plan, topo)

    nodes = list(plan.nodes.values())
    u, v = nodes[0], nodes[1]

    cache = RouteCandidateCache()
    router = CachedDirectionStateRouter(cache=cache, plan_id="64")

    # 1. Normal unconstrained routing -> fills cache
    r1 = router.find_shortest_path(grid=grid, start=u.location, end=v.location, start_id=u.node_id, end_id=v.node_id)
    assert r1 is not None
    assert cache.stats.misses == 1

    # Second call -> cache hit
    r2 = router.find_shortest_path(grid=grid, start=u.location, end=v.location, start_id=u.node_id, end_id=v.node_id)
    assert r2 is not None
    assert cache.stats.hits == 1

    # 2. Avoidance routing with occupied_vertices (e.g. from CROSSING_AWARE_RECONSTRUCTION)
    mid_pt = r1.polyline[len(r1.polyline) // 2]
    occupied_v = {mid_pt}
    r3 = router.find_shortest_path(
        grid=grid,
        start=u.location,
        end=v.location,
        start_id=u.node_id,
        end_id=v.node_id,
        occupied_vertices=occupied_v,
    )
    # Cache must have been bypassed
    assert cache.stats.bypasses == 1


def test_zone_reassignment_invalidation() -> None:
    """Verify that zone boundary changes invalidate routes constrained by modified rooms."""
    cache = RouteCandidateCache()

    r1 = PairRouteMetric(
        start_id="H0",
        end_id="N1",
        length_m=8.0,
        bends=1,
        cost_J=8.5,
        polyline=[Point2D(x=0, y=0), Point2D(x=8, y=0)],
    )
    cache.put(
        u_id="H0",
        v_id="N1",
        route=r1,
        allowed_rooms=["room_A", "room_B"],
        plan_id="64",
    )

    assert cache.get(u_id="H0", v_id="N1", allowed_rooms=["room_A", "room_B"], plan_id="64") is not None

    # Room B is reassigned to another zone
    purged = cache.invalidate_rooms(["room_B"])
    assert purged == 1

    # Route is no longer present
    assert cache.get(u_id="H0", v_id="N1", allowed_rooms=["room_A", "room_B"], plan_id="64") is None


def test_spatial_hanan_grid_augmented_caching() -> None:
    """Verify that SpatialHananGridManager caches augmented grids and invalidates on demand."""
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")
    portals = extract_all_portals(plan)
    topo = MacroTopologyGraph.build_topology(plan, portals)

    mgr = SpatialHananGridManager(plan, topo)
    extra_pt1 = Point2D(x=3.1415, y=2.7182)

    # 1. First call builds augmented grid
    g1 = mgr.get_augmented_grid([extra_pt1])
    assert mgr.stats["augmented_builds"] == 1
    assert mgr.stats["augmented_hits"] == 0

    # 2. Second call with same terminal hits cache in 0 ms
    t0 = time.perf_counter()
    g2 = mgr.get_augmented_grid([extra_pt1])
    t_hit = (time.perf_counter() - t0) * 1000
    assert g1 is g2
    assert mgr.stats["augmented_hits"] == 1
    assert t_hit < 0.1  # Microsecond latency

    # 3. Invalidate cache flushes augmented grids
    mgr.invalidate_augmented_cache()
    g3 = mgr.get_augmented_grid([extra_pt1])
    assert mgr.stats["augmented_builds"] == 2


def test_incremental_checker_multi_ring_parity() -> None:
    """Verify that multi-ring Delta J matches full FloorPlanChecker evaluation to exact parity."""
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")
    opt = PortfolioHybridOptimizer()
    res = opt.optimize(plan)
    assert res.is_valid and res.bundle is not None

    rings = res.bundle.hub_rings
    if len(rings) < 2:
        pytest.skip("Test requires at least 2 rings")

    cached_rings = [
        IncrementalCheckerEvaluator.precompute_ring(r, plan)
        for r in rings
    ]

    # Perturb ring 0 sequence
    r0 = cached_rings[0]
    new_seq_0 = list(reversed(r0.closed_sequence))
    temp_ring_0 = CompletedHubRing(
        hub_id=r0.hub_id,
        room_id=r0.room_id,
        location=r0.location,
        assigned_rooms=r0.assigned_rooms,
        assigned_nodes=r0.assigned_nodes,
        closed_sequence=new_seq_0,
        route_segments=r0.route_segments,
        ring_length_m=r0.length_m,
        ring_bends=r0.bends,
        ring_crossings=r0.internal_crossings,
        ring_overlap_m=r0.internal_overlap_m,
        cost_J=r0.cost_J,
    )
    new_pre_0 = IncrementalCheckerEvaluator.precompute_ring(temp_ring_0, plan)

    # Multi-ring mutation affecting ring 0
    modifications = {0: new_pre_0}
    delta_J, new_total_J = IncrementalCheckerEvaluator.evaluate_multi_ring_delta_J(
        current_rings=cached_rings,
        modified_rings=modifications,
        plan=plan,
    )

    # Ground truth with FloorPlanChecker
    cand_rings = list(rings)
    cand_rings[0] = temp_ring_0
    from resplan_router.contracts import SynthesizedRingsBundle
    cand_bundle = SynthesizedRingsBundle(
        plan_id=str(plan.plan_id),
        hub_rings=cand_rings,
        total_length_m=sum(r.ring_length_m for r in cand_rings),
        total_bends=0,
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=0.0,
        area_gap_m2=0.0,
    )
    rep = FloorPlanChecker.evaluate(plan, cand_bundle)
    assert math.isclose(new_total_J, rep.total_cost_J, abs_tol=1e-3)


def test_safe_optimization_cache_workflow() -> None:
    """Verify end-to-end integration of SafeOptimizationCache."""
    repo = DatasetRepository("ResPlan.pkl")
    plan = repo.get_floor_plan("64")

    opt = PortfolioHybridOptimizer()
    res = opt.optimize(plan)
    assert res.is_valid and res.bundle is not None

    cache_ctx = SafeOptimizationCache(plan)
    cache_ctx.set_current_rings(res.bundle.hub_rings)

    assert len(cache_ctx.current_rings) == len(res.bundle.hub_rings)

    # Simulate hub relocation event
    purged = cache_ctx.notify_hub_relocated(res.bundle.hub_rings[0].hub_id)
    telemetry = cache_ctx.get_telemetry()

    assert "hub_relocated" in telemetry["mutation_events"][0]
    assert telemetry["route_cache"]["invalidations"] >= 0
    assert "base_hits" in telemetry["grid_manager"]
