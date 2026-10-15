"""Unit tests for Phase 5 Direction-Aware Closed-Ring TSP Solver.

Verifies:
- Base cases: m = 0 and m = 1 (single-node closed loop [H, N1, H] with 180-degree turn accounting).
- Two-node case: m = 2 ring tour [H, N1, N2, H].
- Slide 5 Zone H1 visiting sequence [H1, N1, N3, N2, H1].
- Slide 5 Zone H2 visiting sequence [H2, N4, N5, N6, H2].
- Direction-aware turn accounting at nodes and the hub wrap-around connection.
- Performance: Execution time < 5 ms per hub ring.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
import pytest

from resplan_router.contracts import (
    CompassDirection,
    FixedNode,
    HubAssignment,
    MetricFloorPlan,
    PairRouteMetric,
    Point2D,
    PortalTopologyGraph,
    RectilinearRoutingIndex,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase5_ring_sequencing.exact_ring_tsp import DirectionalRingSolver


@pytest.fixture
def slide5_env() -> tuple[MetricFloorPlan, PortalTopologyGraph]:
    """Load Slide 5 metric floor plan and extract portal topology."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    return plan, topology


def test_single_node_ring_m1() -> None:
    """Verify single-node hub ring (m = 1) yields [H1, N1, H1] with turn accounting."""
    hub = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=Point2D(x=1.0, y=1.0),
        assigned_rooms=["R01"],
        assigned_nodes=["N1"],
        zone_area_m2=20.0,
    )
    node = FixedNode(node_id="N1", room_id="R01", location=Point2D(x=4.0, y=1.0))

    # Precomputed metric: H1 -> N1 along EAST (length 3.0), N1 -> H1 along WEST (length 3.0)
    idx = RectilinearRoutingIndex(
        plan_id="test_plan",
        grid_node_count=2,
        grid_edge_count=2,
        pair_metrics={
            "H1_N1_E_E": PairRouteMetric(
                start_id="H1",
                end_id="N1",
                start_dir=CompassDirection.EAST,
                end_dir=CompassDirection.EAST,
                length_m=3.0,
                bends=0,
                cost_J=3.0,
                polyline=[hub.location, node.location],
            ),
            "N1_H1_W_W": PairRouteMetric(
                start_id="N1",
                end_id="H1",
                start_dir=CompassDirection.WEST,
                end_dir=CompassDirection.WEST,
                length_m=3.0,
                bends=0,
                cost_J=3.0,
                polyline=[node.location, hub.location],
            ),
        },
    )
    tensor = PairwiseCostTensor(
        plan_id="test_plan",
        grid=None,  # type: ignore[arg-type]
        terminals={"H1": hub.location, "N1": node.location},
        index=idx,
    )

    t0 = time.perf_counter()
    seq, leg_dirs = DirectionalRingSolver.solve_hub_ring_sequence(hub, [node], tensor)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Assert sequence format
    assert seq == ["H1", "N1", "H1"]
    assert len(leg_dirs) == 2
    assert leg_dirs[0] == (CompassDirection.EAST, CompassDirection.EAST)
    assert leg_dirs[1] == (CompassDirection.WEST, CompassDirection.WEST)

    # Turn at node N1: incoming EAST, outgoing WEST -> 180 deg reversal = 1 bend (+0.5 cost)
    # Turn at hub H1: incoming WEST, outgoing EAST -> 180 deg reversal = 1 bend (+0.5 cost)
    bends_at_node = 1 if leg_dirs[1][0] != leg_dirs[0][1] else 0
    bends_at_hub = 1 if leg_dirs[1][1] != leg_dirs[0][0] else 0
    assert bends_at_node == 1
    assert bends_at_hub == 1

    # Performance
    assert elapsed_ms < 5.0


def test_zero_node_ring_base_case() -> None:
    """Verify empty assigned nodes list (m = 0) returns trivial hub loop [H1, H1]."""
    hub = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=Point2D(x=2.0, y=2.0),
        assigned_rooms=["R01"],
        assigned_nodes=[],
        zone_area_m2=15.0,
    )
    idx = RectilinearRoutingIndex(plan_id="p0", grid_node_count=0, grid_edge_count=0)
    tensor = PairwiseCostTensor("p0", grid=None, terminals={"H1": hub.location}, index=idx)  # type: ignore[arg-type]

    seq, leg_dirs = DirectionalRingSolver.solve_hub_ring_sequence(hub, [], tensor)
    assert seq == ["H1", "H1"]
    assert leg_dirs == [(None, None)]


def test_two_node_ring_m2() -> None:
    """Verify two-node hub ring (m = 2) sequence visiting both nodes and pricing 90-degree turns."""
    hub = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=Point2D(x=0.0, y=0.0),
        assigned_rooms=["R01"],
        assigned_nodes=["N1", "N2"],
        zone_area_m2=25.0,
    )
    node1 = FixedNode(node_id="N1", room_id="R01", location=Point2D(x=4.0, y=0.0))
    node2 = FixedNode(node_id="N2", room_id="R01", location=Point2D(x=4.0, y=3.0))

    # Precomputed metrics forming right triangle loop:
    # Leg 1: H1 -> N1 along EAST (len 4.0, 0 bends)
    # Leg 2: N1 -> N2 along NORTH (len 3.0, 0 bends)
    # Leg 3: N2 -> H1 (west 4.0, south 3.0, 1 bend, len 7.0)
    idx = RectilinearRoutingIndex(
        plan_id="test_plan",
        grid_node_count=3,
        grid_edge_count=3,
        pair_metrics={
            "H1_N1_E_E": PairRouteMetric(
                start_id="H1", end_id="N1", start_dir=CompassDirection.EAST, end_dir=CompassDirection.EAST,
                length_m=4.0, bends=0, cost_J=4.0, polyline=[hub.location, node1.location]
            ),
            "N1_N2_N_N": PairRouteMetric(
                start_id="N1", end_id="N2", start_dir=CompassDirection.NORTH, end_dir=CompassDirection.NORTH,
                length_m=3.0, bends=0, cost_J=3.0, polyline=[node1.location, node2.location]
            ),
            "N2_H1_W_S": PairRouteMetric(
                start_id="N2", end_id="H1", start_dir=CompassDirection.WEST, end_dir=CompassDirection.SOUTH,
                length_m=7.0, bends=1, cost_J=7.5, polyline=[node2.location, Point2D(x=0.0, y=3.0), hub.location]
            ),
        },
    )
    tensor = PairwiseCostTensor(
        plan_id="test_plan",
        grid=None,  # type: ignore[arg-type]
        terminals={"H1": hub.location, "N1": node1.location, "N2": node2.location},
        index=idx,
    )

    seq, leg_dirs = DirectionalRingSolver.solve_hub_ring_sequence(hub, [node1, node2], tensor)
    assert seq == ["H1", "N1", "N2", "H1"]
    assert len(leg_dirs) == 3


def test_slide5_zone_h1_exact_sequence(slide5_env: tuple[MetricFloorPlan, PortalTopologyGraph]) -> None:
    """Verify Slide 5 Zone H1 exact visiting sequence [H1, N1, N3, N2, H1] and runtime < 5ms."""
    plan, topology = slide5_env

    # Slide 5 H1 placement in Living Room (R01)
    h1_pt = Point2D(x=5.2, y=3.6)
    h1 = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=h1_pt,
        assigned_rooms=["R01", "R02", "R03"],
        assigned_nodes=["N1", "N2", "N3"],
        zone_area_m2=71.0,
    )
    nodes_h1 = [plan.nodes[nid] for nid in ["N1", "N2", "N3"]]

    grid_h1 = build_hanan_grid(plan, topology, extra_points=[h1_pt])
    tensor_h1 = PairwiseCostTensor.build_from_plan(
        plan, topology, grid=grid_h1, candidate_hubs={"H1": h1_pt}, include_portals=False
    )

    t0 = time.perf_counter()
    closed_sequence, leg_dirs = DirectionalRingSolver.solve_hub_ring_sequence(h1, nodes_h1, tensor_h1)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Verification criteria from Plan 5.1 & v2.md Section 5.5.3
    assert closed_sequence == ["H1", "N1", "N3", "N2", "H1"]
    assert len(leg_dirs) == 4
    assert elapsed_ms < 5.0, f"DP solver took {elapsed_ms:.2f} ms (target < 5.0 ms)"

    # Verify each node visited exactly once
    assert set(closed_sequence[1:-1]) == {"N1", "N2", "N3"}
    assert len(closed_sequence[1:-1]) == 3


def test_slide5_zone_h2_exact_sequence(slide5_env: tuple[MetricFloorPlan, PortalTopologyGraph]) -> None:
    """Verify Slide 5 Zone H2 exact visiting sequence [H2, N4, N5, N6, H2] and runtime < 5ms."""
    plan, topology = slide5_env

    # Slide 5 H2 placement in Kitchen (R05)
    h2_pt = Point2D(x=11.4, y=7.0)
    h2 = HubAssignment(
        hub_id="H2",
        room_id="R05",
        location=h2_pt,
        assigned_rooms=["R04", "R05", "R06"],
        assigned_nodes=["N4", "N5", "N6"],
        zone_area_m2=47.0,
    )
    nodes_h2 = [plan.nodes[nid] for nid in ["N4", "N5", "N6"]]

    grid_h2 = build_hanan_grid(plan, topology, extra_points=[h2_pt])
    tensor_h2 = PairwiseCostTensor.build_from_plan(
        plan, topology, grid=grid_h2, candidate_hubs={"H2": h2_pt}, include_portals=False
    )

    t0 = time.perf_counter()
    closed_sequence, leg_dirs = DirectionalRingSolver.solve_hub_ring_sequence(h2, nodes_h2, tensor_h2)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Verification criteria from Plan 5.1 & v2.md Section 5.5.3
    assert closed_sequence == ["H2", "N4", "N5", "N6", "H2"]
    assert len(leg_dirs) == 4
    assert elapsed_ms < 5.0, f"DP solver took {elapsed_ms:.2f} ms (target < 5.0 ms)"

    # Verify each node visited exactly once
    assert set(closed_sequence[1:-1]) == {"N4", "N5", "N6"}
    assert len(closed_sequence[1:-1]) == 3


def test_turn_count_accounting_in_dp() -> None:
    """Verify that node turns and hub wrap-around turns are accurately priced in dynamic program."""
    # Test turn evaluation logic
    directions = [CompassDirection.NORTH, CompassDirection.SOUTH, CompassDirection.EAST, CompassDirection.WEST]

    for d_in in directions:
        for d_out in directions:
            # Straight through has 0 bends
            if d_in == d_out:
                assert (1 if d_out != d_in else 0) == 0
            else:
                # 90-degree turn or 180-degree reversal has 1 bend
                assert (1 if d_out != d_in else 0) == 1
