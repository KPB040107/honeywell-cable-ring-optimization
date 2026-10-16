"""Unit tests for Phase 11 Yen's route pool and micro-routing cache."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase11_optimization.route_pool import YenRoutePool


def test_yen_route_pool_shortest_paths():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)

    grid = build_hanan_grid(plan, topology)
    nodes = list(plan.nodes.values())
    n1 = nodes[0]
    n2 = nodes[1]

    pool = YenRoutePool(grid=grid, plan=plan, default_k=3)
    routes = pool.get_k_shortest_routes(
        start_id=n1.node_id,
        end_id=n2.node_id,
        start_pt=n1.location,
        end_pt=n2.location,
        k=2,
    )

    assert len(routes) >= 1
    best_route = routes[0]
    assert best_route.length_m > 0
    assert len(best_route.polyline) >= 2

    # Verify monotonic length
    for i in range(len(routes) - 1):
        assert routes[i].length_m <= routes[i + 1].length_m + 1e-4
