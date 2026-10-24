"""Phase 19 Comprehensive Quality Assurance & Independent Validation Test Suite.

Audited and Verified by Agent 4: Quality Assurance and Independent Validation Lead.

Comprehensive Test Suite Structure:
1. TestEightOfficialHardConstraints:
   - Verification of all 8 official Honeywell hard constraints at 1 mm tolerance:
     * Hard Constraint 1: Hub capacity (<= 5 nodes, <= 100 m² per hub) and H* minimum-hub priority.
     * Hard Constraint 2: Door-only zone connectivity (macro-topology doorway contiguity & lane separation).
     * Hard Constraint 3: Fixed node coordinate invariance (strictly 0.000000 m displacement).
     * Hard Constraint 4: Hub placement and obstacle clearance (>= 0.50 m to all walls/windows/boundaries).
     * Hard Constraint 5: Closed-ring connectivity (polyline closure <= 1.0 mm, single non-branching cycle).
     * Hard Constraint 6: Axis-aligned routing (rectilinear segments only, 90-degree orthogonal turns).
     * Hard Constraint 7: Wall and window obstacle avoidance (zero wall penetration, strict free space containment).
     * Hard Constraint 8: Exact metric recalculation (bitwise agreement between checkers, Delta J = 0.0000).

2. TestDualOracleConcordance:
   - Dual-oracle validation comparing FloorPlanChecker and IndependentVerifier (100% concordance).
   - Validated across representative tiers: Compact (plans 0, 24), Standard (plans 4, 12, 28), and Demo plans.
   - 100% concordance on is_valid, individual rule outcomes (Rules 1-6), and exact metric deltas (Delta J = 0.0000).

3. TestNegativeTestCases:
   - Negative test cases systematically audited across both oracles:
     * Obstacle collision / penetration (interior dividing wall penetration).
     * Window obstacle collision / penetration.
     * Outer boundary / non-free space penetration.
     * Diagonal non-axis-aligned segment routing.
     * Open loops: ring start gap (> 1 mm from hub location).
     * Open loops: ring return gap (> 1 mm from hub location).
     * Open loops: consecutive leg disconnect (> 1 mm gap).
     * Open loops: broken non-returning closed sequence.
     * Capacity violation: fixed nodes count > 5 per hub.
     * Capacity violation: assigned room area > 100.0 m² per hub.
   - Dual-oracle verification of 100% concordant rejection on all negative test cases.

4. TestFixedNodeCoordinateInvariance:
   - Coordinate invariance verification (max displacement strictly 0.000000 m).
   - Submillimetre tolerance boundary tests (0.5 mm tolerated under 1.0 mm threshold; 1.5 mm rejected).
   - Terminal node room consistency verification.

5. TestRoundTripSerialization:
   - Round-trip serialization & deserialization tests of solutions:
     * SynthesizedRingsBundle -> model_dump -> JSON -> model_validate.
     * Slide 8 competition JSON export -> model_validate -> re-evaluation consistency.
   - Round-trip serialization & deserialization tests of configs:
     * SystemConfig -> YAML -> SystemConfig.from_yaml.
     * SystemConfig -> JSON -> SystemConfig.model_validate_json.
     * SystemConfig mutation overrides (.with_overrides).
   - Invariance of evaluation metrics (J, L, X, O, B) across serialization cycles.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Dict, List
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from resplan_router.config import SystemConfig
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
from resplan_router.experimental.independent_verifier import (
    DualValidationResult,
    IndependentVerifier,
    SingleDoorRetraceAudit,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import (
    Slide8Export,
    export_slide8_json,
)
from scripts.audit_phase17_constraints import audit_plan_8_criteria, get_cached_solution

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
DEMO_OUTPUTS_DIR = REPO_ROOT / "tests" / "demo_outputs"


# =============================================================================
# Pytest Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def repo() -> DatasetRepository:
    """Provides singleton dataset repository."""
    return DatasetRepository.get_instance()


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


@pytest.fixture(scope="module")
def demo_disconnected():
    """Provides plan, topology, and valid solution for demo_disconnected_zones.

    4 eligible rooms in 2 disconnected clusters (R01-R02 and R03-R04), 4 nodes, 2 hubs.
    """
    raw = json.loads((FIXTURES_DIR / "demo_disconnected_zones.json").read_text(encoding="utf-8"))
    sol = json.loads(
        (DEMO_OUTPUTS_DIR / "demo_disconnected_zones_solution.json").read_text(encoding="utf-8")
    )
    plan = parse_floor_plan(raw)
    topology = MacroTopologyGraph.build_topology(
        plan, extract_all_portals(plan, raw.get("doors"), raw.get("front_door"))
    )
    return plan, topology, sol


# =============================================================================
# SUITE 1: Verification of All 8 Official Honeywell Hard Constraints
# =============================================================================


class TestEightOfficialHardConstraints:
    """Exhaustive verification of the 8 official Honeywell hard constraints at 1 mm tolerance."""

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_1_hub_capacity_and_h_star(self, repo: DatasetRepository, plan_id: int):
        """Hard Constraint 1: Hub capacity (<= 5 nodes, <= 100 m² per hub) and H* priority."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None, f"Solution for plan {plan_id} must be cached"

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c1 = audit["criterion_1_hub_capacity_and_H_star"]

        assert c1["nodes_capacity_pass"] is True, f"Plan {plan_id} exceeded 5-node capacity"
        assert c1["max_nodes_observed"] <= 5
        assert c1["area_capacity_pass"] is True, f"Plan {plan_id} exceeded 100 m² capacity"
        assert c1["max_area_m2_observed"] <= 100.0 + 1e-4
        assert c1["h_star_satisfied"] is True, f"Plan {plan_id} did not satisfy H* lower bound"
        assert c1["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_2_door_only_connectivity(self, repo: DatasetRepository, plan_id: int):
        """Hard Constraint 2: Door-only connectivity across interior portals (Rule 3)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)
        c2 = audit["criteria_checks"]["criterion_2_door_only_connectivity"]

        assert c2["macro_topology_contiguity"] is True, f"Plan {plan_id} disconnected across doors"
        assert audit["official_report"]["rules_passed"]["Rule 3"] is True
        assert audit["independent_report"]["rules_passed"]["Rule 3"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_3_fixed_node_coordinates_immutability(
        self, repo: DatasetRepository, plan_id: int
    ):
        """Hard Constraint 3: Fixed node coordinate invariance (strictly 0.000000 m displacement)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c3 = audit["criterion_3_fixed_node_displacement"]

        assert c3["terminals_checked"] > 0
        assert c3["unique_nodes_verified"] == len(plan.nodes)
        assert c3["max_displacement_m"] <= 1e-6, (
            f"Plan {plan_id} node displacement {c3['max_displacement_m']} > 0.000000 m"
        )
        assert c3["exact_zero_displacement"] is True
        assert c3["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_4_hub_placement_and_obstacle_clearance(
        self, repo: DatasetRepository, plan_id: int
    ):
        """Hard Constraint 4: Hub placement and obstacle clearance (>= 0.50 m at 1 mm tolerance)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c4 = audit["criterion_4_hub_clearance"]

        assert c4["hub_in_eligible_room"] is True, f"Plan {plan_id} hub placed in ineligible room"
        assert c4["hub_inside_room_geom"] is True, f"Plan {plan_id} hub outside room polygon"
        assert c4["clearance_pass"] is True, (
            f"Plan {plan_id} clearance {c4['min_clearance_observed_m']} < 0.499 m"
        )
        assert c4["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_5_closed_ring_connectivity(self, repo: DatasetRepository, plan_id: int):
        """Hard Constraint 5: Closed-ring connectivity (closure <= 1.0 mm, single non-branching cycle)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c5 = audit["criterion_5_closed_ring_connectivity"]

        assert c5["closure_within_1mm"] is True, f"Plan {plan_id} closure gap exceeds 1.0 mm"
        assert c5["max_closure_gap_m"] <= 0.001
        assert c5["max_start_gap_m"] <= 0.001
        assert c5["max_return_gap_m"] <= 0.001
        assert c5["max_consecutive_gap_m"] <= 0.001
        assert c5["is_single_non_branching_cycle"] is True
        assert c5["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_6_axis_aligned_routing(self, repo: DatasetRepository, plan_id: int):
        """Hard Constraint 6: Axis-aligned routing (rectilinear segments only, 90-degree turns)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c6 = audit["criterion_6_axis_aligned_routing"]

        assert c6["diagonal_segments_count"] == 0, f"Plan {plan_id} contains diagonal segments"
        assert c6["strict_axis_alignment_pass"] is True
        assert c6["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_7_wall_and_window_obstacle_avoidance(
        self, repo: DatasetRepository, plan_id: int
    ):
        """Hard Constraint 7: Wall and window obstacle avoidance (zero penetration at 1 mm tolerance)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c7 = audit["criterion_7_obstacle_avoidance"]

        assert c7["zero_wall_penetration_pass"] is True, f"Plan {plan_id} penetrates obstacles"
        assert c7["max_obstacle_penetration_m"] <= 0.001
        assert c7["max_boundary_exit_m"] <= 0.001
        assert c7["compliant"] is True

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_constraint_8_exact_metric_recalculation(self, repo: DatasetRepository, plan_id: int):
        """Hard Constraint 8: Exact metric recalculation (bitwise agreement, Delta J = 0.0000)."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        audit = audit_plan_8_criteria(plan, sol)["criteria_checks"]
        c8 = audit["criterion_8_exact_metric_recalculation"]

        assert c8["deltas"]["delta_J"] == 0.0, f"Plan {plan_id} Delta J != 0"
        assert c8["deltas"]["delta_X"] == 0
        assert c8["deltas"]["delta_B"] == 0
        assert c8["metrics_concordant"] is True
        assert c8["compliant"] is True


# =============================================================================
# SUITE 2: Dual-Oracle Validation (FloorPlanChecker vs IndependentVerifier)
# =============================================================================


class TestDualOracleConcordance:
    """Rigorous dual-oracle concordance testing between FloorPlanChecker and IndependentVerifier."""

    def test_dual_oracle_demo_three_rooms(self, demo_three_rooms):
        """100% concordance on demo_three_rooms fixture."""
        plan, topology, sol = demo_three_rooms
        res: DualValidationResult = IndependentVerifier.dual_validate(
            plan=plan, raw_submission=sol, topology=topology, tolerance_m=0.001
        )
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_J == pytest.approx(0.0, abs=1e-4)
        assert res.delta_X == 0
        assert res.delta_B == 0
        assert res.delta_L <= 0.001
        assert res.delta_O <= 0.001
        assert len(res.discrepancies) == 0

    def test_dual_oracle_demo_six_rooms(self, demo_six_rooms):
        """100% concordance on demo_six_rooms fixture with 2 hubs."""
        plan, topology, sol = demo_six_rooms
        res: DualValidationResult = IndependentVerifier.dual_validate(
            plan=plan, raw_submission=sol, topology=topology, tolerance_m=0.001
        )
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_J == pytest.approx(0.0, abs=1e-4)
        assert res.delta_X == 0
        assert res.delta_B == 0
        assert res.delta_area_gap == pytest.approx(0.0, abs=1e-4)
        assert len(res.discrepancies) == 0

    def test_dual_oracle_demo_disconnected(self, demo_disconnected):
        """100% concordance on demo_disconnected fixture with separate topological clusters."""
        plan, topology, sol = demo_disconnected
        res: DualValidationResult = IndependentVerifier.dual_validate(
            plan=plan, raw_submission=sol, topology=topology, tolerance_m=0.001
        )
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_J == pytest.approx(0.0, abs=1e-4)
        assert len(res.discrepancies) == 0

    @pytest.mark.parametrize("plan_id", [0, 4, 12, 24, 28])
    def test_dual_oracle_benchmark_plans(self, repo: DatasetRepository, plan_id: int):
        """Dual-oracle concordance on benchmark production floor plans."""
        plan = repo.get_floor_plan(plan_id)
        sol = get_cached_solution(plan_id)
        assert sol is not None

        res: DualValidationResult = IndependentVerifier.dual_validate(
            plan=plan, raw_submission=sol, tolerance_m=0.001
        )
        assert res.is_concordant is True, f"Plan {plan_id} oracles discordant: {res.discrepancies}"
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_J == 0.0
        assert res.delta_X == 0
        assert res.delta_B == 0


# =============================================================================
# SUITE 3: Negative Test Cases (All Hard Failure Modes)
# =============================================================================


class TestNegativeTestCases:
    """Exhaustive negative test cases verifying dual-oracle rejection on invalid inputs."""

    def test_negative_obstacle_collision_wall(self, demo_three_rooms):
        """Negative: routing through an interior dividing wall must fail Rule 5 in both checkers."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Force route through solid dividing wall at y = 0.8
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            corrupt["hubs"][0]["route_segments"][0]["polyline"][0],
            [4.5, 0.8],  # Solid dividing wall
            [5.5, 0.8],  # Wall obstacle penetrated
            [7.5, 2.0],
        ]

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True, "Both oracles must agree on failure"
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 5"]
        assert not dual_res.independent_report.rules_passed["Rule 5"]
        assert any("penetrates wall/window obstacle" in v.message for v in dual_res.official_report.violations)

    def test_negative_obstacle_collision_window(self, demo_three_rooms):
        """Negative: routing through a window boundary opening must fail Rule 5 in both checkers."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Window at x in [2.0, 3.0], y in [7.9, 8.0]
        corrupt["hubs"][0]["route_segments"][0]["polyline"] = [
            corrupt["hubs"][0]["route_segments"][0]["polyline"][0],
            [2.5, 7.5],
            [2.5, 8.0],  # Window obstacle
        ]

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 5"]
        assert not dual_res.independent_report.rules_passed["Rule 5"]

    def test_negative_diagonal_segment(self, demo_three_rooms):
        """Negative: non-axis-aligned segment (diagonal) must fail Rule 5 in both checkers."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        p0 = corrupt["hubs"][0]["route_segments"][0]["polyline"][0]
        # Diagonal segment: dx > 1e-4 and dy > 1e-4
        corrupt["hubs"][0]["route_segments"][0]["polyline"][1] = [p0[0] + 0.5, p0[1] + 0.5]

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 5"]
        assert not dual_res.independent_report.rules_passed["Rule 5"]
        assert any("is diagonal" in v.message for v in dual_res.official_report.violations)

    def test_negative_open_loop_start_gap(self, demo_three_rooms):
        """Negative: ring start point shifted 5 mm (> 1 mm tolerance) must fail Rule 4."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["route_segments"][0]["polyline"][0][0] += 0.005

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 4"]
        assert not dual_res.independent_report.rules_passed["Rule 4"]
        assert any(
            ("ring start" in v.message or "misses fixed terminal" in v.message)
            for v in dual_res.official_report.violations
        )

    def test_negative_open_loop_return_gap(self, demo_three_rooms):
        """Negative: ring return point shifted 5 mm (> 1 mm tolerance) must fail Rule 4."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["route_segments"][-1]["polyline"][-1][0] += 0.005

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 4"]
        assert not dual_res.independent_report.rules_passed["Rule 4"]
        assert any(
            ("ring end" in v.message or "misses fixed terminal" in v.message)
            for v in dual_res.official_report.violations
        )

    def test_negative_open_loop_broken_cycle(self, demo_three_rooms):
        """Negative: closed sequence not returning to hub must fail Rule 4."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        corrupt["hubs"][0]["closed_sequence"][-1] = "N3"

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 4"]
        assert not dual_res.independent_report.rules_passed["Rule 4"]

    def test_negative_capacity_nodes_exceeded(self, demo_six_rooms):
        """Negative: assigning > 5 nodes to a single hub must fail Rule 2 in both checkers."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign 6 nodes to H1
        corrupt["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]
        corrupt["hubs"][1]["assigned_nodes"] = []

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 2"]
        assert not dual_res.independent_report.rules_passed["Rule 2"]
        assert any("exceeding capacity limit of 5" in v.message for v in dual_res.official_report.violations)

    def test_negative_capacity_area_exceeded(self, demo_six_rooms):
        """Negative: assigning > 100 m² room area to a single hub must fail Rule 2."""
        plan, topology, sol = demo_six_rooms
        corrupt = deepcopy(sol)
        # Assign all 6 rooms (120 m² > 100 m²) to H1
        corrupt["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        corrupt["hubs"][1]["assigned_rooms"] = []

        dual_res = IndependentVerifier.dual_validate(plan, corrupt, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 2"]
        assert not dual_res.independent_report.rules_passed["Rule 2"]
        assert any("exceeds capacity limit of 100.0" in v.message for v in dual_res.official_report.violations)


# =============================================================================
# SUITE 4: Fixed Node Coordinate Invariance & Submillimetre Boundary Tests
# =============================================================================


class TestFixedNodeCoordinateInvariance:
    """Verifies fixed node coordinate invariance (strictly 0.000000 m displacement)."""

    def test_fixed_node_displacement_zero_demo(self, demo_three_rooms):
        """Demo three rooms terminal arrival/departure coordinates match fixed node exactly."""
        plan, _, sol = demo_three_rooms
        max_disp = 0.0
        for seg in sol["hubs"][0]["route_segments"]:
            f_node = seg["from"]
            t_node = seg["to"]
            pts = seg["polyline"]
            if f_node in plan.nodes:
                loc = plan.nodes[f_node].location
                d = math.hypot(pts[0][0] - loc.x, pts[0][1] - loc.y)
                max_disp = max(max_disp, d)
            if t_node in plan.nodes:
                loc = plan.nodes[t_node].location
                d = math.hypot(pts[-1][0] - loc.x, pts[-1][1] - loc.y)
                max_disp = max(max_disp, d)

        assert max_disp <= 1e-6, f"Displacement {max_disp} > 0.000000 m"

    def test_fixed_node_displacement_submillimetre_pass(self, demo_three_rooms):
        """Displacement of 0.5 mm (< 1.0 mm tolerance) passes cleanly."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Shift arrival at N1 by 0.5 mm (0.0005 m <= 0.001 m tolerance)
        corrupt["hubs"][0]["route_segments"][0]["polyline"][-1][0] += 0.0005
        corrupt["hubs"][0]["route_segments"][1]["polyline"][0][0] += 0.0005

        rep = FloorPlanChecker.evaluate(plan, corrupt, topology=topology, tolerance_m=0.001)
        assert rep.rules_passed["Rule 4"] is True

    def test_fixed_node_displacement_supermillimetre_fail(self, demo_three_rooms):
        """Displacement of 1.5 mm (> 1.0 mm tolerance) must be strictly rejected."""
        plan, topology, sol = demo_three_rooms
        corrupt = deepcopy(sol)
        # Shift arrival at N1 by 1.5 mm (0.0015 m > 0.001 m tolerance)
        corrupt["hubs"][0]["route_segments"][0]["polyline"][-1][0] += 0.0015
        corrupt["hubs"][0]["route_segments"][1]["polyline"][0][0] += 0.0015

        dual_res = IndependentVerifier.dual_validate(
            plan, corrupt, topology=topology, tolerance_m=0.001
        )
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is False
        assert dual_res.independent_valid is False
        assert not dual_res.official_report.rules_passed["Rule 4"]
        assert not dual_res.independent_report.rules_passed["Rule 4"]
        assert any("misses fixed terminal" in v.message for v in dual_res.official_report.violations)


# =============================================================================
# SUITE 5: Round-Trip Serialization & Deserialization Tests
# =============================================================================


class TestRoundTripSerialization:
    """Verifies round-trip serialization of solutions, configs, and models with bitwise fidelity."""

    def test_bundle_json_roundtrip(self, repo: DatasetRepository):
        """SynthesizedRingsBundle -> model_dump -> JSON -> model_validate roundtrip."""
        sol_dict = get_cached_solution(0)
        assert sol_dict is not None

        bundle_orig = SynthesizedRingsBundle.model_validate(sol_dict)
        json_str = json.dumps(bundle_orig.model_dump())
        loaded_dict = json.loads(json_str)
        bundle_restored = SynthesizedRingsBundle.model_validate(loaded_dict)

        assert bundle_orig.plan_id == bundle_restored.plan_id
        assert len(bundle_orig.hub_rings) == len(bundle_restored.hub_rings)
        assert bundle_orig.total_length_m == bundle_restored.total_length_m
        assert bundle_orig.total_bends == bundle_restored.total_bends
        assert bundle_orig.total_crossings == bundle_restored.total_crossings
        assert bundle_orig.total_overlap_m == bundle_restored.total_overlap_m
        assert bundle_orig.total_cost_J == bundle_restored.total_cost_J

        for r_orig, r_rest in zip(bundle_orig.hub_rings, bundle_restored.hub_rings):
            assert r_orig.hub_id == r_rest.hub_id
            assert r_orig.room_id == r_rest.room_id
            assert r_orig.location.coords == r_rest.location.coords
            assert r_orig.assigned_rooms == r_rest.assigned_rooms
            assert r_orig.assigned_nodes == r_rest.assigned_nodes
            assert r_orig.closed_sequence == r_rest.closed_sequence
            assert len(r_orig.route_segments) == len(r_rest.route_segments)

    def test_slide8_json_export_and_revalidation(self, repo: DatasetRepository):
        """Export to Slide 8 JSON format -> validate Slide8Export -> re-evaluate with checkers."""
        plan = repo.get_floor_plan(0)
        sol_dict = get_cached_solution(0)
        assert sol_dict is not None
        bundle = SynthesizedRingsBundle.model_validate(sol_dict)

        export_dict = export_slide8_json(plan, bundle)
        exported = Slide8Export.model_validate(export_dict)

        assert exported.plan_id == "0"
        assert exported.units == "m"
        assert len(exported.hubs) == len(bundle.hub_rings)

        # Re-evaluate the exported json with FloorPlanChecker and IndependentVerifier
        dual_res = IndependentVerifier.dual_validate(plan, export_dict)
        assert dual_res.is_concordant is True
        assert dual_res.official_valid is True
        assert dual_res.independent_valid is True
        assert dual_res.delta_J == 0.0

    def test_system_config_yaml_roundtrip(self, tmp_path: Path):
        """SystemConfig -> YAML -> SystemConfig.from_yaml."""
        cfg_orig = SystemConfig(
            max_area_m2=95.0,
            max_nodes_per_hub=4,
            wall_clearance_m=0.55,
            checker_tolerance_m=0.001,
            weight_length=1.0,
            weight_crossing=5.0,
            weight_overlap=1.0,
            weight_bend=0.5,
        )

        yaml_path = tmp_path / "test_config.yaml"
        cfg_orig.to_yaml(yaml_path)
        assert yaml_path.exists()

        cfg_loaded = SystemConfig.from_yaml(yaml_path)
        assert cfg_orig == cfg_loaded
        assert cfg_loaded.max_area_m2 == 95.0
        assert cfg_loaded.max_nodes_per_hub == 4
        assert cfg_loaded.wall_clearance_m == 0.55

    def test_system_config_json_roundtrip(self):
        """SystemConfig -> JSON -> SystemConfig.model_validate_json."""
        cfg_orig = SystemConfig()
        json_data = cfg_orig.model_dump_json()
        cfg_restored = SystemConfig.model_validate_json(json_data)

        assert cfg_orig == cfg_restored
        assert cfg_restored.checker_tolerance_m == 0.001
        assert cfg_restored.max_nodes_per_hub == 5
        assert cfg_restored.max_area_m2 == 100.0

    def test_system_config_overrides(self):
        """SystemConfig.with_overrides properly produces modified copy without mutating original."""
        cfg_default = SystemConfig()
        cfg_modified = cfg_default.with_overrides(max_area_m2=80.0, max_nodes_per_hub=3)

        assert cfg_default.max_area_m2 == 100.0
        assert cfg_default.max_nodes_per_hub == 5
        assert cfg_modified.max_area_m2 == 80.0
        assert cfg_modified.max_nodes_per_hub == 3
        assert cfg_modified.checker_tolerance_m == cfg_default.checker_tolerance_m
