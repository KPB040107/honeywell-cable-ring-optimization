"""End-to-end challenge-rule audit on independent demo floor plans."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import Slide8Export
from resplan_router.phase6_output.pipeline import route_floor_plan

FIXTURES = Path(__file__).parent / "fixtures"
OUTPUTS = Path(__file__).parent / "demo_outputs"


def _solve(name: str):
    raw = json.loads((FIXTURES / f"{name}.json").read_text())
    output, report, _ = route_floor_plan(raw)
    plan = parse_floor_plan(raw)
    topology = MacroTopologyGraph.build_topology(
        plan, extract_all_portals(plan, raw.get("doors"), raw.get("front_door"))
    )
    return raw, plan, topology, output, report


@pytest.mark.parametrize(
    ("name", "expected_hubs", "expected_nodes"),
    [
        ("demo_three_rooms_window", 1, 3),
        ("demo_six_rooms_capacity", 2, 6),
        ("demo_disconnected_zones", 2, 3),
    ],
)
def test_demo_solves_and_respects_ppt_rules(name, expected_hubs, expected_nodes):
    raw, plan, topology, output, report = _solve(name)
    assert report.is_valid, report.violations
    assert all(report.rules_passed.values())
    assert FloorPlanChecker.evaluate(plan, output, topology=topology).is_valid
    assert output["plan_id"] == raw["plan_id"] and output["units"] == "m"
    assert plan.scale_units_per_m == pytest.approx(10.0)

    eligible = {rid for rid, room in plan.rooms.items() if room.is_eligible}
    assert len(plan.nodes) == expected_nodes
    assert len(output["hubs"]) == expected_hubs
    assert sorted(rid for h in output["hubs"] for rid in h["assigned_rooms"]) == sorted(eligible)
    assert sorted(nid for h in output["hubs"] for nid in h["assigned_nodes"]) == sorted(plan.nodes)

    # These examples certify their minimum H by area, node count, or a disconnected
    # door graph component. A J optimum requires a separate global proof.
    graph = nx.Graph()
    graph.add_nodes_from(eligible)
    for rid in eligible:
        for neighbor in topology.adjacency.get(rid, []):
            if neighbor in eligible:
                graph.add_edge(rid, neighbor)
    bound = max(
        math.ceil(sum(plan.rooms[r].area_m2 for r in eligible) / 100),
        math.ceil(len(plan.nodes) / 5),
        nx.number_connected_components(graph),
    )
    assert expected_hubs == bound

    independent_length = 0.0
    for hub in output["hubs"]:
        room_set = set(hub["assigned_rooms"])
        node_set = set(hub["assigned_nodes"])
        assert nx.is_connected(graph.subgraph(room_set))
        assert len(node_set) <= 5
        assert sum(plan.rooms[r].area_m2 for r in room_set) <= 100.001
        assert all(plan.nodes[n].room_id in room_set for n in node_set)
        hub_point = Point(hub["location"]["x"], hub["location"]["y"])
        assert plan.rooms[hub["room_id"]].geometry.contains(hub_point)
        assert plan.obstacles_geom.distance(hub_point) >= 0.499
        assert plan.inner_geom.boundary.distance(hub_point) >= 0.499

        seq = hub["closed_sequence"]
        assert seq[0] == seq[-1] == hub["hub_id"]
        assert sorted(seq[1:-1]) == sorted(node_set)
        assert len(hub["route_segments"]) == len(seq) - 1
        for index, leg in enumerate(hub["route_segments"]):
            assert (leg["from"], leg["to"]) == (seq[index], seq[index + 1])
            terminal = lambda tid: (
                (hub["location"]["x"], hub["location"]["y"])
                if tid == hub["hub_id"]
                else (plan.nodes[tid].location.x, plan.nodes[tid].location.y)
            )
            assert tuple(leg["polyline"][0]) == pytest.approx(terminal(leg["from"]), abs=0.001)
            assert tuple(leg["polyline"][-1]) == pytest.approx(terminal(leg["to"]), abs=0.001)
            assert len(leg["polyline"]) >= 2
            for a, b in zip(leg["polyline"], leg["polyline"][1:]):
                assert abs(a[0] - b[0]) <= 0.001 or abs(a[1] - b[1]) <= 0.001
                line = LineString([a, b])
                assert line.difference(plan.free_space_geom.buffer(0.001)).length <= 0.001
                independent_length += line.length

    assert report.total_length_m == pytest.approx(independent_length, abs=0.01)
    assert report.total_cost_J == pytest.approx(
        report.total_length_m + 5 * report.total_crossings
        + report.total_overlap_m + 0.5 * report.total_bends,
        abs=0.01,
    )


@pytest.fixture(scope="module")
def four_room_solution():
    return _solve("new_four_room_plan")


def test_checker_rejects_leg_labels_that_disagree_with_ring(four_room_solution):
    _, plan, topology, output, _ = four_room_solution
    corrupt = deepcopy(output)
    corrupt["hubs"][0]["route_segments"][0]["to"] = "N999"
    report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
    assert not report.rules_passed["Rule 4"]


def test_checker_rejects_cable_missing_a_fixed_node(four_room_solution):
    raw, _, topology, output, _ = four_room_solution
    changed_input = deepcopy(raw)
    changed_input["nodes"][0]["location"][0] += 1.0  # 0.1 m, still inside room
    changed_plan = parse_floor_plan(changed_input)
    report = FloorPlanChecker.evaluate(changed_plan, output, topology=topology)
    assert not report.rules_passed["Rule 4"]


def test_checker_rejects_node_assigned_to_a_different_rooms_hub():
    _, plan, topology, output, _ = _solve("demo_six_rooms_capacity")
    corrupt = deepcopy(output)
    a, b = corrupt["hubs"][:2]
    a["assigned_nodes"][0], b["assigned_nodes"][0] = (
        b["assigned_nodes"][0], a["assigned_nodes"][0]
    )
    report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
    assert not report.rules_passed["Rule 1"]


def test_parser_rejects_two_fixed_nodes_in_one_eligible_room():
    raw = json.loads((FIXTURES / "demo_three_rooms_window.json").read_text())
    raw["nodes"].append({"node_id": "N_extra", "room_id": "R01", "location": [30, 20]})
    with pytest.raises(ValueError, match="more than one fixed node"):
        parse_floor_plan(raw)


def test_parser_rejects_duplicate_fixed_node_ids():
    raw = json.loads((FIXTURES / "demo_three_rooms_window.json").read_text())
    raw["nodes"][1]["node_id"] = raw["nodes"][0]["node_id"]
    with pytest.raises(ValueError, match="Duplicate fixed node ID"):
        parse_floor_plan(raw)


def test_cheaper_disconnected_route_is_valid_comparison_point():
    _, plan, topology, _, _ = _solve("demo_disconnected_zones")
    alternate = json.loads(
        (OUTPUTS / "demo_disconnected_cheaper_valid.json").read_text()
    )
    Slide8Export.model_validate(alternate)
    report = FloorPlanChecker.evaluate(plan, alternate, topology=topology)
    assert report.is_valid, report.violations
    assert report.hub_count == 2
    assert report.total_cost_J == pytest.approx(15.975, abs=0.001)
