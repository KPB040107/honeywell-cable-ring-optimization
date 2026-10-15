"""Phase 9: Interactive Wire-Cut Survivability Simulator Verification Suite.

Tests NFPA 72 Class A bidirectional circuit simulation:
1. test_slide5_exhaustive_single_cut_survivability:
   Exhaustively tests every single leg cut across Hub 1 and Hub 2 on Slide 5 schematic,
   proving 100% device survivability on closed rings under any single fault.
2. test_single_door_leaf_room_fault_isolation:
   Verifies fault isolation on Bedroom 2 (single-door leaf room) where Door Lane A cut is
   isolated and reverse return terminal powers the leaf node via Door Lane B (separated >= 200mm).
3. test_cut_at_arbitrary_spatial_coordinate:
   Verifies projection of arbitrary cut coordinate (6.8, 1.2) onto polyline segment N1 -> N3,
   confirming primary outbound powers N1 and reverse return powers N3 and N2.
4. test_multi_cut_degradation_detection:
   Simulates 2 simultaneous cuts on legs 0 and 2, confirming isolated intermediate nodes
   are marked OFFLINE with degraded survivability and non-compliance.
5. test_contract_serialization_roundtrip:
   Validates Pydantic v2 JSON serialization and deserialization roundtrip.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from resplan_router.contracts import (
    ClassASimulationReport,
    CompletedHubRing,
    HubAssignment,
    MetricFloorPlan,
    NodeSurvivabilityStatus,
    Point2D,
    PortalTopologyGraph,
    PowerSource,
    RouteSegment,
    WireCutLocation,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer
from resplan_router.phase9_survivability.circuit_simulator import ClassACircuitSimulator


@pytest.fixture
def slide5_rings() -> tuple[MetricFloorPlan, PortalTopologyGraph, CompletedHubRing, CompletedHubRing]:
    """Fixture synthesizing the official Slide 5 benchmark rings for H1 and H2."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)

    h1_pt = Point2D(x=5.2, y=3.6)
    h2_pt = Point2D(x=11.4, y=7.0)

    h1 = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=h1_pt,
        assigned_rooms=["R01", "R02", "R03"],
        assigned_nodes=["N1", "N2", "N3"],
        zone_area_m2=71.0,
    )
    h2 = HubAssignment(
        hub_id="H2",
        room_id="R05",
        location=h2_pt,
        assigned_rooms=["R04", "R05", "R06"],
        assigned_nodes=["N4", "N5", "N6"],
        zone_area_m2=47.0,
    )

    grid = build_hanan_grid(plan, topology, extra_points=[h1_pt, h2_pt])
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()

    seq1 = ["H1", "N1", "N3", "N2", "H1"]
    ring1 = synthesizer.synthesize_ring(h1, seq1, plan, topology, router, grid)

    seq2 = ["H2", "N4", "N5", "N6", "H2"]
    ring2 = synthesizer.synthesize_ring(h2, seq2, plan, topology, router, grid)

    return plan, topology, ring1, ring2


def test_slide5_exhaustive_single_cut_survivability(
    slide5_rings: tuple[MetricFloorPlan, PortalTopologyGraph, CompletedHubRing, CompletedHubRing],
) -> None:
    """Test 1: Exhaustively verify 100% survivability on all single wire cuts across Slide 5 rings."""
    plan, _topology, ring1, ring2 = slide5_rings
    sim = ClassACircuitSimulator()

    # Hub 1: H1 -> N1 -> N3 -> N2 -> H1 (4 legs)
    result1 = sim.verify_loop_resilience_exhaustively(ring1, plan_id=plan.plan_id)
    assert result1["hub_id"] == "H1"
    assert result1["legs_tested"] == 4
    assert result1["all_single_cuts_survived"] is True
    assert result1["min_survivability_pct"] == 100.0
    assert result1["max_survivability_pct"] == 100.0

    for rep in result1["reports"]:
        assert rep.survivability_pct == 100.0
        assert rep.offline_nodes == []
        assert rep.online_nodes == 3
        assert rep.total_nodes == 3
        assert rep.nfpa72_class_a_compliant is True
        assert rep.is_severed is True
        assert rep.cut_location is not None

    # Hub 2: H2 -> N4 -> N5 -> N6 -> H2 (4 legs)
    result2 = sim.verify_loop_resilience_exhaustively(ring2, plan_id=plan.plan_id)
    assert result2["hub_id"] == "H2"
    assert result2["legs_tested"] == 4
    assert result2["all_single_cuts_survived"] is True
    assert result2["min_survivability_pct"] == 100.0
    assert result2["max_survivability_pct"] == 100.0

    for rep in result2["reports"]:
        assert rep.survivability_pct == 100.0
        assert rep.offline_nodes == []
        assert rep.online_nodes == 3
        assert rep.total_nodes == 3
        assert rep.nfpa72_class_a_compliant is True
        assert rep.is_severed is True
        assert rep.cut_location is not None


def test_single_door_leaf_room_fault_isolation(
    slide5_rings: tuple[MetricFloorPlan, PortalTopologyGraph, CompletedHubRing, CompletedHubRing],
) -> None:
    """Test 2: Single-door leaf room fault isolation (Bedroom 2, N4).

    When Door Lane A (inbound leg H2 -> N4) is severed, Door Lane B (outbound leg N4 -> N5)
    remains intact due to physical lane separation >= 200 mm.
    The hub return terminal successfully energizes N4 from reverse direction.
    """
    plan, _topology, _ring1, ring2 = slide5_rings
    sim = ClassACircuitSimulator()

    # Leg 0 is H2 -> N4 (entering Bedroom 2 via Door Lane A)
    report = sim.simulate_wire_cut(ring2, cut_leg_index=0, cut_fraction=0.5, plan_id=plan.plan_id)

    assert report.is_severed is True
    assert report.cut_location is not None
    assert report.cut_location.leg_index == 0
    assert report.cut_location.from_node == "H2"
    assert report.cut_location.to_node == "N4"

    # Upstream of cut: no device nodes reached from primary outbound
    assert report.primary_branch_nodes == []

    # Downstream of cut: all nodes energized via return terminal (reverse order H2 -> N6 -> N5 -> N4)
    assert report.return_branch_nodes == ["N6", "N5", "N4"]
    assert report.offline_nodes == []
    assert report.online_nodes == 3
    assert report.survivability_pct == 100.0
    assert report.nfpa72_class_a_compliant is True

    # Validate Bedroom 2 node N4 specifically
    status_n4 = report.node_statuses["N4"]
    assert status_n4.is_online is True
    assert status_n4.power_source == PowerSource.RETURN_TERMINAL
    assert status_n4.hop_count == 3  # H2_return -> N6 -> N5 -> N4
    assert status_n4.path_length_m > 0.0


def test_cut_at_arbitrary_spatial_coordinate(
    slide5_rings: tuple[MetricFloorPlan, PortalTopologyGraph, CompletedHubRing, CompletedHubRing],
) -> None:
    """Test 3: Cut at arbitrary spatial coordinate (6.8, 1.2) along segment N1 -> N3.

    Verifies projection onto polyline, segment attribution, and branch split:
    N1 powered via Primary Outbound; N3 and N2 powered via Return Terminal.
    """
    plan, _topology, ring1, _ring2 = slide5_rings
    sim = ClassACircuitSimulator()

    # Sever cable at coordinate near N1 along corridor route towards N3
    report = sim.simulate_arbitrary_cut_point(ring1, cut_x=6.8, cut_y=1.2, plan_id=plan.plan_id)

    assert report.is_severed is True
    assert report.cut_location is not None
    assert report.cut_location.leg_index == 1
    assert report.cut_location.from_node == "N1"
    assert report.cut_location.to_node == "N3"

    # Primary outbound branch powers N1
    assert report.primary_branch_nodes == ["N1"]
    status_n1 = report.node_statuses["N1"]
    assert status_n1.is_online is True
    assert status_n1.power_source == PowerSource.PRIMARY_OUTBOUND
    assert status_n1.hop_count == 1
    assert status_n1.path_length_m == pytest.approx(4.8, abs=0.5)

    # Return terminal branch powers N2 and N3
    assert set(report.return_branch_nodes) == {"N2", "N3"}
    assert report.return_branch_nodes == ["N2", "N3"]

    status_n2 = report.node_statuses["N2"]
    assert status_n2.is_online is True
    assert status_n2.power_source == PowerSource.RETURN_TERMINAL
    assert status_n2.hop_count == 1
    assert status_n2.path_length_m == pytest.approx(6.6, abs=0.5)

    status_n3 = report.node_statuses["N3"]
    assert status_n3.is_online is True
    assert status_n3.power_source == PowerSource.RETURN_TERMINAL
    assert status_n3.hop_count == 2
    assert status_n3.path_length_m == pytest.approx(11.9, abs=0.5)

    # Overall resilience
    assert report.survivability_pct == 100.0
    assert report.offline_nodes == []
    assert report.nfpa72_class_a_compliant is True
    assert report.max_latency_path_m == pytest.approx(11.9, abs=0.5)


def test_multi_cut_degradation_detection(
    slide5_rings: tuple[MetricFloorPlan, PortalTopologyGraph, CompletedHubRing, CompletedHubRing],
) -> None:
    """Test 4: Multi-cut degradation detection (2 simultaneous cuts on legs 0 and 2).

    Simulates simultaneous cuts on Leg 0 (H1 -> N1) and Leg 2 (N3 -> N2).
    Nodes N1 and N3 are isolated between the two cuts and must be marked OFFLINE.
    Node N2 remains powered by Return Terminal.
    NFPA 72 Class A compliance must be False.
    """
    plan, _topology, ring1, _ring2 = slide5_rings
    sim = ClassACircuitSimulator()

    report = sim.simulate_multi_cut(ring1, cut_leg_indices=[0, 2], plan_id=plan.plan_id)

    assert report.is_severed is True
    assert len(report.cut_locations) == 2
    assert report.primary_branch_nodes == []
    assert report.return_branch_nodes == ["N2"]
    assert sorted(report.offline_nodes) == ["N1", "N3"]

    assert report.total_nodes == 3
    assert report.online_nodes == 1
    assert report.survivability_pct == pytest.approx(33.33, abs=0.1)
    assert report.nfpa72_class_a_compliant is False

    # Node status checks
    assert report.node_statuses["N1"].is_online is False
    assert report.node_statuses["N1"].power_source == PowerSource.OFFLINE
    assert report.node_statuses["N1"].path_length_m == 0.0

    assert report.node_statuses["N3"].is_online is False
    assert report.node_statuses["N3"].power_source == PowerSource.OFFLINE
    assert report.node_statuses["N3"].path_length_m == 0.0

    assert report.node_statuses["N2"].is_online is True
    assert report.node_statuses["N2"].power_source == PowerSource.RETURN_TERMINAL
    assert report.node_statuses["N2"].hop_count == 1
    assert report.node_statuses["N2"].path_length_m > 0.0


def test_contract_serialization_roundtrip() -> None:
    """Test 5: Pydantic v2 JSON serialization and deserialization roundtrip."""
    loc = WireCutLocation(
        hub_id="H1",
        leg_index=1,
        from_node="N1",
        to_node="N3",
        cut_point=Point2D(x=6.8, y=1.2),
        distance_along_leg_m=2.35,
    )
    statuses = {
        "N1": NodeSurvivabilityStatus(
            node_id="N1",
            is_online=True,
            power_source=PowerSource.PRIMARY_OUTBOUND,
            path_length_m=4.8,
            hop_count=1,
        ),
        "N2": NodeSurvivabilityStatus(
            node_id="N2",
            is_online=True,
            power_source=PowerSource.RETURN_TERMINAL,
            path_length_m=6.6,
            hop_count=1,
        ),
        "N3": NodeSurvivabilityStatus(
            node_id="N3",
            is_online=True,
            power_source=PowerSource.RETURN_TERMINAL,
            path_length_m=11.9,
            hop_count=2,
        ),
    }
    report = ClassASimulationReport(
        plan_id="slide5_schematic",
        hub_id="H1",
        is_severed=True,
        cut_location=loc,
        cut_locations=[loc],
        total_nodes=3,
        online_nodes=3,
        survivability_pct=100.0,
        primary_branch_nodes=["N1"],
        return_branch_nodes=["N2", "N3"],
        offline_nodes=[],
        node_statuses=statuses,
        nfpa72_class_a_compliant=True,
        max_latency_path_m=11.9,
    )

    json_str = report.model_dump_json(indent=2)
    assert "slide5_schematic" in json_str
    assert "primary_outbound" in json_str
    assert "return_terminal" in json_str

    deserialized = ClassASimulationReport.model_validate_json(json_str)
    assert deserialized == report
    assert deserialized.node_statuses["N1"].power_source == PowerSource.PRIMARY_OUTBOUND
    assert deserialized.cut_location.cut_point.coords == (6.8, 1.2)
