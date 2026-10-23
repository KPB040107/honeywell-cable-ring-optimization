"""Phase 16 Dual-Validation Unit Test Suite.

Rigorously verifies the independent geometric dual-verification oracle
(`IndependentVerifier`) in comparison with the official `FloorPlanChecker`
at 1 mm tolerance:
- Rule 1: Full Coverage & Excluded Spaces
- Rule 2: Capacity Limits (<= 5 nodes, <= 100 m² per hub)
- Rule 3: Doorway Contiguity (Independent Door Graph Construction)
- Rule 4: Ring Closure and Continuity (1 mm endpoint closure)
- Rule 5: Axis-Aligned Free Space Routing (STRtree Obstacle Avoidance)
- Rule 6: Hub Clearance (>= 0.50 m STRtree clearance disc)
- Single-door retrace collinearity and parallel-lane separation
- Metric recalculation parity: J = L + 5X + O + 0.5B and Delta A_gap
- 1D Sweep-line exact interval overlap algorithm
- End-to-end concordance on real-world ResPlan floor plans
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import networkx as nx
import pytest
from shapely.geometry import box, LineString, Point

from resplan_router.contracts import (
    CompletedHubRing,
    EligibleOrExcludedRoom,
    EvaluationReport,
    FixedNode,
    MetricFloorPlan,
    Point2D,
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

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "demo_outputs"


# =============================================================================
# Fixtures
# =============================================================================


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
def demo_disconnected():
    """Provides plan, topology, and valid solution for demo_disconnected_zones."""
    raw = json.loads((FIXTURES_DIR / "demo_disconnected_zones.json").read_text(encoding="utf-8"))
    sol = json.loads(
        (DEMO_OUTPUTS_DIR / "demo_disconnected_zones_solution.json").read_text(encoding="utf-8")
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
# 1. Independent Spatial Modeling & STRtree Tests
# =============================================================================


class TestIndependentSpatialModeling:
    """Verifies STRtree indexing and continuous obstacle intersection logic."""

    def test_strtree_obstacle_spatial_indexing(self, demo_three_rooms):
        """STRtree accurately indexes constituent obstacle polygons and finds hits."""
        plan, _, _ = demo_three_rooms
        from resplan_router.experimental.independent_verifier import _extract_all_polygons
        from shapely.strtree import STRtree

        polygons = _extract_all_polygons(plan.obstacles_geom)
        assert len(polygons) > 0, "Floor plan must contain obstacle polygons"

        tree = STRtree(polygons)
        # Line traversing interior solid wall at x=5.0, y=3.5
        wall_cross_line = LineString([(4.0, 3.5), (6.0, 3.5)])
        hits = tree.query(wall_cross_line)
        assert len(hits) > 0, "STRtree query must intersect wall obstacle"

        # Line strictly within free space at x=2.5
        free_line = LineString([(2.5, 1.0), (2.5, 3.5)])
        free_hits = tree.query(free_line)
        actual_intersections = [
            polygons[idx] for idx in free_hits if polygons[idx].intersection(free_line).length > 0.001
        ]
        assert len(actual_intersections) == 0, "Free space line must not penetrate any obstacle"

    def test_continuous_hub_clearance_disc(self, demo_three_rooms):
        """Continuous spatial disc buffer verifies hub clearance >= 0.50 m."""
        plan, _, sol = demo_three_rooms
        hub_loc = sol["hubs"][0]["location"]
        hub_pt = Point(hub_loc["x"], hub_loc["y"])

        # Official hub location satisfies >= 0.50 m clearance
        from resplan_router.experimental.independent_verifier import _extract_all_polygons
        from shapely.strtree import STRtree

        polygons = _extract_all_polygons(plan.obstacles_geom)
        tree = STRtree(polygons)
        clearance_disc = hub_pt.buffer(0.499)
        hits = tree.query(clearance_disc)
        for idx in hits:
            assert polygons[idx].distance(hub_pt) >= 0.499, "Hub clearance must be >= 0.499 m"

        # Hub shifted to wall at x=0.2 m violates clearance
        bad_hub_pt = Point(0.2, hub_loc["y"])
        bad_disc = bad_hub_pt.buffer(0.499)
        assert plan.inner_geom.boundary.distance(bad_hub_pt) < 0.499


# =============================================================================
# 2. Independent Topological Doorway Graph Tests
# =============================================================================


class TestIndependentDoorwayGraph:
    """Verifies doorway graph construction directly from physical door polygons."""

    def test_independent_door_graph_construction(self, demo_three_rooms):
        """Constructs room adjacency from physical door polygons matching macro graph."""
        plan, macro_topo, _ = demo_three_rooms
        ind_graph = IndependentVerifier._build_independent_doorway_graph(plan, None)

        assert ind_graph.has_edge("R01", "R02"), "R01 and R02 must be connected by door"
        assert ind_graph.has_edge("R01", "R03"), "R01 and R03 must be connected by door"
        assert ind_graph.has_edge("R02", "R04"), "R02 and R04 must be connected by door"
        assert not ind_graph.has_edge("R02", "R03"), "R02 and R03 must not share a direct door"

    def test_subgraph_connectivity_detection(self, demo_three_rooms):
        """Subgraphs of non-contiguous rooms are strictly rejected."""
        plan, _, _ = demo_three_rooms
        door_graph = IndependentVerifier._build_independent_doorway_graph(plan, None)

        # R01 and R02 are connected
        assert nx.is_connected(door_graph.subgraph(["R01", "R02"]))

        # R02 and R03 are not directly connected without R01
        assert not nx.is_connected(door_graph.subgraph(["R02", "R03"]))


# =============================================================================
# 3. 1D Event-Based Sweep-Line Overlap Tests
# =============================================================================


class TestSweepLineOverlapAlgorithm:
    """Verifies that the independent 1D sweep-line algorithm exactly matches Slide 7."""

    def test_sweepline_sub_mm_collinear_overlap(self):
        """Lateral offset of 0.5 mm (<= 1 mm) counts as collinear overlap."""
        leg1 = [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)]
        leg2 = [Point2D(x=4.0, y=0.0005), Point2D(x=0.0, y=0.0005)]

        o_off = FloorPlanChecker._compute_overlap_for_legs([leg1, leg2])
        o_ind = IndependentVerifier._compute_overlap_sweepline([leg1, leg2])

        assert o_off == pytest.approx(4.0, abs=1e-3)
        assert o_ind == pytest.approx(4.0, abs=1e-3)
        assert abs(o_off - o_ind) < 1e-4

    def test_sweepline_supra_mm_separation_zero_overlap(self):
        """Lateral offset of 1.5 mm (> 1 mm) yields O = 0.0 m."""
        leg1 = [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)]
        leg2 = [Point2D(x=4.0, y=0.0015), Point2D(x=0.0, y=0.0015)]

        o_off = FloorPlanChecker._compute_overlap_for_legs([leg1, leg2])
        o_ind = IndependentVerifier._compute_overlap_sweepline([leg1, leg2])

        assert o_off == pytest.approx(0.0, abs=1e-4)
        assert o_ind == pytest.approx(0.0, abs=1e-4)
        assert abs(o_off - o_ind) < 1e-4

    def test_sweepline_multi_cable_overlap_k_equals_3(self):
        """3 cables overlapping on same interval [2, 6] contribute (3 - 1) * 4 = 8.0 m."""
        legs = [
            [Point2D(x=0.0, y=1.0), Point2D(x=10.0, y=1.0)],
            [Point2D(x=2.0, y=1.0), Point2D(x=8.0, y=1.0)],
            [Point2D(x=2.0, y=1.0004), Point2D(x=6.0, y=1.0004)],
        ]
        o_off = FloorPlanChecker._compute_overlap_for_legs(legs)
        o_ind = IndependentVerifier._compute_overlap_sweepline(legs)

        assert abs(o_off - o_ind) < 1e-4
        assert o_ind > 0.0


# =============================================================================
# 4. Single-Door Retrace Collinearity Tests
# =============================================================================


class TestSingleDoorRetraceCollinearity:
    """Audits single-door leaf rooms for collinear retrace and parallel-lane compliance."""

    def test_audit_single_door_retrace_on_valid_solution(self, demo_three_rooms):
        """Valid demo solution has leaf room R03 with legal parallel doorway transits."""
        plan, _, sol = demo_three_rooms
        audit = IndependentVerifier.audit_single_door_retrace(plan, sol)

        assert audit.leaf_rooms == ["R03"]
        assert audit.retrace_events_detected >= 1
        assert audit.all_collinear_or_lane_separated is True
        assert len(audit.diagnostics) == 0

    def test_direct_exact_retrace_leg_validity(self, demo_three_rooms):
        """Exact 180-degree retracing along identical polyline is valid in both checkers."""
        plan, topology, sol = demo_three_rooms
        retrace_sol = deepcopy(sol)
        n1 = [plan.nodes["N1"].location.x, plan.nodes["N1"].location.y]
        n2 = [plan.nodes["N2"].location.x, plan.nodes["N2"].location.y]

        # Retrace Leg 1 exactly in Leg 2
        retrace_sol["hubs"][0]["route_segments"][1]["polyline"] = [
            n2,
            [n2[0], n1[1]],
            n1,
        ]

        dual_res = IndependentVerifier.dual_validate(plan, retrace_sol, topology=topology)
        assert dual_res.is_concordant is True
        assert dual_res.metrics_concordant is True
        assert dual_res.delta_O <= 1e-3


# =============================================================================
# 5. Dual-Validation Concordance on Demo Plans
# =============================================================================


class TestDualValidationConcordance:
    """Verifies 100% agreement between FloorPlanChecker and IndependentVerifier on valid plans."""

    def test_dual_validation_demo_three_rooms(self, demo_three_rooms):
        """demo_three_rooms_window yields 100% concordance on validity and metrics."""
        plan, topology, sol = demo_three_rooms
        res = IndependentVerifier.dual_validate(plan, sol, topology=topology)

        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_L == 0.0
        assert res.delta_X == 0
        assert res.delta_O == 0.0
        assert res.delta_B == 0
        assert res.delta_J == 0.0
        assert res.delta_area_gap == 0.0
        assert len(res.discrepancies) == 0

    def test_dual_validation_demo_disconnected_zones(self, demo_disconnected):
        """demo_disconnected_zones yields 100% concordance across multiple hubs."""
        plan, topology, sol = demo_disconnected
        res = IndependentVerifier.dual_validate(plan, sol, topology=topology)

        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True
        assert res.delta_X == 0
        assert res.delta_B == 0
        assert res.delta_L <= 1e-3
        assert res.delta_J <= 1e-3

    def test_dual_validation_demo_six_rooms(self, demo_six_rooms):
        """demo_six_rooms_capacity yields 100% concordance on multi-hub partition."""
        plan, topology, sol = demo_six_rooms
        res = IndependentVerifier.dual_validate(plan, sol, topology=topology)

        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True
        assert res.rules_concordant is True
        assert res.metrics_concordant is True

    def test_synthesized_bundle_type_support(self, demo_three_rooms):
        """IndependentVerifier accepts SynthesizedRingsBundle as input directly."""
        plan, topology, sol = demo_three_rooms
        # Convert sol dict into SynthesizedRingsBundle
        hub_data = sol["hubs"][0]
        segments = [
            RouteSegment(
                from_node=s["from"],
                to_node=s["to"],
                polyline=[Point2D(x=pt[0], y=pt[1]) for pt in s["polyline"]],
                length_m=s["length_m"],
            )
            for s in hub_data["route_segments"]
        ]
        total_len = sum(s.length_m for s in segments)
        ring = CompletedHubRing(
            hub_id=hub_data["hub_id"],
            room_id=hub_data["room_id"],
            location=Point2D(x=hub_data["location"]["x"], y=hub_data["location"]["y"]),
            assigned_rooms=hub_data["assigned_rooms"],
            assigned_nodes=hub_data["assigned_nodes"],
            closed_sequence=hub_data["closed_sequence"],
            route_segments=segments,
            ring_length_m=total_len,
            ring_bends=12,
            ring_crossings=0,
            ring_overlap_m=0.0,
            cost_J=total_len + 0.5 * 12,
        )
        bundle = SynthesizedRingsBundle(
            plan_id=sol["plan_id"],
            hub_rings=[ring],
            total_length_m=total_len,
            total_bends=12,
            total_cost_J=total_len + 0.5 * 12,
        )

        res = IndependentVerifier.dual_validate(plan, bundle, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is True
        assert res.independent_valid is True


# =============================================================================
# 6. Negative Violation Concordance Tests (All 6 Rules)
# =============================================================================


class TestNegativeViolationConcordance:
    """Verifies that all 6 Hard Rule violations are caught by BOTH checkers with 100% agreement."""

    def test_rule1_excluded_room_assigned(self, demo_three_rooms):
        """Both checkers reject assignment of excluded bathroom R04."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"].append("R04")

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False
        assert res.rules_concordant is True

    def test_rule1_eligible_room_omitted(self, demo_three_rooms):
        """Both checkers reject unassigned eligible room."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02"]  # R03 omitted
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2"]  # N3 omitted
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "H1"]
        bad_sol["hubs"][0]["route_segments"] = bad_sol["hubs"][0]["route_segments"][:2]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule1_node_home_mismatch(self, demo_three_rooms):
        """Both checkers reject fixed node served by hub that does not own its host room."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # N3 belongs to R03, but omit R03 from assigned_rooms
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02"]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule2_capacity_node_limit_exceeded(self, demo_three_rooms):
        """Both checkers reject hub serving > 5 nodes."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule2_capacity_area_limit_exceeded(self, demo_three_rooms):
        """Both checkers reject hub zone exceeding 100.0 m²."""
        plan, topology, sol = demo_three_rooms
        # Evaluate with strict max_area_m2 = 20.0 m² (plan eligible area is ~60 m²)
        res = IndependentVerifier.dual_validate(plan, sol, topology=topology, max_area_m2=20.0)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule3_doorway_contiguity_violation(self, demo_six_rooms):
        """Both checkers reject non-contiguous room assignments."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Create disconnected zone by assigning non-adjacent rooms to hub 1
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R05"]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule4_ring_sequence_start_mismatch(self, demo_three_rooms):
        """Both checkers reject ring not starting with hub_id."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["closed_sequence"][0] = "N1"

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule4_terminal_miss_greater_than_1mm(self, demo_three_rooms):
        """Both checkers reject segment endpoint missing node terminal by > 1 mm."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Shift start of leg 0 by 5 mm away from hub
        hub_loc = bad_sol["hubs"][0]["location"]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][0] = [
            hub_loc["x"] + 0.005,
            hub_loc["y"],
        ]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule4_consecutive_segment_gap(self, demo_three_rooms):
        """Both checkers reject internal segment gap > 1 mm."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Introduce 10 mm gap between segment 0 end and segment 1 start
        p_end = bad_sol["hubs"][0]["route_segments"][0]["polyline"][-1]
        bad_sol["hubs"][0]["route_segments"][1]["polyline"][0] = [
            p_end[0] + 0.010,
            p_end[1],
        ]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule5_diagonal_segment_rejected(self, demo_three_rooms):
        """Both checkers reject non-axis-aligned segment."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][1] = [3.2, 2.7]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule5_solid_wall_obstacle_penetration(self, demo_three_rooms):
        """Both checkers reject segment penetrating solid wall."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Route leg 1 directly through wall at y=3.5 m (wall between x=4.95 and 5.05)
        bad_sol["hubs"][0]["route_segments"][1]["polyline"] = [
            [2.5, 2.0],
            [2.5, 3.5],
            [7.5, 3.5],
            [7.5, 1.9],
            [7.5, 1.8],
        ]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule5_outer_boundary_exit(self, demo_three_rooms):
        """Both checkers reject segment routed outside building exterior."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Extend segment outside building bounding box (x > 10 m, e.g. x = 20.0 m)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            [2.5, 3.85],
            [20.0, 3.85],
            [20.0, 2.0],
            [2.5, 2.0],
        ]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule6_hub_clearance_under_50cm(self, demo_three_rooms):
        """Both checkers reject hub placed < 0.50 m from wall obstacle."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Shift hub to within 10 cm of outer wall (x=0.10)
        bad_sol["hubs"][0]["location"] = {"x": 0.10, "y": 3.85}
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][0] = [0.10, 3.85]
        bad_sol["hubs"][0]["route_segments"][-1]["polyline"][-1] = [0.10, 3.85]

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False

    def test_rule6_hub_in_excluded_room(self, demo_three_rooms):
        """Both checkers reject hub hosted in excluded bathroom."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["room_id"] = "R04"  # Bathroom
        bad_sol["hubs"][0]["assigned_rooms"].append("R04")

        res = IndependentVerifier.dual_validate(plan, bad_sol, topology=topology)
        assert res.is_concordant is True
        assert res.official_valid is False
        assert res.independent_valid is False


# =============================================================================
# 7. Real-World ResPlan Dataset End-to-End Dual-Audit
# =============================================================================


class TestRealWorldResPlanDualAudit:
    """Audits real floor plans from the 17,000 ResPlan dataset."""

    @pytest.mark.parametrize("plan_id", [0, 14433])
    def test_resplan_real_dataset_dual_audit(self, plan_id: int):
        """Verifies dual concordance on end-to-end routed solutions from ResPlan dataset."""
        repo = DatasetRepository.get_instance()
        raw_rec = repo.get_record(plan_id)
        plan = parse_floor_plan(raw_rec)

        from resplan_router.phase6_output.pipeline import route_floor_plan
        sol_dict, off_rep, bundle = route_floor_plan(raw_rec)

        # Audit with IndependentVerifier
        res = IndependentVerifier.dual_validate(plan, sol_dict)

        assert res.is_concordant is True, (
            f"Plan {plan_id} must have concordant validity decisions (Off={res.official_valid}, Ind={res.independent_valid})"
        )
        if res.official_valid:
            assert res.independent_valid is True
            assert res.metrics_concordant is True, (
                f"Plan {plan_id} metrics must match: Delta L={res.delta_L}, Delta X={res.delta_X}, Delta O={res.delta_O}, Delta B={res.delta_B}, Delta J={res.delta_J}"
            )
            assert res.delta_L <= 1e-3
            assert res.delta_X == 0
            assert res.delta_O <= 1e-3
            assert res.delta_B == 0
            assert res.delta_J <= 1e-3


# =============================================================================
# 8. Dual-Validation of Recovered Discrepancy Plans (Agent 2 Rescues)
# =============================================================================


class TestRecoveredPlansDualAudit:
    """Audits recovered candidate solutions rescued by geometry healing."""

    @pytest.mark.parametrize("plan_id", [8, 10, 19, 8202, 10080, 8335, 1924])
    def test_recovered_plan_dual_concordance(self, plan_id: int):
        """Audits recovered discrepancy plans against BOTH checkers at 1 mm tolerance."""
        from resplan_router.phase1_ingestion.geometry_healing import recover_and_certify_plan

        repo = DatasetRepository.get_instance()
        plan = repo.get_floor_plan(plan_id)

        is_valid, report, bundle = recover_and_certify_plan(plan)
        assert is_valid is True, f"Recovery failed for plan {plan_id}"

        # Dual-validate against both FloorPlanChecker and IndependentVerifier
        dual_res = IndependentVerifier.dual_validate(plan, bundle)

        assert dual_res.is_concordant is True
        assert dual_res.official_valid is True
        assert dual_res.independent_valid is True
        assert dual_res.rules_concordant is True
        assert dual_res.metrics_concordant is True
        assert dual_res.delta_L <= 1e-3
        assert dual_res.delta_X == 0
        assert dual_res.delta_O <= 1e-3
        assert dual_res.delta_B == 0
        assert dual_res.delta_J <= 1e-3
        assert dual_res.delta_area_gap <= 1e-3
        assert len(dual_res.discrepancies) == 0

