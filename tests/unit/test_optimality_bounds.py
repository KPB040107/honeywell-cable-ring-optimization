"""Unit tests for Post-Champion Agent 2 Optimality Bounds & Exact Small-Plan Routing."""

import math
import pytest
from resplan_router.contracts import Point2D
from resplan_router.experimental.optimality_bounds import (
    ExactSmallPlanCPSATRouter,
    HananMetricClosure,
    HananMSTLowerBound,
    HeldKarpLagrangianBound,
    OneTreeLowerBound,
    OptimalityGapAnalyzer,
    RectilinearSteinerBounds,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository


@pytest.fixture(scope="session")
def dataset_repo():
    return DatasetRepository.get_instance()


def test_rectilinear_steiner_bounds_synthetic():
    """Verify HPWL, RMST, and Hwang RSMT bounds on synthetic coordinate geometry."""
    terminals = {
        "T0": Point2D(x=0.0, y=0.0),
        "T1": Point2D(x=10.0, y=0.0),
        "T2": Point2D(x=10.0, y=10.0),
        "T3": Point2D(x=0.0, y=10.0),
    }
    # HPWL for a 10x10 box: 2 * (10 + 10) = 40.0
    hpwl = RectilinearSteinerBounds.compute_hpwl_bound(terminals)
    assert math.isclose(hpwl, 40.0, abs_tol=1e-4)

    # Manhattan distances between all pairs
    len_matrix = {}
    for u, p1 in terminals.items():
        len_matrix[u] = {}
        for v, p2 in terminals.items():
            len_matrix[u][v] = abs(p1.x - p2.x) + abs(p1.y - p2.y)

    rmst = RectilinearSteinerBounds.compute_rmst_bound(terminals, len_matrix)
    # Spanning 4 corners of 10x10 square requires 3 edges of 10 = 30.0
    assert math.isclose(rmst, 30.0, abs_tol=1e-4)

    # Hwang RSMT bound: (2/3) * 30.0 = 20.0
    rsmt_hwang = RectilinearSteinerBounds.compute_hwang_rsmt_bound(rmst)
    assert math.isclose(rsmt_hwang, 20.0, abs_tol=1e-4)
    assert rsmt_hwang <= rmst <= hpwl


def test_mst_and_1tree_relaxations_synthetic():
    """Verify MST and 1-Tree relaxation inequalities."""
    terminals = {
        "H": Point2D(x=0.0, y=0.0),
        "A": Point2D(x=4.0, y=0.0),
        "B": Point2D(x=4.0, y=3.0),
        "C": Point2D(x=0.0, y=3.0),
    }
    # Complete metric distance matrix
    dist_matrix = {}
    for u, p1 in terminals.items():
        dist_matrix[u] = {}
        for v, p2 in terminals.items():
            dist_matrix[u][v] = math.hypot(p1.x - p2.x, p1.y - p2.y)

    mst_val = HananMSTLowerBound.compute(terminals, dist_matrix)
    one_tree_val = OneTreeLowerBound.compute(terminals, dist_matrix)
    hk_val = HeldKarpLagrangianBound.compute(terminals, dist_matrix)

    # Fundamental TSP inequality: MST <= 1-Tree <= Held-Karp <= TSP
    assert mst_val <= one_tree_val + 1e-4
    assert one_tree_val <= hk_val + 1e-4


def test_exact_small_plan_router_plan_15596(dataset_repo):
    """Verify that ExactSmallPlanCPSATRouter certifies global optimality on Plan 15596."""
    plan = dataset_repo.get_floor_plan(15596)
    router = ExactSmallPlanCPSATRouter(time_limit_s=5.0)
    res = router.solve(plan)

    assert res.is_optimal is True
    assert res.status == "OPTIMAL"
    assert res.total_cost_J > 0.0
    assert res.bundle is not None
    assert len(res.bundle.hub_rings) == 1
    assert res.runtime_s < 5.0

    # Ensure checker validates the bundle
    from resplan_router.phase6_output.checker import FloorPlanChecker
    report = FloorPlanChecker.evaluate(plan, res.bundle)
    assert report.is_valid is True
    assert math.isclose(report.total_cost_J, res.total_cost_J, abs_tol=1e-3)


def test_optimality_gap_hierarchy_plan_51(dataset_repo):
    """Verify strict lower bound hierarchy and valid gap on Plan 51."""
    plan = dataset_repo.get_floor_plan(51)
    analyzer = OptimalityGapAnalyzer()
    rep = analyzer.evaluate_plan(plan)

    # Strict theoretical ordering: Hwang RSMT <= RMST <= MST <= 1-Tree <= Held-Karp <= J_LB <= J_UB
    assert rep.rsmt_hwang_bound <= rep.rmst_len_bound + 1e-4
    assert rep.rmst_len_bound <= rep.mst_j_bound + 1e-4
    assert rep.mst_j_bound <= rep.one_tree_j_bound + 1e-4
    assert rep.one_tree_j_bound <= rep.held_karp_j_bound + 1e-4
    assert rep.proven_j_lb <= rep.champion_j_ub + 1e-4

    # Absolute and relative gap consistency
    assert rep.absolute_gap >= 0.0
    assert rep.relative_gap_pct >= 0.0
    assert math.isclose(rep.absolute_gap, rep.champion_j_ub - rep.proven_j_lb, abs_tol=1e-3)

    # Check exact router solution matches near-optimality
    if rep.exact_cpsat_j is not None:
        assert rep.exact_cpsat_j <= rep.champion_j_ub + 1e-2
