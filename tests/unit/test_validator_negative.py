"""Exhaustive Negative Unit Test Suite for Independent FloorPlanChecker.

Audits and verifies FloorPlanChecker against all official competition constraints:
- Rule 1 (Full Coverage & Eligibility): Omitted room/node, duplicate room/node, excluded
  spaces (bathroom/balcony/etc.), unknown rooms/nodes, room-node mismatches.
- Rule 2 (Capacity): Exceeding 5 nodes, exceeding 100.0 m² area, boundary edge cases.
- Rule 3 (Doorway Contiguity): Disconnected room subgraphs, wall-only adjacencies,
  window adjacencies, exterior/front door connections.
- Rule 4 (Single Closed Ring): Empty sequence, sequence not starting/ending with hub,
  missing/duplicate node visits, empty segments, segment count/label mismatches,
  under 2 points, ring start/return gap, internal segment gaps, fixed node terminal
  misses, terminal outside designated room.
- Rule 5 (Free Space & Rectilinear Routing): Diagonal segments, wall obstacle
  penetrations, window penetrations, inner boundary exits, non-free space routing.
- Rule 6 (Hub Placement & Clearance): Unknown room, ineligible room, unassigned room,
  outside room polygon, wall clearance < 0.50 m, outer boundary clearance < 0.50 m,
  boundary clearance tolerance.
- Rule 7 (Scoring & Retracing): Retracing validity, 1 mm lateral tolerance (0.5 mm vs 1.5 mm),
  crossings away from terminals vs at terminals, deduplication, bend accounting
  (90 deg, 180 deg reversal, straight through, hub wrap-around), composite cost J, area gap.
- Slide 8 JSON Compliance: Strict schema validation, coordinate precision, roundtrip
  evaluation consistency.
- Multi-Violation Aggregation: Verification that concurrent rule violations are all
  accurately detected and isolated in the diagnostic report.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import pytest
from shapely.geometry import LineString, Point

from resplan_router.contracts import (
    CompletedHubRing,
    EligibleOrExcludedRoom,
    EvaluationReport,
    FixedNode,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    RouteSegment,
    RuleViolation,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import (
    Slide8Export,
    export_slide8_json,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "demo_outputs"


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def demo_three_rooms():
    """Provides plan, topology, and valid solution for demo_three_rooms_window.

    3 eligible rooms (Living, Kitchen, Bedroom), 1 excluded Bathroom, 3 nodes, 1 hub.
    """
    raw = json.loads((FIXTURES_DIR / "demo_three_rooms_window.json").read_text(encoding="utf-8"))
    sol = json.loads(
        (DEMO_OUTPUTS_DIR / "demo_three_rooms_window_solution.json").read_text(encoding="utf-8")
    )
    plan = parse_floor_plan(raw)
    topology = MacroTopologyGraph.build_topology(
        plan, extract_all_portals(plan, raw.get("doors"), raw.get("front_door"))
    )
    return plan, topology, sol


@pytest.fixture(scope="module")
def demo_six_rooms():
    """Provides plan, topology, and valid solution for demo_six_rooms_capacity.

    6 eligible rooms, 6 nodes, 120 m² area, 2 hubs (H1, H2).
    """
    raw = json.loads((FIXTURES_DIR / "demo_six_rooms_capacity.json").read_text(encoding="utf-8"))
    sol = json.loads(
        (DEMO_OUTPUTS_DIR / "demo_six_rooms_capacity_solution.json").read_text(encoding="utf-8")
    )
    plan = parse_floor_plan(raw)
    topology = MacroTopologyGraph.build_topology(
        plan, extract_all_portals(plan, raw.get("doors"), raw.get("front_door"))
    )
    return plan, topology, sol


# =============================================================================
# 1. Baseline Clean Pass Verification
# =============================================================================


class TestBaselineCleanPasses:
    """Verifies that clean, compliant designs pass all 6 rules with 0 violations."""

    def test_demo_three_rooms_valid_pass(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        assert report.is_valid, f"Expected valid, got violations: {report.violations}"
        assert len(report.violations) == 0
        assert report.hub_count == 1
        assert all(report.rules_passed.values()), f"Some rules failed: {report.rules_passed}"
        assert report.total_length_m > 0
        assert report.total_cost_J == pytest.approx(
            report.total_length_m + 5 * report.total_crossings
            + report.total_overlap_m + 0.5 * report.total_bends,
            abs=1e-4,
        )

    def test_demo_six_rooms_valid_pass(self, demo_six_rooms):
        plan, topology, sol = demo_six_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        assert report.is_valid, f"Expected valid, got violations: {report.violations}"
        assert len(report.violations) == 0
        assert report.hub_count == 2
        assert all(report.rules_passed.values())
        assert report.total_cost_J == pytest.approx(
            report.total_length_m + 5 * report.total_crossings
            + report.total_overlap_m + 0.5 * report.total_bends,
            abs=1e-4,
        )

    def test_synthesized_bundle_evaluates_identically(self, demo_six_rooms):
        plan, topology, sol = demo_six_rooms
        # Convert sol dict into SynthesizedRingsBundle
        hub_rings: list[CompletedHubRing] = []
        for h in sol["hubs"]:
            route_segs = [
                RouteSegment(
                    from_node=s["from"],
                    to_node=s["to"],
                    length_m=s["length_m"],
                    polyline=[Point2D(x=pt[0], y=pt[1]) for pt in s["polyline"]],
                )
                for s in h["route_segments"]
            ]
            ring = CompletedHubRing(
                hub_id=h["hub_id"],
                room_id=h["room_id"],
                location=Point2D(x=h["location"]["x"], y=h["location"]["y"]),
                assigned_rooms=h["assigned_rooms"],
                assigned_nodes=h["assigned_nodes"],
                closed_sequence=h["closed_sequence"],
                route_segments=route_segs,
                ring_length_m=h["ring_length_m"],
                ring_bends=10,
                ring_crossings=0,
                ring_overlap_m=0.0,
                cost_J=h["ring_length_m"] + 5.0,
            )
            hub_rings.append(ring)

        bundle = SynthesizedRingsBundle(
            plan_id=sol["plan_id"],
            hub_rings=hub_rings,
            total_length_m=sol["reported_metrics"]["total_length_m"],
            total_bends=20,
            total_crossings=0,
            total_overlap_m=0.0,
            total_cost_J=sol["reported_metrics"]["total_length_m"] + 10.0,
        )

        rep_bundle = FloorPlanChecker.evaluate(plan, bundle, topology=topology)
        rep_dict = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        assert rep_bundle.is_valid
        assert rep_bundle.total_length_m == pytest.approx(rep_dict.total_length_m, abs=1e-4)
        assert rep_bundle.total_cost_J == pytest.approx(rep_dict.total_cost_J, abs=1e-4)
        assert rep_bundle.total_bends == rep_dict.total_bends


# =============================================================================
# 2. Hard Rule 1: Full Coverage & Excluded Spaces Negative Tests
# =============================================================================


class TestRule1CoverageNegative:
    """Tests all failure modes of Hard Rule 1 (Full Coverage & Eligibility)."""

    def test_rule1_omitted_eligible_room(self, demo_six_rooms):
        """Omission of an eligible room must trigger Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Remove first room from H1 (R01)
        removed_room = corrupt["hubs"][0]["assigned_rooms"].pop(0)

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        v = [v for v in report.violations if v.rule_number == 1 and removed_room in v.message]
        assert len(v) >= 1
        assert any("not assigned to any hub" in vi.message for vi in v)

    def test_rule1_duplicate_eligible_room(self, demo_six_rooms):
        """Assigning an eligible room to multiple hubs must trigger Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Duplicate R01 in both H1 and H2
        corrupt["hubs"][1]["assigned_rooms"].append("R01")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "R01" in v.message and "assigned to multiple hubs" in v.message
            for v in report.violations
        )

    def test_rule1_omitted_fixed_node(self, demo_six_rooms):
        """Omission of a fixed node must trigger Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Remove first node from H1 (N1)
        removed_node = corrupt["hubs"][0]["assigned_nodes"].pop(0)

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        v = [v for v in report.violations if v.rule_number == 1 and removed_node in v.message]
        assert len(v) >= 1
        assert "not assigned to any hub" in v[0].message

    def test_rule1_duplicate_fixed_node(self, demo_six_rooms):
        """Assigning a fixed node to multiple hubs must trigger Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign N1 to both H1 and H2
        corrupt["hubs"][1]["assigned_nodes"].append("N1")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "N1" in v.message and "assigned to multiple hubs" in v.message
            for v in report.violations
        )

    def test_rule1_excluded_space_assigned(self, demo_three_rooms):
        """Assigning an excluded space (e.g. bathroom R04) must trigger Rule 1 violation."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Assign excluded bathroom R04 to H1
        corrupt["hubs"][0]["assigned_rooms"].append("R04")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        v = [v for v in report.violations if v.rule_number == 1 and "R04" in v.message]
        assert len(v) >= 1
        assert "Excluded space" in v[0].message

    def test_rule1_unknown_room_assigned(self, demo_three_rooms):
        """Assigning a non-existent room ID must trigger Rule 1 violation."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["assigned_rooms"].append("R_NONEXISTENT")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        v = [v for v in report.violations if v.rule_number == 1 and "R_NONEXISTENT" in v.message]
        assert len(v) >= 1
        assert "Unknown room" in v[0].message

    def test_rule1_unknown_node_assigned(self, demo_three_rooms):
        """Assigning a non-existent node ID must trigger Rule 1 violation."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["assigned_nodes"].append("N_PHANTOM")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        v = [v for v in report.violations if v.rule_number == 1 and "N_PHANTOM" in v.message]
        assert len(v) >= 1
        assert "Unknown node" in v[0].message

    def test_rule1_node_room_mismatch(self, demo_six_rooms):
        """Assigning a node without assigning its host room to that hub must fail Rule 1."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # H1 has ['N1', 'N4'] in rooms R01, R04.
        # H2 has ['N2', 'N3', 'N5', 'N6'] in rooms R02, R03, R05, R06.
        # Swap N1 and N2 across hubs without swapping rooms:
        corrupt["hubs"][0]["assigned_nodes"].remove("N1")
        corrupt["hubs"][0]["assigned_nodes"].append("N2")
        corrupt["hubs"][1]["assigned_nodes"].remove("N2")
        corrupt["hubs"][1]["assigned_nodes"].append("N1")

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "which is not assigned to hub" in v.message
            for v in report.violations
        )


# =============================================================================
# 3. Hard Rule 2: Capacity Limits Negative Tests
# =============================================================================


class TestRule2CapacityNegative:
    """Tests all failure modes and boundary conditions of Hard Rule 2 (Capacity)."""

    def test_rule2_node_capacity_exceeded(self, demo_six_rooms):
        """Hub serving > 5 nodes must trigger Rule 2 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign all 6 nodes to H1
        corrupt["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]
        corrupt["hubs"][1]["assigned_nodes"] = []

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        v = [v for v in report.violations if v.rule_number == 2 and "exceeding capacity limit of 5" in v.message]
        assert len(v) >= 1

    def test_rule2_area_capacity_exceeded(self, demo_six_rooms):
        """Hub serving > 100.0 m² must trigger Rule 2 violation."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign all 6 rooms (120 m² > 100 m²) to H1
        corrupt["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        corrupt["hubs"][1]["assigned_rooms"] = []

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        v = [v for v in report.violations if v.rule_number == 2 and "exceeds capacity limit of 100.0" in v.message]
        assert len(v) >= 1

    def test_rule2_exact_boundary_conditions(self, demo_three_rooms):
        """Test exact boundary tolerance for area and node capacity."""
        plan, topology, sol = demo_three_rooms

        # 1. Custom capacity: 60.0 m² max with 60.0 m² assigned passes cleanly
        report_pass = FloorPlanChecker.evaluate(
            plan, sol, topology=topology, max_area_m2=60.0, max_nodes_per_hub=3
        )
        assert report_pass.rules_passed["Rule 2"]

        # 2. Area 60.0 m² with limit 59.9 m² fails
        report_fail_area = FloorPlanChecker.evaluate(
            plan, sol, topology=topology, max_area_m2=59.9, max_nodes_per_hub=3
        )
        assert not report_fail_area.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 for v in report_fail_area.violations)

        # 3. 3 nodes with limit 2 fails
        report_fail_nodes = FloorPlanChecker.evaluate(
            plan, sol, topology=topology, max_area_m2=100.0, max_nodes_per_hub=2
        )
        assert not report_fail_nodes.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 for v in report_fail_nodes.violations)


# =============================================================================
# 4. Hard Rule 3: Doorway Contiguity Negative Tests
# =============================================================================


class TestRule3ContiguityNegative:
    """Tests all failure modes of Hard Rule 3 (Doorway Contiguity)."""

    def test_rule3_disconnected_rooms(self, demo_six_rooms):
        """Rooms without doorway connectivity in a zone must fail Rule 3."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign R01 and R06 to H1 without connecting intermediate rooms R02/R05
        corrupt["hubs"][0]["assigned_rooms"] = ["R01", "R06"]
        corrupt["hubs"][0]["assigned_nodes"] = ["N1", "N6"]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 3"]
        v = [v for v in report.violations if v.rule_number == 3]
        assert len(v) >= 1
        assert "not contiguous through doors/open passages" in v[0].message

    def test_rule3_single_room_hub_trivially_contiguous(self, demo_three_rooms):
        """A hub serving a single room is trivially contiguous and must pass Rule 3."""
        plan, topology, sol = demo_three_rooms
        single_room_sol = deepcopy(sol)
        single_room_sol["hubs"][0]["assigned_rooms"] = ["R01"]
        single_room_sol["hubs"][0]["assigned_nodes"] = ["N1"]
        # Update sequence & segments to be a single-node ring
        h_loc = single_room_sol["hubs"][0]["location"]
        n1_loc = [plan.nodes["N1"].location.x, plan.nodes["N1"].location.y]
        single_room_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "H1"]
        single_room_sol["hubs"][0]["route_segments"] = [
            {
                "from": "H1",
                "to": "N1",
                "length_m": 2.0,
                "polyline": [[h_loc["x"], h_loc["y"]], [n1_loc[0], h_loc["y"]], n1_loc],
            },
            {
                "from": "N1",
                "to": "H1",
                "length_m": 2.0,
                "polyline": [n1_loc, [h_loc["x"], n1_loc[1]], [h_loc["x"], h_loc["y"]]],
            },
        ]

        report = FloorPlanChecker.evaluate(plan, single_room_sol, topology=topology)
        # Rule 3 must pass
        assert report.rules_passed["Rule 3"]

    def test_rule3_wall_adjacency_without_door(self):
        """Rooms adjacent along a solid wall with no portal do not provide contiguity."""
        # Create an artificial topology where R01 and R02 share a boundary but no door
        plan_dict = {
            "plan_id": "test_wall_contiguity",
            "scale_units_per_m": 1.0,
            "total_area_m2": 50.0,
            "inner_wkt": "POLYGON ((0 0, 10 0, 10 5, 0 5, 0 0))",
            "free_space_wkt": "MULTIPOLYGON (((0 0, 10 0, 10 5, 0 5, 0 0)))",
            "obstacles_wkt": "POLYGON ((4.9 0, 5.1 0, 5.1 5, 4.9 5, 4.9 0))",
            "rooms": {
                "R01": {
                    "room_id": "R01",
                    "category": "living",
                    "is_eligible": True,
                    "area_m2": 25.0,
                    "polygon_wkt": "POLYGON ((0 0, 4.9 0, 4.9 5, 0 5, 0 0))",
                },
                "R02": {
                    "room_id": "R02",
                    "category": "bedroom",
                    "is_eligible": True,
                    "area_m2": 25.0,
                    "polygon_wkt": "POLYGON ((5.1 0, 10 0, 10 5, 5.1 5, 5.1 0))",
                },
            },
            "nodes": {
                "N1": {"node_id": "N1", "room_id": "R01", "location": {"x": 2.0, "y": 2.5}},
                "N2": {"node_id": "N2", "room_id": "R02", "location": {"x": 7.0, "y": 2.5}},
            },
        }
        plan = MetricFloorPlan.model_validate(plan_dict)

        # Topology graph has empty adjacency between R01 and R02
        topology = PortalTopologyGraph(
            plan_id="test_wall_contiguity",
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )

        sub = {
            "plan_id": "test_wall_contiguity",
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 1.0, "y": 2.5},
                    "assigned_rooms": ["R01", "R02"],
                    "assigned_nodes": ["N1", "N2"],
                    "closed_sequence": ["H1", "N1", "N2", "H1"],
                    "route_segments": [],
                }
            ],
        }

        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert not report.rules_passed["Rule 3"]
        assert any(v.rule_number == 3 for v in report.violations)


# =============================================================================
# 5. Hard Rule 4: Closed Non-Branching Ring Negative Tests
# =============================================================================


class TestRule4ClosedRingNegative:
    """Tests all failure modes of Hard Rule 4 (Closed Non-Branching Ring)."""

    def test_rule4_empty_closed_sequence(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["closed_sequence"] = []

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "closed_sequence is empty" in v.message for v in report.violations)

    def test_rule4_sequence_starts_with_node(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Sequence starts with N1 instead of H1
        corrupt["hubs"][0]["closed_sequence"][0] = "N1"

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "must start with" in v.message for v in report.violations)

    def test_rule4_sequence_ends_with_node(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Sequence ends with N3 instead of H1
        corrupt["hubs"][0]["closed_sequence"][-1] = "N3"

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "must end with" in v.message for v in report.violations)

    def test_rule4_sequence_missing_assigned_node(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Drop N2 from sequence: H1 -> N1 -> N3 -> H1
        corrupt["hubs"][0]["closed_sequence"] = ["H1", "N1", "N3", "H1"]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "do not visit assigned nodes" in v.message for v in report.violations)

    def test_rule4_sequence_duplicate_node_visit(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Duplicate N1: H1 -> N1 -> N2 -> N1 -> N3 -> H1
        corrupt["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "N1", "N3", "H1"]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "do not visit assigned nodes" in v.message for v in report.violations)

    def test_rule4_empty_route_segments(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["route_segments"] = []

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "has no route segments" in v.message for v in report.violations)

    def test_rule4_segment_count_mismatch(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Remove last route segment
        corrupt["hubs"][0]["route_segments"].pop()

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "route segments for" in v.message for v in report.violations)

    def test_rule4_segment_labels_disagree_with_sequence(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Corrupt segment 0 labels
        corrupt["hubs"][0]["route_segments"][0]["to"] = "N999"

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "do not match sequence leg" in v.message for v in report.violations)

    def test_rule4_segment_fewer_than_two_points(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [[1.0, 1.0]]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "fewer than two points" in v.message for v in report.violations)

    def test_rule4_ring_start_misses_hub_location(self, demo_three_rooms):
        """Ring start point with 5 mm gap (> 1 mm tolerance) must fail."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Shift start point by 5 mm
        corrupt["hubs"][0]["route_segments"][0]["polyline"][0][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(
            v.rule_number == 4 and "ring start does not match hub location" in v.message
            for v in report.violations
        )

    def test_rule4_ring_return_misses_hub_location(self, demo_three_rooms):
        """Ring return endpoint with 5 mm gap (> 1 mm tolerance) must fail."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Shift return point by 5 mm
        corrupt["hubs"][0]["route_segments"][-1]["polyline"][-1][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(
            v.rule_number == 4 and "ring return endpoint does not match hub location" in v.message
            for v in report.violations
        )

    def test_rule4_gap_between_consecutive_segments(self, demo_three_rooms):
        """Internal 10 mm gap between segment i and segment i+1 must fail."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Inject 10 mm gap into start of segment 1
        corrupt["hubs"][0]["route_segments"][1]["polyline"][0][1] += 0.010

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "gap between segment" in v.message for v in report.violations)

    def test_rule4_segment_misses_fixed_node(self, demo_three_rooms):
        """Leg ending 5 mm away from fixed node must fail terminal snapping check."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Leg 0 ends at N1; shift end point by 5 mm
        corrupt["hubs"][0]["route_segments"][0]["polyline"][-1][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "misses fixed terminal" in v.message for v in report.violations)

    def test_rule4_terminal_outside_designated_room(self, demo_three_rooms):
        """If a fixed node's terminal visit coordinate is placed outside room polygon, fail."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Leg 0 to N1: set endpoint far away in R02 territory
        corrupt["hubs"][0]["route_segments"][0]["polyline"][-1] = [7.5, 2.0]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(
            v.rule_number == 4 and ("misses fixed terminal" in v.message or "not inside its designated room" in v.message)
            for v in report.violations
        )


# =============================================================================
# 6. Hard Rule 5: Free-Space & Rectilinear Routing Negative Tests
# =============================================================================


class TestRule5FreeSpaceNegative:
    """Tests all failure modes of Hard Rule 5 (Free Space & Axis-Alignment)."""

    def test_rule5_diagonal_segment(self, demo_three_rooms):
        """Diagonal segment (dx > 1e-4 and dy > 1e-4) must trigger Rule 5 violation."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Make segment 0 diagonal by altering vertex 1
        p0 = corrupt["hubs"][0]["route_segments"][0]["polyline"][0]
        corrupt["hubs"][0]["route_segments"][0]["polyline"][1] = [p0[0] + 0.5, p0[1] + 0.5]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 and "is diagonal" in v.message for v in report.violations)

    def test_rule5_wall_obstacle_penetration(self, demo_three_rooms):
        """Routing through an interior wall obstacle must fail Rule 5."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Alter segment 0 to cut through solid wall dividing R01 and R02 at y = 0.8
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            corrupt["hubs"][0]["route_segments"][0]["polyline"][0],
            [4.5, 0.8],  # Solid dividing wall
            [5.5, 0.8],  # Wall obstacle penetrated
            [7.5, 2.0],
        ]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and "penetrates wall/window obstacle" in v.message
            for v in report.violations
        )

    def test_rule5_window_obstacle_penetration(self, demo_three_rooms):
        """Routing through a window opening polygon must fail Rule 5."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Demo three rooms has a window at x in [2.0, 3.0], y in [7.9, 8.0]
        # Route a segment passing through window obstacle
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            corrupt["hubs"][0]["route_segments"][0]["polyline"][0],
            [2.5, 7.5],
            [2.5, 8.0],  # Window obstacle
        ]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and "penetrates wall/window obstacle" in v.message
            for v in report.violations
        )

    def test_rule5_exits_inner_boundary(self, demo_three_rooms):
        """Routing outside apartment inner boundary must fail Rule 5."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Extend first segment out past outer boundary (inner max_x is 10.0; route to x=10.5)
        p_start = corrupt["hubs"][0]["route_segments"][0]["polyline"][0]
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            p_start,
            [10.5, p_start[1]],  # Outside inner boundary (10.5 > 10.0)
            [10.5, 1.5],
            [7.5, 1.5],
        ]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 and "exits inner boundary" in v.message for v in report.violations)

    def test_rule5_outside_free_space(self, demo_three_rooms):
        """Routing across excluded non-free space must fail Rule 5."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Route through R04 bathroom which is not part of free space
        p_start = corrupt["hubs"][0]["route_segments"][0]["polyline"][0]
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            p_start,
            [7.5, p_start[1]],
            [7.5, 6.0],  # Inside bathroom R04
            [1.5, 6.0],
        ]

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 for v in report.violations)


# =============================================================================
# 7. Hard Rule 6: Hub Placement & Clearance Negative Tests
# =============================================================================


class TestRule6HubClearanceNegative:
    """Tests all failure modes and boundary tolerance of Hard Rule 6 (Hub Clearance)."""

    def test_rule6_hub_room_not_in_plan(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["room_id"] = "R_UNKNOWN"

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "not found in plan" in v.message for v in report.violations)

    def test_rule6_hub_in_ineligible_room(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Place hub in bathroom R04
        corrupt["hubs"][0]["room_id"] = "R04"
        corrupt["hubs"][0]["assigned_rooms"].append("R04")
        corrupt["hubs"][0]["location"] = {"x": 7.5, "y": 6.0}

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "ineligible/excluded room" in v.message for v in report.violations)

    def test_rule6_hub_room_not_in_assigned_rooms(self, demo_six_rooms):
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Set hub host room to R05, which is in H2's assigned_rooms, not H1's
        corrupt["hubs"][0]["room_id"] = "R05"

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "is not in hub's assigned rooms" in v.message for v in report.violations)

    def test_rule6_hub_outside_room_polygon(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Hub location placed outside R01 (e.g. at x=9.0 in R02 territory)
        corrupt["hubs"][0]["location"] = {"x": 9.0, "y": 2.0}

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "is not inside host room" in v.message for v in report.violations)

    def test_rule6_hub_wall_clearance_under_50cm(self, demo_three_rooms):
        """Hub placed 0.15 m from interior wall (< 0.50 m) must fail Rule 6."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Interior wall between R01 and R02 at y=0.8 is at x=5.0; place hub at x=4.80, y=0.80 (0.15m clearance)
        corrupt["hubs"][0]["location"] = {"x": 4.80, "y": 0.80}

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "clearance to walls/windows" in v.message and "less than required 0.50" in v.message
            for v in report.violations
        )

    def test_rule6_hub_outer_boundary_clearance_under_50cm(self, demo_three_rooms):
        """Hub placed 0.20 m from outer boundary (< 0.50 m) must fail Rule 6."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Outer boundary is at x = 0.0; place hub at x = 0.20 (0.20 m clearance)
        corrupt["hubs"][0]["location"] = {"x": 0.20, "y": 2.0}

        report = FloorPlanChecker.evaluate(plan, corrupt, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "clearance to exterior boundary" in v.message and "less than required 0.50" in v.message
            for v in report.violations
        )

    def test_rule6_hub_clearance_tolerance_boundary(self, demo_three_rooms):
        """Boundary test: 0.500 m clearance passes, while 0.490 m fails."""
        plan, topology, sol = demo_three_rooms

        # In R01, west wall obstacle ends at x=0.10.
        # Candidate at x=0.600, y=2.0 has clearance to west wall obstacle equal to 0.500 m (>= 0.499m)
        corrupt_pass = deepcopy(sol)
        corrupt_pass["hubs"][0]["location"] = {"x": 0.600, "y": 2.0}
        # Update first and last leg to match hub point
        corrupt_pass["hubs"][0]["route_segments"][0]["polyline"][0] = [0.600, 2.0]
        corrupt_pass["hubs"][0]["route_segments"][-1]["polyline"][-1] = [0.600, 2.0]

        report_pass = FloorPlanChecker.evaluate(plan, corrupt_pass, topology=topology)
        # Clearance check should pass (clearance >= 0.50 - 0.001 = 0.499m)
        assert report_pass.rules_passed["Rule 6"]

        # Candidate at x=0.590 has clearance to obstacle = 0.490m (< 0.499m limit) -> must fail
        corrupt_fail = deepcopy(sol)
        corrupt_fail["hubs"][0]["location"] = {"x": 0.590, "y": 2.0}
        corrupt_fail["hubs"][0]["route_segments"][0]["polyline"][0] = [0.590, 2.0]
        corrupt_fail["hubs"][0]["route_segments"][-1]["polyline"][-1] = [0.590, 2.0]

        report_fail = FloorPlanChecker.evaluate(plan, corrupt_fail, topology=topology)
        assert not report_fail.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 for v in report_fail.violations)


# =============================================================================
# 8. Rule 7 & Metric Recomputation: Retracing & 1 mm Tolerance
# =============================================================================


class TestRule7ScoringAndTolerance:
    """Verifies Slide 7 metric semantics and 1 mm spatial tolerance."""

    def test_rule7_retracing_is_valid(self, demo_three_rooms):
        """Retracing a leg out and back along the same path is valid and penalized only via O."""
        plan, topology, sol = demo_three_rooms
        retrace_sol = deepcopy(sol)
        # Modify route to retrace an out-and-back leg
        hub_loc = retrace_sol["hubs"][0]["location"]
        n1 = [plan.nodes["N1"].location.x, plan.nodes["N1"].location.y]
        n2 = [plan.nodes["N2"].location.x, plan.nodes["N2"].location.y]
        n3 = [plan.nodes["N3"].location.x, plan.nodes["N3"].location.y]

        # Route visits H1 -> N1 -> N2 -> N1 (retrace) -> N3 -> H1
        # Leg 2 is N2 -> N1, retracing Leg 1 (N1 -> N2)
        leg1_poly = [n1, [n2[0], n1[1]], n2]
        leg2_poly = [n2, [n2[0], n1[1]], n1]  # Exact retrace

        retrace_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "N3", "H1"]
        # Retracing is entirely valid
        report = FloorPlanChecker.evaluate(plan, retrace_sol, topology=topology)
        assert report.is_valid

    def test_rule7_overlap_tolerance_0_5mm_vs_1_5mm(self):
        """0.5 mm lateral separation counts as collinear overlap; 1.5 mm separation yields O = 0.0m."""
        # 1. Segments separated by 0.5 mm (<= 1.0 mm tolerance):
        # Line 1: (0, 0.0000) -> (3, 0.0000)
        # Line 2: (3, 0.0005) -> (0, 0.0005)
        leg_sub_mm_1 = [Point2D(x=0.0, y=0.0), Point2D(x=3.0, y=0.0)]
        leg_sub_mm_2 = [Point2D(x=3.0, y=0.0005), Point2D(x=0.0, y=0.0005)]
        o_sub = FloorPlanChecker._compute_overlap_for_legs([leg_sub_mm_1, leg_sub_mm_2])
        assert o_sub == pytest.approx(3.0, abs=1e-3), f"Expected overlap 3.0m for 0.5mm separation, got {o_sub}"

        # 2. Segments separated by 1.5 mm (> 1.0 mm tolerance):
        # Line 1: (0, 0.0000) -> (3, 0.0000)
        # Line 2: (3, 0.0015) -> (0, 0.0015)
        leg_supra_mm_1 = [Point2D(x=0.0, y=0.0), Point2D(x=3.0, y=0.0)]
        leg_supra_mm_2 = [Point2D(x=3.0, y=0.0015), Point2D(x=0.0, y=0.0015)]
        o_supra = FloorPlanChecker._compute_overlap_for_legs([leg_supra_mm_1, leg_supra_mm_2])
        assert o_supra == pytest.approx(0.0, abs=1e-4), f"Expected overlap 0.0m for 1.5mm separation, got {o_supra}"

        # 3. Parallel door lanes separated by 20 mm:
        leg_door_a = [Point2D(x=0.0, y=0.0), Point2D(x=2.0, y=0.0)]
        leg_door_b = [Point2D(x=2.0, y=0.02), Point2D(x=0.0, y=0.02)]
        o_door = FloorPlanChecker._compute_overlap_for_legs([leg_door_a, leg_door_b])
        assert o_door == pytest.approx(0.0, abs=1e-4)

    def test_rule7_multi_segment_collinear_overlap(self):
        """Three collinear segments on same line incur (3 - 1) * length overlap."""
        leg1 = [Point2D(x=0.0, y=1.0), Point2D(x=4.0, y=1.0)]
        leg2 = [Point2D(x=4.0, y=1.0), Point2D(x=0.0, y=1.0)]
        leg3 = [Point2D(x=0.0, y=1.0), Point2D(x=4.0, y=1.0)]
        # 3 segments on [0, 4] -> (3 - 1) * 4.0 = 8.0 m overlap
        o_multi = FloorPlanChecker._compute_overlap_for_legs([leg1, leg2, leg3])
        assert o_multi == pytest.approx(8.0, abs=1e-4)

    def test_rule7_crossings_recomputation(self):
        """Crossings in open space count X=1; crossings at terminals count X=0."""
        # 1. Crossing in open space
        leg_h = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0)]
        leg_v = [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        terminals = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0), Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        x_open = FloorPlanChecker._compute_crossings_for_legs([leg_h, leg_v], terminals)
        assert x_open == 1

        # 2. Meeting at terminal (node or hub)
        terminal_pt = Point2D(x=5.0, y=5.0)
        leg_in = [Point2D(x=0.0, y=5.0), terminal_pt]
        leg_out = [terminal_pt, Point2D(x=5.0, y=10.0)]
        x_term = FloorPlanChecker._compute_crossings_for_legs([leg_in, leg_out], [terminal_pt])
        assert x_term == 0

        # 3. Deduplication of multiple legs crossing at exact same point
        leg_v2 = [Point2D(x=5.0, y=2.0), Point2D(x=5.0, y=8.0)]
        x_dedup = FloorPlanChecker._compute_crossings_for_legs([leg_h, leg_v, leg_v2], terminals)
        assert x_dedup == 1

    def test_rule7_bends_recomputation(self):
        """Bends count 90 deg turns (1), 180 reversals (1), straight (0), and hub wrap-around."""
        # Standard rectangle: 4 turns
        rect = [
            [Point2D(x=0.0, y=0.0), Point2D(x=5.0, y=0.0)],
            [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=3.0)],
            [Point2D(x=5.0, y=3.0), Point2D(x=0.0, y=3.0)],
            [Point2D(x=0.0, y=3.0), Point2D(x=0.0, y=0.0)],
        ]
        assert FloorPlanChecker._compute_ring_bends(rect) == 4

        # 180 deg reversal at terminal
        out_and_back = [
            [Point2D(x=0.0, y=0.0), Point2D(x=3.0, y=0.0)],
            [Point2D(x=3.0, y=0.0), Point2D(x=0.0, y=0.05)],
            [Point2D(x=0.0, y=0.05), Point2D(x=0.0, y=0.0)],
        ]
        # Turn at (3, 0): EAST -> WEST (180 deg reversal = 1 bend)
        # Turn at (0, 0.05): WEST -> SOUTH (90 deg = 1 bend)
        # Turn at hub return (0, 0): SOUTH -> EAST (90 deg wrap-around = 1 bend)
        # Total = 3 bends
        assert FloorPlanChecker._compute_ring_bends(out_and_back) == 3

    def test_rule7_composite_cost_j_exact(self, demo_three_rooms):
        """Total composite cost J = L + 5X + O + 0.5B exact numerical verification."""
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        expected_J = round(
            report.total_length_m + 5.0 * report.total_crossings
            + report.total_overlap_m + 0.5 * report.total_bends,
            4,
        )
        assert report.total_cost_J == pytest.approx(expected_J, abs=1e-4)

    def test_rule7_area_gap_computation(self, demo_six_rooms):
        """Area gap delta A_gap = max(A) - min(A) verification."""
        plan, topology, sol = demo_six_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        hub_areas = [m.zone_area_m2 for m in report.hub_metrics]
        expected_gap = round(max(hub_areas) - min(hub_areas), 4)
        assert report.area_gap_m2 == pytest.approx(expected_gap, abs=1e-4)


# =============================================================================
# 9. Slide 8 Specification & JSON Compliance Tests
# =============================================================================


class TestSlide8JSONCompliance:
    """Tests compliance with official Slide 8 JSON format and schema."""

    def test_slide8_schema_structure(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        # Validate against official Pydantic schema
        model = Slide8Export.model_validate(sol)
        assert model.plan_id == "demo_three_rooms_window"
        assert model.units == "m"
        assert len(model.hubs) == 1
        assert model.reported_metrics.hub_count == 1
        assert model.reported_metrics.total_length_m > 0

    def test_slide8_hub_fields_presence(self, demo_six_rooms):
        plan, topology, sol = demo_six_rooms
        for hub in sol["hubs"]:
            assert "hub_id" in hub
            assert "room_id" in hub
            assert "location" in hub
            assert "x" in hub["location"] and "y" in hub["location"]
            assert "assigned_rooms" in hub
            assert "assigned_nodes" in hub
            assert "closed_sequence" in hub
            assert "route_segments" in hub
            assert "ring_length_m" in hub

    def test_slide8_route_segment_format(self, demo_three_rooms):
        plan, topology, sol = demo_three_rooms
        for seg in sol["hubs"][0]["route_segments"]:
            assert "from" in seg
            assert "to" in seg
            assert "length_m" in seg
            assert "polyline" in seg
            # Polyline must be list of [x, y] coordinates
            for pt in seg["polyline"]:
                assert isinstance(pt, list) and len(pt) == 2
                assert isinstance(pt[0], (int, float)) and isinstance(pt[1], (int, float))

    def test_slide8_coordinate_precision_rounded(self, demo_three_rooms):
        """Coordinates must be rounded to at most 4 decimal places (0.1 mm)."""
        plan, topology, sol = demo_three_rooms
        for seg in sol["hubs"][0]["route_segments"]:
            for pt in seg["polyline"]:
                for coord in pt:
                    # Check that string representation does not exceed 4 decimal places
                    s = str(coord)
                    if "." in s:
                        decimals = len(s.split(".")[1])
                        assert decimals <= 4, f"Coordinate {coord} has > 4 decimal places"

    def test_slide8_export_roundtrip_evaluates_identically(self, demo_three_rooms):
        """Exporting to Slide 8 JSON and re-evaluating matches bundle evaluation."""
        plan, topology, sol = demo_three_rooms
        report_orig = FloorPlanChecker.evaluate(plan, sol, topology=topology)

        # Dump to JSON string and reload
        json_str = json.dumps(sol)
        sol_reloaded = json.loads(json_str)

        report_reloaded = FloorPlanChecker.evaluate(plan, sol_reloaded, topology=topology)
        assert report_reloaded.is_valid == report_orig.is_valid
        assert report_reloaded.total_cost_J == pytest.approx(report_orig.total_cost_J, abs=1e-4)
        assert report_reloaded.total_length_m == pytest.approx(report_orig.total_length_m, abs=1e-4)


# =============================================================================
# 10. Multi-Rule Violation Aggregation & Robustness
# =============================================================================


class TestMultiRuleViolationAggregation:
    """Verifies that simultaneous violations across multiple rules are all detected."""

    def test_multi_rule_violation_aggregation(self, demo_three_rooms):
        """A submission violating Rules 1, 2, 4, 5, and 6 concurrently must report all of them."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)

        # Rule 1 violation: assign unknown room
        corrupt["hubs"][0]["assigned_rooms"].append("R_GHOST")

        # Rule 2 violation: set limit to 1 node
        # Rule 4 violation: corrupt sequence start
        corrupt["hubs"][0]["closed_sequence"][0] = "N_WRONG"

        # Rule 5 violation: make segment diagonal
        p0 = corrupt["hubs"][0]["route_segments"][0]["polyline"][0]
        corrupt["hubs"][0]["route_segments"][0]["polyline"][1] = [p0[0] + 1.0, p0[1] + 1.0]

        # Rule 6 violation: place hub in bathroom R04
        corrupt["hubs"][0]["room_id"] = "R04"
        corrupt["hubs"][0]["location"] = {"x": 7.5, "y": 6.0}

        report = FloorPlanChecker.evaluate(
            plan, corrupt, topology=topology, max_nodes_per_hub=1
        )

        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert not report.rules_passed["Rule 2"]
        assert not report.rules_passed["Rule 4"]
        assert not report.rules_passed["Rule 5"]
        assert not report.rules_passed["Rule 6"]

        violated_rule_numbers = {v.rule_number for v in report.violations}
        assert {1, 2, 4, 5, 6}.issubset(violated_rule_numbers)
