"""Phase 4: Lexicographic Hub Placement & Contiguous Zone Partitioning Verification Suite.

Implements official verification gates from v2.md Section 5.4.3:
- Test 4.1 (test_slide5_6room_exact_partition):
  Assert hub_count == 2 (matching H_min = 2), Zone H1 = {Living, Bedroom 1, Bedroom 3} (71 m^2, 3 nodes),
  Zone H2 = {Bedroom 2, Kitchen, Storage} (47 m^2, 3 nodes).
- Test 4.2 (test_rule6_hub_05m_wall_clearance):
  Assert host_room.contains(H_k) is True, host_room is eligible and assigned to H_k,
  and distance(H_k, obstacles) >= 0.50m - 0.001m for every hub.
- Test 4.3 (test_area_gap_tie_breaker):
  On a symmetric 4-room plan where two partitions have equal routing cost J = 30.0,
  one with (80 m^2, 20 m^2) and one with (50 m^2, 50 m^2), assert the solver selects
  the balanced (50, 50) partition (delta A_gap = 0).
- Test 4.4 (test_phase1_to_phase4_regression):
  Comprehensive end-to-end regression across Phase 1, Phase 2, Phase 3, and Phase 4.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import shapely.geometry as sg

from resplan_router.contracts import (
    EligibleOrExcludedRoom,
    FixedNode,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    RoomCategory,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase4_partitioning.hub_candidates import generate_all_room_hub_candidates
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner


@pytest.fixture
def slide5_fixture() -> tuple[
    MetricFloorPlan,
    PortalTopologyGraph,
    PairwiseCostTensor,
    dict[str, list[Point2D]],
]:
    """Fixture providing Slide 5 plan, topology, precomputed tensor, and candidate hubs."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    grid = build_hanan_grid(plan, topology)
    tensor = PairwiseCostTensor.build_from_plan(
        plan, topology, grid=grid, include_portals=False
    )
    candidates = generate_all_room_hub_candidates(plan, topology=topology)
    return plan, topology, tensor, candidates


def test_slide5_6room_exact_partition(
    slide5_fixture: tuple[
        MetricFloorPlan,
        PortalTopologyGraph,
        PairwiseCostTensor,
        dict[str, list[Point2D]],
    ],
) -> None:
    """Test 4.1: Verify exact contiguous 2-hub partition on Slide 5 schematic.

    Slide 5 specification (118 m^2, 6 nodes):
    - Lower bound H_min = max(ceil(118/100), ceil(6/5)) = 2.
    - Zone H1: {Living, Bedroom 1, Bedroom 3} (71 m^2, 3 nodes).
    - Zone H2: {Bedroom 2, Kitchen, Storage} (47 m^2, 3 nodes).
    """
    plan, topology, tensor, candidates = slide5_fixture

    partition = ZonePartitioner.solve_partition(
        plan=plan,
        topology=topology,
        cost_tensor=tensor,
        candidates=candidates,
    )

    # 1. Hub count equals lower bound H_min = 2
    assert partition.hub_count == 2
    assert partition.lower_bound_hubs == 2
    assert len(partition.hubs) == 2

    # Map room ID -> room name from Slide 5 schema
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
    room_names = {r["room_id"]: r.get("name", r["room_id"]) for r in raw_data["rooms"]}

    h1 = next(h for h in partition.hubs if h.hub_id == "H1")
    h2 = next(h for h in partition.hubs if h.hub_id == "H2")

    # 2. Zone H1 room set and capacities
    assert set(h1.assigned_rooms) == {"R01", "R02", "R03"}
    h1_names = {room_names[r] for r in h1.assigned_rooms}
    assert h1_names == {"Living", "Bedroom 1", "Bedroom 3"}
    assert h1.zone_area_m2 == pytest.approx(71.0)
    assert len(h1.assigned_nodes) == 3
    assert set(h1.assigned_nodes) == {"N1", "N2", "N3"}

    # 3. Zone H2 room set and capacities
    assert set(h2.assigned_rooms) == {"R04", "R05", "R06"}
    h2_names = {room_names[r] for r in h2.assigned_rooms}
    assert h2_names == {"Bedroom 2", "Kitchen", "Storage"}
    assert h2.zone_area_m2 == pytest.approx(47.0)
    assert len(h2.assigned_nodes) == 3
    assert set(h2.assigned_nodes) == {"N4", "N5", "N6"}

    # 4. Area gap calculation
    expected_gap = 71.0 - 47.0  # 24.0 m^2
    assert partition.area_gap_m2 == pytest.approx(expected_gap)


def test_rule6_hub_05m_wall_clearance(
    slide5_fixture: tuple[
        MetricFloorPlan,
        PortalTopologyGraph,
        PairwiseCostTensor,
        dict[str, list[Point2D]],
    ],
) -> None:
    """Test 4.2: Verify Rule 6 wall clearance and host room placement for every hub.

    Invariants:
    1. host_room.contains(H_k) is True.
    2. host_room is an eligible room assigned to H_k.
    3. distance(H_k, obstacles) >= 0.50m - 0.001m.
    """
    plan, topology, tensor, candidates = slide5_fixture

    partition = ZonePartitioner.solve_partition(
        plan=plan,
        topology=topology,
        cost_tensor=tensor,
        candidates=candidates,
    )

    obs = plan.obstacles_geom
    min_allowed_dist = 0.50 - 0.001  # 0.499m checker threshold

    for hub in partition.hubs:
        host_room = plan.rooms[hub.room_id]
        pt = hub.location.as_shapely()

        # Invariant 1: host_room contains H_k
        assert host_room.geometry.contains(pt), (
            f"Hub {hub.hub_id} ({hub.location.coords}) is not strictly inside host room {hub.room_id}"
        )

        # Invariant 2: host_room is eligible and member of assigned_rooms
        assert host_room.is_eligible, f"Host room {hub.room_id} is not an eligible room"
        assert hub.room_id in hub.assigned_rooms, (
            f"Host room {hub.room_id} must be in assigned_rooms {hub.assigned_rooms}"
        )

        # Invariant 3: Rule 6 clearance >= 0.50m from obstacles (walls and windows)
        dist = obs.distance(pt)
        assert dist >= min_allowed_dist, (
            f"Hub {hub.hub_id} violates Rule 6: dist={dist:.4f}m < {min_allowed_dist:.3f}m"
        )


def test_area_gap_tie_breaker() -> None:
    """Test 4.3: Verify area-gap tie-breaker selects balanced partition when routing costs are equal.

    Given a symmetric 4-room plan where two partitions have equal routing cost J = 30.0:
    - Partition 1: {R1, R2} (80 m^2) and {R3, R4} (20 m^2) -> delta A_gap = 60 m^2
    - Partition 2: {R1, R3} (50 m^2) and {R2, R4} (50 m^2) -> delta A_gap = 0 m^2
    The solver must select the balanced (50, 50) partition.
    """
    # Build symmetric 4-room plan with 6 nodes (H_min = 2)
    r1 = EligibleOrExcludedRoom(
        room_id="R1",
        category=RoomCategory.BEDROOM,
        is_eligible=True,
        polygon_wkt=sg.box(0, 0, 4, 10).wkt,
        area_m2=40.0,
    )
    r2 = EligibleOrExcludedRoom(
        room_id="R2",
        category=RoomCategory.BEDROOM,
        is_eligible=True,
        polygon_wkt=sg.box(4, 0, 8, 10).wkt,
        area_m2=40.0,
    )
    r3 = EligibleOrExcludedRoom(
        room_id="R3",
        category=RoomCategory.LIVING,
        is_eligible=True,
        polygon_wkt=sg.box(0, 10, 4, 12.5).wkt,
        area_m2=10.0,
    )
    r4 = EligibleOrExcludedRoom(
        room_id="R4",
        category=RoomCategory.LIVING,
        is_eligible=True,
        polygon_wkt=sg.box(4, 10, 8, 12.5).wkt,
        area_m2=10.0,
    )

    # 6 nodes total -> H_min = max(ceil(100/100), ceil(6/5)) = 2 hubs
    nodes = {
        "N1a": FixedNode(node_id="N1a", room_id="R1", location=Point2D(x=1.0, y=5.0)),
        "N1b": FixedNode(node_id="N1b", room_id="R1", location=Point2D(x=3.0, y=5.0)),
        "N2a": FixedNode(node_id="N2a", room_id="R2", location=Point2D(x=5.0, y=5.0)),
        "N2b": FixedNode(node_id="N2b", room_id="R2", location=Point2D(x=7.0, y=5.0)),
        "N3": FixedNode(node_id="N3", room_id="R3", location=Point2D(x=2.0, y=11.0)),
        "N4": FixedNode(node_id="N4", room_id="R4", location=Point2D(x=6.0, y=11.0)),
    }

    plan = MetricFloorPlan(
        plan_id="tie_breaker_test",
        scale_units_per_m=1.0,
        total_area_m2=100.0,
        inner_wkt=sg.box(0, 0, 8, 12.5).wkt,
        free_space_wkt=sg.box(0, 0, 8, 12.5).wkt,
        obstacles_wkt=sg.MultiPolygon([sg.box(0, 0, 0.1, 0.1)]).wkt,
        rooms={"R1": r1, "R2": r2, "R3": r3, "R4": r4},
        nodes=nodes,
    )

    # 4-cycle graph: R1-R2-R4-R3-R1
    topo = PortalTopologyGraph(
        plan_id="tie_breaker_test",
        eligible_room_ids=["R1", "R2", "R3", "R4"],
        adjacency={
            "R1": ["R2", "R3"],
            "R2": ["R1", "R4"],
            "R3": ["R1", "R4"],
            "R4": ["R2", "R3"],
        },
        is_fully_connected=True,
    )

    candidates = {
        "R1": [Point2D(x=2.0, y=4.0)],
        "R2": [Point2D(x=6.0, y=4.0)],
        "R3": [Point2D(x=2.0, y=11.5)],
        "R4": [Point2D(x=6.0, y=11.5)],
    }

    # Mock evaluate_zone_exact_ring so both 2-room partitions have cost 15.0 (total J = 30.0)
    orig_eval = ZonePartitioner.evaluate_zone_exact_ring

    def mock_eval(zone_rooms, plan, cost_tensor, candidates):
        if len(zone_rooms) == 2:
            return candidates[zone_rooms[0]][0], zone_rooms[0], 15.0
        return candidates[zone_rooms[0]][0], zone_rooms[0], 50.0

    ZonePartitioner.evaluate_zone_exact_ring = mock_eval
    try:
        partition = ZonePartitioner.solve_partition(
            plan=plan,
            topology=topo,
            cost_tensor=MagicMock(),
            candidates=candidates,
        )

        assert partition.hub_count == 2
        # Solver must choose the balanced partition: delta A_gap == 0.0
        assert partition.area_gap_m2 == pytest.approx(0.0)

        areas = [h.zone_area_m2 for h in partition.hubs]
        assert areas == [50.0, 50.0]

        # Verify selected rooms correspond to {R1, R3} and {R2, R4}
        zone1_rooms = set(partition.hubs[0].assigned_rooms)
        zone2_rooms = set(partition.hubs[1].assigned_rooms)
        assert (zone1_rooms == {"R1", "R3"} and zone2_rooms == {"R2", "R4"}) or (
            zone1_rooms == {"R2", "R4"} and zone2_rooms == {"R1", "R3"}
        )
    finally:
        ZonePartitioner.evaluate_zone_exact_ring = orig_eval


def test_phase1_to_phase4_regression(
    slide5_fixture: tuple[
        MetricFloorPlan,
        PortalTopologyGraph,
        PairwiseCostTensor,
        dict[str, list[Point2D]],
    ],
) -> None:
    """Test 4.4: Re-verify end-to-end integration across Phases 1, 2, 3, and 4."""
    plan, topology, tensor, candidates = slide5_fixture

    # Phase 1 Invariants
    assert plan.plan_id == "slide5_schematic"
    assert len(plan.rooms) == 7
    assert len(plan.nodes) == 6
    assert plan.scale_units_per_m == pytest.approx(10.0)

    # Phase 2 Invariants
    assert len(topology.portals) >= 4
    assert "R04" in topology.leaf_room_ids
    assert "R06" in topology.leaf_room_ids

    # Phase 3 Invariants
    assert tensor.computation_time_ms < 400.0
    for node_id in plan.nodes:
        assert tensor.get_metric(node_id, node_id) is not None

    # Phase 4 Invariants
    partition = ZonePartitioner.solve_partition(plan, topology, tensor, candidates)
    assert partition.hub_count == 2
    assert partition.lower_bound_hubs == 2
    assert partition.area_gap_m2 == pytest.approx(24.0)

    # Every eligible room is covered exactly once
    covered_rooms: list[str] = []
    for hub in partition.hubs:
        covered_rooms.extend(hub.assigned_rooms)
    assert sorted(covered_rooms) == ["R01", "R02", "R03", "R04", "R05", "R06"]

    # Every zone is a door-connected subgraph in topology
    for hub in partition.hubs:
        rooms_in_zone = hub.assigned_rooms
        if len(rooms_in_zone) > 1:
            for r in rooms_in_zone:
                neighbors = topology.adjacency.get(r, [])
                has_connection = any(nbr in rooms_in_zone for nbr in neighbors)
                assert has_connection, f"Room {r} in hub {hub.hub_id} is disconnected"

    # Hub locations are valid Hanan grid vertices
    for hub in partition.hubs:
        assert tensor.grid.has_vertex(hub.location), (
            f"Hub {hub.hub_id} location {hub.location.coords} is not a valid Hanan grid vertex"
        )
