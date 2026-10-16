"""Unit tests for Phase 11 global CP-SAT ring selector and conflict matrix."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase11_optimization.conflict_matrix import PairwiseConflictMatrix
from resplan_router.phase11_optimization.global_selector import GlobalRingSelector
from resplan_router.phase11_optimization.lower_bounds import prove_minimum_hubs
from resplan_router.phase11_optimization.ring_pool import ZoneRingGenerator


def test_global_ring_selector():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)

    _, _, optimal_partition = prove_minimum_hubs(plan, topology)
    generator = ZoneRingGenerator(plan, topology, candidates_per_zone=2)

    candidates = []
    for idx, z_rooms in enumerate(optimal_partition, 1):
        cands = generator.generate_zone_candidates(z_rooms, zone_idx=idx, existing_nodes=plan.nodes)
        candidates.extend(cands)

    matrix = PairwiseConflictMatrix()
    matrix.precompute(candidates)

    selector = GlobalRingSelector(plan, candidates, matrix, time_limit_s=2.0)
    res = selector.solve()

    assert res.solver_status in ("OPTIMAL", "FEASIBLE")
    assert len(res.selected_rings) == len(optimal_partition)
    assert res.h_count == len(optimal_partition)
    assert res.j_score > 0

