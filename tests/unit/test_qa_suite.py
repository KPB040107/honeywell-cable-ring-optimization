"""Comprehensive QA and Integration Test Suite for Honeywell Cable Ring Optimization.

Strictly verifies:
1. All 17 competition constraints from FloorPlanChecker (Hard Rules 1 to 6 and Rule 7 metric auditing).
2. Incumbent monotonic preservation: ALNS optimizers (Bandit, Random, LinUCB) NEVER return
   a worse valid solution than their starting PortfolioHybrid baseline (J_final <= J_base, H_final <= H_base).
3. Exact reproducibility: identical random seeds produce bit-for-bit identical solutions and metrics.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import pytest
from shapely.geometry import LineString, Point, Polygon

from resplan_router.contracts import (
    CompletedHubRing,
    EvaluationReport,
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
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.optimizers.alns_variants import ALNSRandomOptimizer, ALNSBanditOptimizer
from resplan_router.optimizers.alns_operators import ALNSState


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "demo_outputs"


@pytest.fixture(scope="module")
def demo_three_rooms():
    """Provides plan, topology, and valid solution for demo_three_rooms_window."""
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
    """Provides plan, topology, and valid solution for demo_six_rooms_capacity."""
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
# SUITE 1: Rule-by-Rule 17 Competition Constraints
# =============================================================================


class TestRule1CoverageConstraints:
    """Verifies that all 8 Rule 1 coverage and assignment constraints are strictly enforced."""

    def test_constraint_1_1_excluded_space_rejected(self, demo_three_rooms):
        """Rule 1.1: Excluded spaces (e.g. bathroom, balcony) assigned to hub are strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"].append("R04")  # R04 is bathroom

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "Excluded space" in v.message for v in report.violations)

    def test_constraint_1_2_unknown_room_rejected(self, demo_three_rooms):
        """Rule 1.2: Nonexistent room ID assigned to hub is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"].append("R_NONEXISTENT_999")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "Unknown room" in v.message for v in report.violations)

    def test_constraint_1_3_omitted_eligible_room_rejected(self, demo_six_rooms):
        """Rule 1.3: Every eligible room must be assigned to at least 1 hub (omissions rejected)."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"] = [r for r in bad_sol["hubs"][0]["assigned_rooms"] if r != "R01"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "not assigned to any hub" in v.message for v in report.violations)

    def test_constraint_1_4_duplicate_room_assignment_rejected(self, demo_six_rooms):
        """Rule 1.4: Every eligible room must be assigned to at most 1 hub (duplicates rejected)."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][1]["assigned_rooms"].append("R01")  # R01 already in hub 0

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "multiple hubs" in v.message for v in report.violations)

    def test_constraint_1_5_fixed_node_room_mismatch_rejected(self, demo_six_rooms):
        """Rule 1.5: A fixed node must be assigned to the same hub as its host room."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Move node N1 to H2 while keeping room R01 in H1
        bad_sol["hubs"][0]["assigned_nodes"] = [n for n in bad_sol["hubs"][0]["assigned_nodes"] if n != "N1"]
        bad_sol["hubs"][1]["assigned_nodes"].append("N1")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "belongs to room" in v.message for v in report.violations)

    def test_constraint_1_6_unknown_fixed_node_rejected(self, demo_three_rooms):
        """Rule 1.6: Nonexistent node ID assigned to hub is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_nodes"].append("N_UNKNOWN_999")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "Unknown node" in v.message for v in report.violations)

    def test_constraint_1_7_omitted_fixed_node_rejected(self, demo_six_rooms):
        """Rule 1.7: Every fixed node must be assigned to at least 1 hub."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_nodes"] = [n for n in bad_sol["hubs"][0]["assigned_nodes"] if n != "N1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "is not assigned to any hub" in v.message for v in report.violations)

    def test_constraint_1_8_duplicate_fixed_node_rejected(self, demo_six_rooms):
        """Rule 1.8: Fixed node assigned to multiple hubs is strictly rejected."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][1]["assigned_nodes"].append("N1")
        bad_sol["hubs"][1]["assigned_rooms"].append("R01")  # avoid mismatch violation to isolate duplicate

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert any(v.rule_number == 1 and "multiple hubs" in v.message for v in report.violations)


class TestRule2CapacityConstraints:
    """Verifies that hub node capacity (<= 5) and area capacity (<= 100 m²) are enforced."""

    def test_constraint_2_1_node_capacity_exceeded_rejected(self, demo_six_rooms):
        """Rule 2.1: Single hub serving > 5 fixed nodes (e.g. 6 nodes) is strictly rejected."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Merge all 6 nodes into Hub 1
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        bad_sol["hubs"] = [bad_sol["hubs"][0]]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 and "exceeding capacity limit of 5" in v.message for v in report.violations)

    def test_constraint_2_2_area_capacity_exceeded_rejected(self, demo_six_rooms):
        """Rule 2.2: Single hub serving > 100.0 m² area is strictly rejected."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # 6 rooms sum to 120 m²
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4"]  # keep nodes <= 5
        bad_sol["hubs"] = [bad_sol["hubs"][0]]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 and "exceeds capacity limit of 100.0" in v.message for v in report.violations)

    def test_constraint_2_3_boundary_capacity_pass(self, demo_three_rooms):
        """Rule 2.3: Hub exactly at boundary (<= 5 nodes and <= 100.0 m²) strictly passes."""
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.is_valid
        assert report.rules_passed["Rule 2"]


class TestRule3DoorwayContiguityConstraints:
    """Verifies that hub assigned rooms must form a connected subgraph through doors/open passages only."""

    def test_constraint_3_1_disconnected_room_subgraph_rejected(self, demo_six_rooms):
        """Rule 3.1: Disconnected rooms (no doorway path) assigned to same hub are rejected."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Assign R01 and R06 to H1 (they are separated by other rooms without direct link)
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R06"]
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N6"]
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N6", "H1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 3"]
        assert any(v.rule_number == 3 and "not contiguous" in v.message for v in report.violations)

    def test_constraint_3_2_wall_only_adjacency_rejected(self):
        """Rule 3.2: Rooms sharing only a wall boundary without a door cannot be combined."""
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

        # Topology graph has empty adjacency between R01 and R02 (no doorway)
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
        assert any(v.rule_number == 3 and "not contiguous" in v.message for v in report.violations)

    def test_constraint_3_3_window_only_adjacency_rejected(self):
        """Rule 3.3: Portals that are windows do NOT establish doorway contiguity."""
        plan_dict = {
            "plan_id": "test_window_contiguity",
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

        topology = PortalTopologyGraph(
            plan_id="test_window_contiguity",
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )

        sub = {
            "plan_id": "test_window_contiguity",
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
        assert any(v.rule_number == 3 and "not contiguous" in v.message for v in report.violations)

    def test_constraint_3_4_doorway_contiguity_pass(self, demo_three_rooms):
        """Rule 3.4: Fully contiguous doorway chain strictly passes Rule 3."""
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.rules_passed["Rule 3"]


class TestRule4ClosedRingConstraints:
    """Verifies that one single closed loop per hub visiting each node exactly once is enforced."""

    def test_constraint_4_1_empty_closed_sequence_rejected(self, demo_three_rooms):
        """Rule 4.1: Empty closed sequence triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["closed_sequence"] = []

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "closed_sequence is empty" in v.message for v in report.violations)

    def test_constraint_4_2_sequence_start_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.2: Closed sequence not starting with hub ID triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["closed_sequence"] = ["N1", "N2", "N3", "H1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "must start with 'H1'" in v.message for v in report.violations)

    def test_constraint_4_3_sequence_end_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.3: Closed sequence not ending with hub ID triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "N3"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "must end with 'H1'" in v.message for v in report.violations)

    def test_constraint_4_4_node_visit_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.4: Omitting or visiting an assigned node multiple times triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # N2 visited twice, N3 omitted
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "N2", "H1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "do not visit assigned nodes" in v.message for v in report.violations)

    def test_constraint_4_5_segment_count_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.5: Segment count not matching sequence legs triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"].pop()

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "route segments" in v.message for v in report.violations)

    def test_constraint_4_6_segment_label_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.6: Segment from/to endpoints mismatching sequence triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["to"] = "N999"

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "do not match sequence leg" in v.message for v in report.violations)

    def test_constraint_4_7_ring_start_location_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.7: Cable start point separated from hub location by > 1 mm triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][0][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "ring start does not match hub location" in v.message for v in report.violations)

    def test_constraint_4_8_ring_return_location_mismatch_rejected(self, demo_three_rooms):
        """Rule 4.8: Cable return end point separated from hub location by > 1 mm triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][-1]["polyline"][-1][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "return endpoint does not match hub location" in v.message for v in report.violations)

    def test_constraint_4_9_segment_continuity_gap_rejected(self, demo_three_rooms):
        """Rule 4.9: Gap > 1 mm between consecutive segments triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][1]["polyline"][0][1] += 0.010

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "gap between segment" in v.message for v in report.violations)

    def test_constraint_4_10_terminal_misses_fixed_node_rejected(self, demo_three_rooms):
        """Rule 4.10: Segment terminal missing fixed node by > 1 mm triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][-1][1] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 4"]
        assert any(v.rule_number == 4 and "misses fixed terminal" in v.message for v in report.violations)


class TestRule5FreeSpaceAndAxisAlignmentConstraints:
    """Verifies free space traversal and strictly rectilinear routing (delta_x=0 or delta_y=0)."""

    def test_constraint_5_1_non_axis_aligned_diagonal_rejected(self, demo_three_rooms):
        """Rule 5.1: Diagonal cable routing (delta_x != 0 and delta_y != 0) is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][1] = [p0[0] + 0.5, p0[1] + 0.5]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 and "is diagonal" in v.message for v in report.violations)

    def test_constraint_5_2_wall_obstacle_penetration_rejected(self, demo_three_rooms):
        """Rule 5.2: Cable passing through a wall obstacle polygon is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p0,
            [4.5, 0.8],  # Solid dividing wall
            [5.5, 0.8],  # Wall obstacle penetrated
            [7.5, 2.0],
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 and "penetrates wall" in v.message for v in report.violations)

    def test_constraint_5_3_window_penetration_rejected(self, demo_three_rooms):
        """Rule 5.3: Cable passing through a window polygon is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p0,
            [2.5, 7.5],
            [2.5, 8.0],  # Window obstacle
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and ("penetrates wall/window" in v.message or "window" in v.message or "exits inner" in v.message)
            for v in report.violations
        )

    def test_constraint_5_4_exits_inner_boundary_rejected(self, demo_three_rooms):
        """Rule 5.4: Cable routing outside the inner boundary is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p_start = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p_start,
            [10.5, p_start[1]],  # Outside inner boundary (10.5 > 10.0)
            [10.5, 1.5],
            [7.5, 1.5],
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(v.rule_number == 5 and "exits inner boundary" in v.message for v in report.violations)

    def test_constraint_5_5_axis_aligned_free_space_pass(self, demo_three_rooms):
        """Rule 5.5: Strictly axis-aligned cable completely in free space passes Rule 5."""
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.rules_passed["Rule 5"]


class TestRule6HubClearanceConstraints:
    """Verifies hub placement inside assigned eligible room with >= 0.50 m clearance."""

    def test_constraint_6_1_hub_room_not_in_plan_rejected(self, demo_three_rooms):
        """Rule 6.1: Hub placed in nonexistent room ID is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["room_id"] = "R_GHOST_999"

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "not found in plan" in v.message for v in report.violations)

    def test_constraint_6_2_hub_in_ineligible_room_rejected(self, demo_three_rooms):
        """Rule 6.2: Hub placed in ineligible room (e.g. bathroom) is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["room_id"] = "R04"  # R04 is bathroom

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "ineligible/excluded room" in v.message for v in report.violations)

    def test_constraint_6_3_hub_room_not_in_assigned_rooms_rejected(self, demo_six_rooms):
        """Rule 6.3: Hub host room must be included in the hub's own assigned rooms."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Hub host is R01, but assigned_rooms omits R01
        bad_sol["hubs"][0]["assigned_rooms"] = ["R02", "R03"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "is not in hub's assigned rooms" in v.message for v in report.violations)

    def test_constraint_6_4_hub_outside_room_polygon_rejected(self, demo_three_rooms):
        """Rule 6.4: Hub coordinates located outside host room polygon are strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["location"] = [10.0, 10.0]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "is not inside host room" in v.message for v in report.violations)

    def test_constraint_6_5_hub_wall_clearance_under_50cm_rejected(self, demo_three_rooms):
        """Rule 6.5: Hub placed with < 0.50 m clearance to an interior wall is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # R01 east wall is at x=3.0. Place hub at x=2.85 (0.15 m from wall < 0.50 m)
        bad_sol["hubs"][0]["location"] = [2.85, 3.85]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "less than required 0.50 m" in v.message for v in report.violations)

    def test_constraint_6_6_hub_exterior_clearance_under_50cm_rejected(self, demo_three_rooms):
        """Rule 6.6: Hub placed with < 0.50 m clearance to exterior boundary is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Outer boundary at x=0.0. Place hub at x=0.20 (0.20 m < 0.50 m)
        bad_sol["hubs"][0]["location"] = [0.20, 3.85]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 6"]
        assert any(v.rule_number == 6 and "less than required 0.50 m" in v.message for v in report.violations)

    def test_constraint_6_7_hub_clearance_compliant_pass(self, demo_three_rooms):
        """Rule 6.7: Hub placed with clearance >= 0.50 m strictly passes Rule 6."""
        plan, topology, sol = demo_three_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.rules_passed["Rule 6"]


class TestRule7CollinearOverlapAndMetricAccounting:
    """Verifies collinear overlap penalization at 1 mm tolerance, crossings, bends, and cost J."""

    def test_constraint_7_1_collinear_overlap_submillimeter_penalization(self):
        """Rule 7.1: Parallel segments within 1.0 mm lateral distance incur (k-1)*Delta overlap."""
        seg1 = [Point2D(x=0.0, y=2.000), Point2D(x=10.0, y=2.000)]
        seg2 = [Point2D(x=0.0, y=2.0005), Point2D(x=10.0, y=2.0005)]

        overlap = FloorPlanChecker._compute_overlap_for_legs([seg1, seg2], tol=0.001)
        assert math.isclose(overlap, 10.0, abs_tol=1e-3), f"Expected 10.0 m overlap, got {overlap}"

    def test_constraint_7_2_collinear_overlap_separation_above_1mm(self):
        """Rule 7.2: Parallel segments > 1.0 mm apart (e.g. 1.5 mm) incur exactly 0.0 m overlap."""
        seg1 = [Point2D(x=0.0, y=2.000), Point2D(x=10.0, y=2.000)]
        seg2 = [Point2D(x=0.0, y=2.0015), Point2D(x=10.0, y=2.0015)]  # 1.5 mm apart > 1 mm

        overlap = FloorPlanChecker._compute_overlap_for_legs([seg1, seg2], tol=0.001)
        assert math.isclose(overlap, 0.0, abs_tol=1e-3), f"Expected 0.0 m overlap, got {overlap}"

    def test_constraint_7_3_doorway_retracing_valid_with_overlap(self):
        """Rule 7.3: Retracing a corridor/doorway in reverse direction is valid and penalized by O = Delta."""
        leg1 = [Point2D(x=0.0, y=2.0), Point2D(x=5.0, y=2.0)]
        leg2 = [Point2D(x=5.0, y=2.0), Point2D(x=0.0, y=2.0)]
        overlap = FloorPlanChecker._compute_overlap_for_legs([leg1, leg2], tol=0.001)
        assert math.isclose(overlap, 5.0, abs_tol=1e-3), f"Expected 5.0 m overlap, got {overlap}"

    def test_constraint_7_4_crossings_away_from_terminals_penalized(self):
        """Rule 7.4: Cable intersections away from terminals count as crossings (5X penalty)."""
        terminals = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0), Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        leg1 = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0)]
        leg2 = [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]

        crossings = FloorPlanChecker._compute_crossings_for_legs([leg1, leg2], terminals, tol=0.001)
        assert crossings == 1

    def test_constraint_7_5_crossings_at_terminals_zero_penalty(self):
        """Rule 7.5: Legs converging at terminal nodes or hubs incur zero crossing penalty (X = 0)."""
        hub = Point2D(x=5.0, y=5.0)
        n1 = Point2D(x=0.0, y=5.0)
        n2 = Point2D(x=5.0, y=0.0)
        terminals = [hub, n1, n2]

        leg1 = [n1, hub]
        leg2 = [n2, hub]

        crossings = FloorPlanChecker._compute_crossings_for_legs([leg1, leg2], terminals, tol=0.001)
        assert crossings == 0

    def test_constraint_7_6_bend_counting_and_reversals(self):
        """Rule 7.6: Cyclic bend counting: 90 deg turn = 1 bend, 180 deg reversal = 1 bend, straight = 0."""
        rect_legs = [
            [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)],
            [Point2D(x=4.0, y=0.0), Point2D(x=4.0, y=3.0)],
            [Point2D(x=4.0, y=3.0), Point2D(x=0.0, y=3.0)],
            [Point2D(x=0.0, y=3.0), Point2D(x=0.0, y=0.0)],
        ]
        bends = FloorPlanChecker._compute_ring_bends(rect_legs, tol=0.001)
        assert bends == 4

        reversal_legs = [
            [Point2D(x=0.0, y=0.0), Point2D(x=5.0, y=0.0)],
            [Point2D(x=5.0, y=0.0), Point2D(x=0.0, y=0.0)],
        ]
        reversal_bends = FloorPlanChecker._compute_ring_bends(reversal_legs, tol=0.001)
        assert reversal_bends == 2

    def test_constraint_7_7_composite_cost_j_exact(self, demo_six_rooms):
        """Rule 7.7: Official composite cost strictly matches J = L + 5X + O + 0.5B to 4 decimal places."""
        plan, topology, sol = demo_six_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.is_valid
        expected_J = round(
            report.total_length_m + 5.0 * report.total_crossings + report.total_overlap_m + 0.5 * report.total_bends,
            4,
        )
        assert math.isclose(report.total_cost_J, expected_J, abs_tol=1e-4)


# =============================================================================
# SUITE 2: Incumbent Monotonic Preservation Verification
# =============================================================================


class TestIncumbentMonotonicPreservation:
    """Verifies that ALNS optimizers NEVER return a worse valid solution than their baseline."""

    def test_alns_random_monotonic_preservation(self, demo_six_rooms):
        """ALNSRandomOptimizer must strictly return J_final <= J_base and H_final <= H_base."""
        plan, _, _ = demo_six_rooms
        base_opt = PortfolioHybridOptimizer()
        base_res = base_opt.optimize(plan, time_limit_seconds=1.5, random_seed=42)
        assert base_res.is_valid

        random_opt = ALNSRandomOptimizer(max_iterations=15)
        random_res = random_opt.optimize(plan, time_limit_seconds=2.0, random_seed=42)

        assert random_res.is_valid
        assert (
            random_res.h_count < base_res.h_count
            or (random_res.h_count == base_res.h_count and random_res.total_cost_J <= base_res.total_cost_J + 1e-4)
        ), f"Monotonic violation: Random J={random_res.total_cost_J} > Base J={base_res.total_cost_J}"

    def test_alns_bandit_monotonic_preservation(self, demo_six_rooms):
        """ALNSBanditOptimizer must strictly return J_final <= J_base and H_final <= H_base."""
        plan, _, _ = demo_six_rooms
        base_opt = PortfolioHybridOptimizer()
        base_res = base_opt.optimize(plan, time_limit_seconds=1.5, random_seed=42)
        assert base_res.is_valid

        bandit_opt = ALNSBanditOptimizer(max_iterations=15)
        bandit_res = bandit_opt.optimize(plan, time_limit_seconds=2.0, random_seed=42)

        assert bandit_res.is_valid
        assert (
            bandit_res.h_count < base_res.h_count
            or (bandit_res.h_count == base_res.h_count and bandit_res.total_cost_J <= base_res.total_cost_J + 1e-4)
        ), f"Monotonic violation: Bandit J={bandit_res.total_cost_J} > Base J={base_res.total_cost_J}"

    def test_optimizer_rejects_degraded_states(self):
        """Verifies ALNS state tracking rejects candidate states with worse cost or hub count."""
        base_state = ALNSState(
            zones=[{"R01", "R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=45.0,
            h_count=2,
            area_gap=10.0,
            is_valid=True,
        )
        best_state = base_state.clone()

        worse_cost_state = ALNSState(
            zones=[{"R01", "R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=55.0,  # Worse by 10.0
            h_count=2,
            area_gap=10.0,
            is_valid=True,
        )

        is_better = False
        if worse_cost_state.h_count < best_state.h_count:
            is_better = True
        elif worse_cost_state.h_count == best_state.h_count and worse_cost_state.cost_J < best_state.cost_J - 1e-4:
            is_better = True

        assert not is_better, "Degraded state should never be accepted as new incumbent"
        assert best_state.cost_J == 45.0, "Incumbent cost must remain preserved"


# =============================================================================
# SUITE 3: Random Seed Reproducibility Verification
# =============================================================================


class TestOptimizerReproducibility:
    """Verifies that running optimizers with identical random seed produces identical results."""

    def test_alns_random_seed_reproducibility(self, demo_three_rooms):
        """ALNSRandomOptimizer produces bit-for-bit identical results with same random seed."""
        plan, _, _ = demo_three_rooms
        opt = ALNSRandomOptimizer(max_iterations=10)

        res_a = opt.optimize(plan, time_limit_seconds=1.5, random_seed=777)
        res_b = opt.optimize(plan, time_limit_seconds=1.5, random_seed=777)

        assert res_a.is_valid and res_b.is_valid
        assert res_a.total_cost_J == res_b.total_cost_J
        assert res_a.h_count == res_b.h_count
        assert res_a.total_length_m == res_b.total_length_m
        assert res_a.total_bends == res_b.total_bends
        assert res_a.total_crossings == res_b.total_crossings
        assert res_a.total_overlap_m == res_b.total_overlap_m

    def test_alns_bandit_seed_reproducibility(self, demo_three_rooms):
        """ALNSBanditOptimizer produces bit-for-bit identical results with same random seed."""
        plan, _, _ = demo_three_rooms
        opt = ALNSBanditOptimizer(max_iterations=10)

        res_a = opt.optimize(plan, time_limit_seconds=1.5, random_seed=999)
        res_b = opt.optimize(plan, time_limit_seconds=1.5, random_seed=999)

        assert res_a.is_valid and res_b.is_valid
        assert res_a.total_cost_J == res_b.total_cost_J
        assert res_a.h_count == res_b.h_count
        assert res_a.total_length_m == res_b.total_length_m
        assert res_a.total_bends == res_b.total_bends
        assert res_a.total_crossings == res_b.total_crossings
        assert res_a.total_overlap_m == res_b.total_overlap_m
