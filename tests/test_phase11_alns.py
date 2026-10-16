"""Unit tests for Phase 11 Adaptive Large Neighborhood Search (ALNS) optimizer."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase11_optimization.alns import ALNSConfig, ALNSOptimizer, rings_to_bundle_and_partition
from resplan_router.phase11_optimization.greedy_baseline import solve_greedy_baseline
from resplan_router.phase11_optimization.objective import evaluate_solution
from resplan_router.phase11_optimization.ring_pool import ZoneRingGenerator



def test_alns_preserves_validity_and_improves():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)

    greedy_res = solve_greedy_baseline(plan, topology)
    generator = ZoneRingGenerator(plan, topology)

    init_rings = []
    for idx, hub in enumerate(greedy_res.partition.hubs, 1):
        cands = generator.generate_zone_candidates(frozenset(hub.assigned_rooms), zone_idx=idx, existing_nodes=plan.nodes)
        if cands:
            init_rings.append(cands[0])

    assert len(init_rings) == greedy_res.h_count

    b0, p0 = rings_to_bundle_and_partition(plan, init_rings)
    c0, _ = evaluate_solution(plan, b0, p0, topology)

    cfg = ALNSConfig(
        optimization_time_limit_s=2.0,
        random_seed=42,
        initial_temperature=1.0,
        cooling_rate=0.99,
        restart_count=1,
    )
    optimizer = ALNSOptimizer(plan, topology, cfg)
    res = optimizer.optimize(initial_rings=init_rings)

    assert res.report.is_valid is True
    assert all(res.report.rules_passed.values())
    assert res.cost <= c0

