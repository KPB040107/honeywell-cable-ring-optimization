"""Unit tests for Modular ALNS Operators (Agent 2)."""

import pytest
import random
from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
    synthesize_rings_for_zones,
)
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase6_output.checker import FloorPlanChecker


@pytest.fixture(scope="module")
def sample_plan():
    repo = DatasetRepository()
    plan = repo.get_floor_plan("14433")
    assert plan is not None
    return plan


def test_alns_operators_initialization(sample_plan):
    """Test that PortfolioHybrid can initialize an ALNSState and operators run safely."""
    opt = PortfolioHybridOptimizer()
    init_res = opt.optimize(sample_plan, time_limit_seconds=2.0)
    assert init_res.is_valid
    assert init_res.bundle is not None
    assert init_res.partition is not None

    zones = [set(h.assigned_rooms) for h in init_res.partition.hubs]
    state = ALNSState(
        zones=zones,
        bundle=init_res.bundle,
        partition=init_res.partition,
        cost_J=init_res.total_cost_J,
        h_count=init_res.h_count,
        area_gap=init_res.area_gap_m2,
        is_valid=True,
    )

    portals = extract_all_portals(sample_plan)
    topology = MacroTopologyGraph.build_topology(sample_plan, portals)
    door_graph = build_door_connectivity_graph(sample_plan, topology)
    rng = random.Random(42)

    assert NUM_OPERATORS == 8

    # Test all 8 operators execute without crashing
    for op_idx in range(NUM_OPERATORS):
        new_state, applied, op_name = apply_alns_operator(
            operator_idx=op_idx,
            state=state,
            plan=sample_plan,
            topology=topology,
            door_graph=door_graph,
            rng=rng,
        )
        assert op_name == OPERATOR_NAMES[op_idx]
        assert isinstance(new_state, ALNSState)
        assert new_state.is_valid
        # Check that checker verifies the new state
        report = FloorPlanChecker.evaluate(sample_plan, new_state.bundle)
        assert report.is_valid
