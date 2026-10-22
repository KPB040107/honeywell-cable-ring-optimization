"""Integration tests for Phase 11 hybrid optimizer on benchmark plans."""

import json
from pathlib import Path
import pytest
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase11_optimization.phase11_solver import optimize_floor_plan


def test_phase11_disconnected_zones_target():
    fixture_path = Path("tests/fixtures/demo_disconnected_zones.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)

    res = optimize_floor_plan(plan=plan, topology=topology, time_limit_s=5.0)

    assert res.report.is_valid is True
    assert all(res.report.rules_passed.values())
    assert res.opt_H == 2
    # Benchmark target: J <= 15.975
    assert res.opt_J <= 15.975
    assert res.hub_optimality_status == "PROVEN OPTIMAL HUB COUNT"


def test_phase11_dataset_plan_14433():
    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(14433)

    res = optimize_floor_plan(plan=plan, time_limit_s=5.0)

    assert res.report.is_valid is True
    assert all(res.report.rules_passed.values())
    assert res.opt_H in (2, 4)
    assert res.hub_optimality_status == "PROVEN OPTIMAL HUB COUNT"
    assert res.opt_J <= res.greedy_J + 1e-4

    sb = res.as_dict()
    assert sb["is_valid"] is True
    assert sb["hub_optimality_status"] == "PROVEN OPTIMAL HUB COUNT"
    assert "greedy_metrics" in sb
    assert "optimized_metrics" in sb
