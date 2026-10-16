"""Unit tests for Phase 11 zone ring candidate pool generation."""

import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase11_optimization.ring_pool import ZoneRingGenerator, CompleteRingCandidate


def test_zone_ring_generator():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)
    portals = extract_all_portals(plan)
    topology = MacroTopologyGraph.build_topology(plan, portals)

    generator = ZoneRingGenerator(plan=plan, topology=topology, beam_width=10, candidates_per_zone=2)

    # Generate for a single room
    r_first = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])[0]
    cands = generator.generate_zone_candidates(
        zone_rooms=frozenset([r_first]),
        zone_idx=1,
        existing_nodes=plan.nodes,
    )

    assert len(cands) >= 1
    c0 = cands[0]
    assert isinstance(c0, CompleteRingCandidate)
    assert c0.total_length_m > 0
    assert c0.total_cost_J > 0
    assert len(c0.route_segments) > 0
    assert c0.closed_sequence[0] == c0.hub_id
    assert c0.closed_sequence[-1] == c0.hub_id

    # Test conversion to contract CompletedHubRing
    hub_ring = c0.to_hub_ring()
    assert hub_ring.hub_id == c0.hub_id
    assert hub_ring.cost_J == round(c0.total_cost_J, 4)
