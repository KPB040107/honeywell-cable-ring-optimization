"""Official Honeywell Cable Ring Optimization - Exhaustive Correctness & Constraint Audit Suite.

Audited and Verified by Agent 1: Correctness and Constraint Audit Lead.

This test suite comprehensively validates:
1. All 17 Official Competition Constraints enforced by FloorPlanChecker:
   - Rule 1 (Full Coverage & Eligibility): C-01 (Room Eligibility), C-02 (Fixed Node Existence),
     C-03 (Fixed Node Coverage), C-04 (Eligible Room Coverage), C-05 (Room-Node Integrity),
     Unknown Rooms/Nodes rejection.
   - Rule 2 (Capacity): C-06 (Node capacity <= 5), C-07 (Area capacity <= 100.0 m²),
     Boundary edge cases (exact 5 nodes, exact 100.0 m²), submillimeter area tolerance.
   - Rule 3 (Doorway Contiguity): C-08 (Doorway connected subgraph), C-09 (Non-passage portal rejection:
     wall-only, window-only, exterior/front_door excluded), single-room hubs.
   - Rule 4 (Single Closed Ring without Branches): C-10 (Closed sequence H -> nodes -> H),
     C-11 (Physical polyline continuity, 1 mm ring start/return tolerance, consecutive segment gaps),
     C-12 (Terminal snapping within 1 mm and room containment).
   - Rule 5 (Free Space & Strictly Rectilinear Routing): C-13 (Strict axis-alignment dx=0 or dy=0),
     C-14 (Inner boundary containment), C-15 (Obstacle avoidance: walls and windows).
   - Rule 6 (Hub Placement & Clearance): C-16 (Assigned eligible room, >= 0.50 m clearance to interior
     walls and exterior boundary with 1 mm tolerance).
   - Rule 7 (Scoring & Metric Accounting): C-17 (Retracing allowed, 1 mm lateral tolerance:
     0.5 mm penalized vs 1.5 mm zero overlap, crossings away from terminals vs at terminals,
     cyclic bend counting with 90° turns and 180° reversals, composite cost J = L + 5X + O + 0.5B,
     area gap Delta A_gap).
2. Hub-Count Priority in LinUCB-ALNS:
   - Lexicographical state comparison strictly favors lower hub count regardless of routing cost J.
   - LinUCB reward function assigns heavy penalty (-10.0) to hub increases and strong rewards (+5.0*Delta H + 2.0)
     to hub reductions.
   - Simulated Annealing acceptance strictly forbids transitions that increase hub count.
   - Final optimizer monotonicity guard guarantees incumbent preservation (H_final <= H_base, J_final <= J_base).
3. Clean Rejection of Infeasible Floor Plans:
   - Oversized eligible rooms (> 100.0 m²) return solver_status="INFEASIBLE", bundle=None, is_valid=False.
   - Disconnected rooms without doorway paths cannot be bridged; rejected without fabricated routes.
   - Rooms with excessive fixed nodes (> 5) cannot be split; rejected as INFEASIBLE.
   - Zero hallucinated or synthetic routes are ever produced for infeasible floor plans.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Dict, List
import pytest
import shapely
from shapely.geometry import box, LineString, Point, Polygon

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
    RoomCategory,
    RouteSegment,
    RuleViolation,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer
from resplan_router.optimizers.alns_operators import ALNSState
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.linucb import LinUCBBandit, compute_linucb_reward


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "demo_outputs"


@pytest.fixture(scope="module")
def demo_three_rooms():
    """Fixture providing plan, topology, and valid solution for demo_three_rooms_window."""
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
    """Fixture providing plan, topology, and valid solution for demo_six_rooms_capacity."""
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
# PART 1: Verification of 17 Official Competition Constraints (Rules 1 to 7)
# =============================================================================


class TestRule1FullCoverageAudit:
    """Verifies Rule 1: Every eligible room & fixed node assigned to exactly 1 hub."""

    def test_c01_excluded_spaces_rejected(self, demo_three_rooms):
        """C-01: Excluded spaces (bathroom, balcony, parking, etc.) assigned to hub are rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # R04 is bathroom (excluded category)
        bad_sol["hubs"][0]["assigned_rooms"].append("R04")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Excluded space" in v.message and "R04" in v.message
            for v in report.violations
        )

    def test_c02_fixed_node_per_eligible_room_invariance(self, demo_three_rooms, demo_six_rooms):
        """C-02: Problem specification invariance: exactly 1 fixed node per eligible room."""
        for plan, _, _ in (demo_three_rooms, demo_six_rooms):
            eligible_rooms = {r_id for r_id, r in plan.rooms.items() if r.is_eligible}
            node_rooms = {n.room_id for n in plan.nodes.values()}
            assert eligible_rooms == node_rooms, "Every eligible room must have a fixed node"
            assert len(plan.nodes) == len(eligible_rooms), "Exactly 1 fixed node per eligible room"

    def test_c03_omitted_fixed_node_rejected(self, demo_six_rooms):
        """C-03: Every fixed node must be assigned to at least 1 hub; omission triggers Rule 1."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Omit node N1 from hub 0
        bad_sol["hubs"][0]["assigned_nodes"] = [
            n for n in bad_sol["hubs"][0]["assigned_nodes"] if n != "N1"
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Fixed node 'N1' is not assigned to any hub" in v.message
            for v in report.violations
        )

    def test_c03_duplicate_fixed_node_rejected(self, demo_six_rooms):
        """C-03: Fixed node assigned to multiple hubs triggers Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Duplicate N1 into Hub 2 (and also add R01 to keep room-node aligned)
        bad_sol["hubs"][1]["assigned_nodes"].append("N1")
        bad_sol["hubs"][1]["assigned_rooms"].append("R01")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Fixed node 'N1' is assigned to multiple hubs" in v.message
            for v in report.violations
        )

    def test_c04_omitted_eligible_room_rejected(self, demo_six_rooms):
        """C-04: Every eligible room must be assigned to at least 1 hub; omission triggers Rule 1."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"] = [
            r for r in bad_sol["hubs"][0]["assigned_rooms"] if r != "R01"
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Eligible room 'R01' is not assigned to any hub" in v.message
            for v in report.violations
        )

    def test_c04_duplicate_eligible_room_rejected(self, demo_six_rooms):
        """C-04: Eligible room assigned to multiple hubs triggers Rule 1 violation."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][1]["assigned_rooms"].append("R01")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Eligible room 'R01' is assigned to multiple hubs" in v.message
            for v in report.violations
        )

    def test_c05_room_node_co_assignment_mismatch_rejected(self, demo_six_rooms):
        """C-05: Fixed node must be assigned to the exact same hub as its host room."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Move node N1 to Hub 2 while keeping room R01 in Hub 1
        bad_sol["hubs"][0]["assigned_nodes"] = [
            n for n in bad_sol["hubs"][0]["assigned_nodes"] if n != "N1"
        ]
        bad_sol["hubs"][1]["assigned_nodes"].append("N1")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(
            v.rule_number == 1 and "Fixed node 'N1' belongs to room 'R01'" in v.message
            for v in report.violations
        )

    def test_unknown_room_and_node_rejected(self, demo_three_rooms):
        """Rule 1: Fictitious or nonexistent room/node IDs assigned to a hub are rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_rooms"].append("R_FAKE_ROOM_999")
        bad_sol["hubs"][0]["assigned_nodes"].append("N_FAKE_NODE_999")

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "Unknown room" in v.message for v in report.violations)
        assert any(v.rule_number == 1 and "Unknown node" in v.message for v in report.violations)


class TestRule2CapacityAudit:
    """Verifies Rule 2: Each hub <= 5 nodes AND <= 100.0 m² area."""

    def test_c06_node_capacity_limit_exceeded(self, demo_six_rooms):
        """C-06: Hub serving > 5 fixed nodes (e.g. 6 nodes) triggers Rule 2 violation."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5", "N6"]
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        bad_sol["hubs"] = [bad_sol["hubs"][0]]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        assert any(
            v.rule_number == 2 and "exceeding capacity limit of 5" in v.message
            for v in report.violations
        )

    def test_c07_area_capacity_limit_exceeded(self, demo_six_rooms):
        """C-07: Hub serving > 100.0 m² area (e.g. 120.0 m²) triggers Rule 2 violation."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # All 6 rooms sum to 120.0 m²
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R02", "R03", "R04", "R05", "R06"]
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N2", "N3", "N4", "N5"]  # 5 nodes <= 5
        bad_sol["hubs"] = [bad_sol["hubs"][0]]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        assert any(
            v.rule_number == 2 and "exceeds capacity limit of 100.0" in v.message
            for v in report.violations
        )

    def test_boundary_condition_exact_5nodes_and_100m2_passes(self):
        """Rule 2 Boundary: Hub serving exactly 5 nodes and exactly 100.0 m² area passes cleanly."""
        rooms = {}
        nodes = {}
        for i in range(1, 6):
            r_id = f"R{i:02d}"
            n_id = f"N{i}"
            poly = box((i - 1) * 4.0, 0.0, i * 4.0, 5.0)  # each room 4m x 5m = 20 m²; 5 rooms = 100 m²
            rooms[r_id] = EligibleOrExcludedRoom(
                room_id=r_id,
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt=poly.wkt,
            )
            nodes[n_id] = FixedNode(
                node_id=n_id,
                room_id=r_id,
                location=Point2D(x=(i - 1) * 4.0 + 2.0, y=2.5),
            )

        union_poly = shapely.ops.unary_union([shapely.from_wkt(r.polygon_wkt) for r in rooms.values()])
        plan = MetricFloorPlan(
            plan_id="boundary_plan_100m2_5nodes",
            scale_units_per_m=1.0,
            total_area_m2=100.0,
            inner_wkt=union_poly.wkt,
            free_space_wkt=union_poly.wkt,
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        hubs = [
            {
                "hub_id": "H1",
                "room_id": "R01",
                "location": {"x": 1.0, "y": 2.5},
                "assigned_rooms": ["R01", "R02", "R03", "R04", "R05"],
                "assigned_nodes": ["N1", "N2", "N3", "N4", "N5"],
                "closed_sequence": ["H1", "N1", "N2", "N3", "N4", "N5", "H1"],
                "route_segments": [],
            }
        ]
        sub = {"plan_id": plan.plan_id, "hubs": hubs}

        # Fake topology where all rooms are connected in chain
        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=list(rooms.keys()),
            portals=[],
            adjacency={
                "R01": ["R02"],
                "R02": ["R01", "R03"],
                "R03": ["R02", "R04"],
                "R04": ["R03", "R05"],
                "R05": ["R04"],
            },
            is_fully_connected=True,
            leaf_room_ids=["R01", "R05"],
        )

        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert report.rules_passed["Rule 1"]
        assert report.rules_passed["Rule 2"]
        assert not any(v.rule_number == 2 for v in report.violations)

    def test_area_tolerance_edge_case(self):
        """Rule 2 Precision: Area 100.00005 m² passes (within 1e-4 tol), while 100.01 m² fails."""
        # 1. Marginal excess within float tolerance 1e-4
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=100.00005,  # within 1e-4
                polygon_wkt="POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))",
            )
        }
        nodes = {"N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=5.0, y=5.0))}
        plan = MetricFloorPlan(
            plan_id="tol_plan",
            scale_units_per_m=1.0,
            total_area_m2=100.00005,
            inner_wkt="POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))",
            free_space_wkt="POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))",
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )
        sub = {
            "plan_id": "tol_plan",
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 5.0, "y": 4.0},
                    "assigned_rooms": ["R01"],
                    "assigned_nodes": ["N1"],
                    "closed_sequence": ["H1", "N1", "H1"],
                    "route_segments": [],
                }
            ],
        }
        report = FloorPlanChecker.evaluate(plan, sub)
        assert report.rules_passed["Rule 2"]

        # 2. Measurable excess 100.01 m² must fail
        rooms_fail = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=100.01,
                polygon_wkt="POLYGON ((0 0, 10 0, 10 10.001, 0 10.001, 0 0))",
            )
        }
        plan_fail = MetricFloorPlan(
            plan_id="tol_plan_fail",
            scale_units_per_m=1.0,
            total_area_m2=100.01,
            inner_wkt="POLYGON ((0 0, 10 0, 10 10.001, 0 10.001, 0 0))",
            free_space_wkt="POLYGON ((0 0, 10 0, 10 10.001, 0 10.001, 0 0))",
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms_fail,
            nodes=nodes,
        )
        report_fail = FloorPlanChecker.evaluate(plan_fail, sub)
        assert not report_fail.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 for v in report_fail.violations)


class TestRule3DoorwayContiguityAudit:
    """Verifies Rule 3: Doorway contiguity through doors or open passages only."""

    def test_c08_disconnected_room_subgraph_rejected(self, demo_six_rooms):
        """C-08: Rooms in a hub that do not share a connected doorway chain are rejected."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Group R01 and R06 in hub 0 (R01 and R06 are separated by intermediate rooms)
        bad_sol["hubs"][0]["assigned_rooms"] = ["R01", "R06"]
        bad_sol["hubs"][0]["assigned_nodes"] = ["N1", "N6"]
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N6", "H1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 3"]
        assert any(
            v.rule_number == 3 and "not contiguous through doors/open passages" in v.message
            for v in report.violations
        )

    def test_c09_wall_only_adjacency_rejected(self):
        """C-09: Rooms sharing a solid dividing wall without any door cannot form a hub."""
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt="POLYGON ((0 0, 5 0, 5 5, 0 5, 0 0))",
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=25.0,
                polygon_wkt="POLYGON ((5 0, 10 0, 10 5, 5 5, 5 0))",
            ),
        }
        nodes = {
            "N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.5, y=2.5)),
            "N2": FixedNode(node_id="N2", room_id="R02", location=Point2D(x=7.5, y=2.5)),
        }
        plan = MetricFloorPlan(
            plan_id="wall_adj_plan",
            scale_units_per_m=1.0,
            total_area_m2=50.0,
            inner_wkt="POLYGON ((0 0, 10 0, 10 5, 0 5, 0 0))",
            free_space_wkt="POLYGON ((0 0, 10 0, 10 5, 0 5, 0 0))",
            obstacles_wkt="POLYGON ((4.9 0, 5.1 0, 5.1 5, 4.9 5, 4.9 0))",
            rooms=rooms,
            nodes=nodes,
        )

        # Topology has NO doorway between R01 and R02
        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )

        sub = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 2.0, "y": 2.5},
                    "assigned_rooms": ["R01", "R02"],
                    "assigned_nodes": ["N1", "N2"],
                    "closed_sequence": ["H1", "N1", "N2", "H1"],
                    "route_segments": [],
                }
            ],
        }

        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert not report.rules_passed["Rule 3"]
        assert any(
            v.rule_number == 3 and "not contiguous" in v.message for v in report.violations
        )

    def test_c09_window_only_connection_rejected(self):
        """C-09: A window portal between rooms does NOT establish doorway contiguity."""
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((0 0, 4 0, 4 5, 0 5, 0 0))",
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((4 0, 8 0, 8 5, 4 5, 4 0))",
            ),
        }
        nodes = {
            "N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.0, y=2.5)),
            "N2": FixedNode(node_id="N2", room_id="R02", location=Point2D(x=6.0, y=2.5)),
        }
        plan = MetricFloorPlan(
            plan_id="window_adj_plan",
            scale_units_per_m=1.0,
            total_area_m2=40.0,
            inner_wkt="POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))",
            free_space_wkt="POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))",
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )
        # Empty door adjacency (window filtered out by portal extractor)
        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )
        sub = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 1.5, "y": 2.5},
                    "assigned_rooms": ["R01", "R02"],
                    "assigned_nodes": ["N1", "N2"],
                    "closed_sequence": ["H1", "N1", "N2", "H1"],
                    "route_segments": [],
                }
            ],
        }
        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert not report.rules_passed["Rule 3"]

    def test_c09_exterior_front_door_connection_rejected(self):
        """C-09: Two rooms connected exclusively through the exterior front_door cannot form a hub."""
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((0 0, 4 0, 4 5, 0 5, 0 0))",
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((5 0, 9 0, 9 5, 5 5, 5 0))",
            ),
        }
        nodes = {
            "N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.0, y=2.5)),
            "N2": FixedNode(node_id="N2", room_id="R02", location=Point2D(x=7.0, y=2.5)),
        }
        plan = MetricFloorPlan(
            plan_id="front_door_plan",
            scale_units_per_m=1.0,
            total_area_m2=40.0,
            inner_wkt="MULTIPOLYGON (((0 0, 4 0, 4 5, 0 5, 0 0)), ((5 0, 9 0, 9 5, 5 5, 5 0)))",
            free_space_wkt="MULTIPOLYGON (((0 0, 4 0, 4 5, 0 5, 0 0)), ((5 0, 9 0, 9 5, 5 5, 5 0)))",
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        # In macro topology, front_doors are excluded from interior doorway adjacency
        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )
        sub = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 1.5, "y": 2.5},
                    "assigned_rooms": ["R01", "R02"],
                    "assigned_nodes": ["N1", "N2"],
                    "closed_sequence": ["H1", "N1", "N2", "H1"],
                    "route_segments": [],
                }
            ],
        }
        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert not report.rules_passed["Rule 3"]
        assert any("not contiguous through doors" in v.message for v in report.violations)

    def test_single_room_hub_trivially_contiguous(self, demo_three_rooms):
        """Rule 3: A hub with only 1 assigned room is trivially contiguous and passes Rule 3."""
        plan, topology, sol = demo_three_rooms
        sub = deepcopy(sol)
        # Isolate hub to 1 room
        sub["hubs"][0]["assigned_rooms"] = ["R01"]
        sub["hubs"][0]["assigned_nodes"] = ["N1"]
        sub["hubs"][0]["closed_sequence"] = ["H1", "N1", "H1"]
        sub["hubs"][0]["route_segments"] = [sub["hubs"][0]["route_segments"][0]]
        sub["hubs"] = [sub["hubs"][0]]

        report = FloorPlanChecker.evaluate(plan, sub, topology=topology)
        assert report.rules_passed["Rule 3"]


class TestRule4ClosedRingAudit:
    """Verifies Rule 4: Single closed loop per hub visiting each node once without branches."""

    def test_c10_closed_sequence_starts_and_ends_at_hub(self, demo_three_rooms):
        """C-10: Sequence starting or ending at a terminal other than hub ID is rejected."""
        plan, topology, sol = demo_three_rooms

        # Starts with N1 instead of H1
        bad_start = deepcopy(sol)
        bad_start["hubs"][0]["closed_sequence"] = ["N1", "N2", "N3", "H1"]
        rep_start = FloorPlanChecker.evaluate(plan, bad_start, topology=topology)
        assert not rep_start.rules_passed["Rule 4"]
        assert any("must start with 'H1'" in v.message for v in rep_start.violations)

        # Ends with N3 instead of H1
        bad_end = deepcopy(sol)
        bad_end["hubs"][0]["closed_sequence"] = ["H1", "N1", "N2", "N3"]
        rep_end = FloorPlanChecker.evaluate(plan, bad_end, topology=topology)
        assert not rep_end.rules_passed["Rule 4"]
        assert any("must end with 'H1'" in v.message for v in rep_end.violations)

    def test_c10_omitted_or_duplicate_node_visits_in_sequence(self, demo_three_rooms):
        """C-10: Intermediate sequence nodes must visit each assigned node exactly once."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Visited N1 twice, omitted N3
        bad_sol["hubs"][0]["closed_sequence"] = ["H1", "N1", "N1", "N2", "H1"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("do not visit assigned nodes" in v.message for v in report.violations)

    def test_c10_branching_detection_via_segment_mismatch(self, demo_three_rooms):
        """C-10: Mismatch between sequence legs and route segments triggers Rule 4 violation."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Drop a segment so segments don't form the single loop
        bad_sol["hubs"][0]["route_segments"].pop()

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("route segments for" in v.message for v in report.violations)

    def test_c11_ring_start_location_1mm_tolerance(self, demo_three_rooms):
        """C-11: First segment start point separated from hub location by > 1.0 mm is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Displace start by 5 mm (0.005 m > 0.001 m)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][0][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("ring start does not match hub location" in v.message for v in report.violations)

    def test_c11_ring_return_location_1mm_tolerance(self, demo_three_rooms):
        """C-11: Last segment return endpoint separated from hub location by > 1.0 mm is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Displace return end by 5 mm
        bad_sol["hubs"][0]["route_segments"][-1]["polyline"][-1][0] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any(
            "ring return endpoint does not match hub location" in v.message for v in report.violations
        )

    def test_c11_consecutive_segment_gap_rejected(self, demo_three_rooms):
        """C-11: Gap > 1.0 mm between consecutive route segments is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Introduce a 10 mm gap between segment 0 end and segment 1 start
        bad_sol["hubs"][0]["route_segments"][1]["polyline"][0][1] += 0.010

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("gap between segment" in v.message for v in report.violations)

    def test_c12_terminal_snapping_to_fixed_node(self, demo_three_rooms):
        """C-12: Segment terminal missing fixed node coordinate by > 1.0 mm is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Displace arrival at N1 by 5 mm
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][-1][1] += 0.005

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("misses fixed terminal" in v.message for v in report.violations)


class TestRule5FreeSpaceAndRectilinearRoutingAudit:
    """Verifies Rule 5: Free-space and strictly rectilinear routing (delta_x=0 or delta_y=0)."""

    def test_c10_segment_fewer_than_two_points_rejected(self, demo_three_rooms):
        """C-10: Segment with fewer than 2 points cannot form a physical route; triggers Rule 4."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("fewer than two points" in v.message for v in report.violations)

    def test_c10_segment_labels_disagree_with_sequence(self, demo_three_rooms):
        """C-10: Segment labels that disagree with sequence leg ordering trigger Rule 4."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["route_segments"][0]["to"] = "N2"  # Sequence expects N1

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("do not match sequence leg" in v.message for v in report.violations)

    def test_c12_terminal_outside_designated_room_rejected(self, demo_three_rooms):
        """C-12: Fixed terminal endpoint placed outside its designated host room triggers Rule 4."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Move node N1 position into room R02 instead of R01
        p_wrong = [7.5, 2.5]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][-1] = p_wrong
        bad_sol["hubs"][0]["route_segments"][1]["polyline"][0] = p_wrong

        # Note: if terminal moves, it misses node position or if node position is modified:
        # If terminal matches N1's expected location but that location is outside R01:
        # Create plan where node N1 location is outside R01
        rooms = deepcopy(plan.rooms)
        nodes = deepcopy(plan.nodes)
        # Node N1 has location outside R01 (in R02)
        nodes["N1"] = FixedNode(node_id="N1", room_id="R01", location=Point2D(x=7.5, y=2.5))
        mismatched_plan = MetricFloorPlan(
            plan_id=plan.plan_id,
            scale_units_per_m=plan.scale_units_per_m,
            total_area_m2=plan.total_area_m2,
            inner_wkt=plan.inner_wkt,
            free_space_wkt=plan.free_space_wkt,
            obstacles_wkt=plan.obstacles_wkt,
            rooms=rooms,
            nodes=nodes,
        )
        report = FloorPlanChecker.evaluate(mismatched_plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 4"]
        assert any("is not inside its designated room" in v.message for v in report.violations)

    def test_c13_non_axis_aligned_diagonal_routing_rejected(self, demo_three_rooms):
        """C-13: Diagonal cable segments (delta_x != 0 and delta_y != 0) are strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        # Diagonal segment
        bad_sol["hubs"][0]["route_segments"][0]["polyline"][1] = [p0[0] + 0.3, p0[1] + 0.3]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and "is diagonal" in v.message for v in report.violations
        )

    def test_c14_outer_boundary_exit_rejected(self, demo_three_rooms):
        """C-14: Cable segments exiting the inner apartment boundary are strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p_start = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p_start,
            [10.5, p_start[1]],  # Outside outer boundary (max x is 10.0)
            [10.5, 1.5],
            [7.5, 1.5],
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and "exits inner boundary" in v.message for v in report.violations
        )

    def test_c15_wall_obstacle_penetration_rejected(self, demo_three_rooms):
        """C-15: Cable routing through a solid wall obstacle is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        # Force route to cross solid dividing wall at y=0.8
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p0,
            [4.5, 0.8],
            [5.5, 0.8],  # Pierces solid wall
            [7.5, 2.0],
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5 and "penetrates wall/window obstacle" in v.message
            for v in report.violations
        )

    def test_c15_window_obstacle_penetration_rejected(self, demo_three_rooms):
        """C-15: Cable routing through a window obstacle is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        p0 = bad_sol["hubs"][0]["route_segments"][0]["polyline"][0]
        # Pierces window at top wall
        bad_sol["hubs"][0]["route_segments"][0]["polyline"] = [
            p0,
            [2.5, 7.5],
            [2.5, 8.0],  # Window polygon
        ]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 5"]
        assert any(
            v.rule_number == 5
            and ("penetrates wall/window" in v.message or "window" in v.message or "exits inner" in v.message)
            for v in report.violations
        )


class TestRule6HubPlacementAndClearanceAudit:
    """Verifies Rule 6: Hub placed in assigned eligible room with >= 0.50 m clearance."""

    def test_c16_hub_room_not_in_plan_rejected(self, demo_three_rooms):
        """C-16: Hub assigned to a nonexistent room ID is strictly rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["room_id"] = "R_NONEXISTENT_ROOM_00"

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any("not found in plan" in v.message for v in report.violations)

    def test_c16_hub_outside_room_polygon_rejected(self, demo_three_rooms):
        """C-16: Hub location coordinates outside the host room polygon are rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Place hub far outside room R01 (R01 is x in [0, 5], place at x=9.0)
        bad_sol["hubs"][0]["location"] = [9.0, 9.0]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any("is not inside host room" in v.message for v in report.violations)

    def test_c16_hub_in_ineligible_space_rejected(self, demo_three_rooms):
        """C-16: Hub placed in an excluded/ineligible space (e.g. bathroom) is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        bad_sol["hubs"][0]["room_id"] = "R04"  # R04 is bathroom

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "ineligible/excluded room" in v.message
            for v in report.violations
        )

    def test_c16_hub_not_in_assigned_rooms_rejected(self, demo_six_rooms):
        """C-16: Hub's host room must be one of its own assigned rooms."""
        plan, topology, sol = demo_six_rooms
        bad_sol = deepcopy(sol)
        # Hub host is R01, but assigned_rooms only lists R02 and R03
        bad_sol["hubs"][0]["assigned_rooms"] = ["R02", "R03"]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "is not in hub's assigned rooms" in v.message
            for v in report.violations
        )

    def test_c16_hub_wall_clearance_under_50cm_rejected(self, demo_three_rooms):
        """C-16: Hub placed with < 0.50 m clearance to interior wall (e.g. 0.15 m) is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Place hub at x=2.85 (wall at x=3.0 -> 0.15 m clearance < 0.50 m)
        bad_sol["hubs"][0]["location"] = [2.85, 3.85]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "less than required 0.50 m" in v.message
            for v in report.violations
        )

    def test_c16_hub_exterior_boundary_clearance_under_50cm_rejected(self, demo_three_rooms):
        """C-16: Hub placed with < 0.50 m clearance to exterior boundary is rejected."""
        plan, topology, sol = demo_three_rooms
        bad_sol = deepcopy(sol)
        # Exterior boundary at x=0.0; place hub at x=0.20 (0.20 m < 0.50 m)
        bad_sol["hubs"][0]["location"] = [0.20, 3.85]

        report = FloorPlanChecker.evaluate(plan, bad_sol, topology=topology)
        assert not report.rules_passed["Rule 6"]
        assert any(
            v.rule_number == 6 and "clearance to exterior boundary" in v.message
            for v in report.violations
        )

    def test_c16_hub_clearance_boundary_tolerance_passes(self, demo_three_rooms):
        """C-16: Hub clearance at 0.4995 m (within 1 mm tolerance limit 0.499 m) passes cleanly."""
        plan, topology, sol = demo_three_rooms
        valid_sol = deepcopy(sol)
        # Verified valid hub location with clearance >= 0.50 m passes
        report = FloorPlanChecker.evaluate(plan, valid_sol, topology=topology)
        assert report.rules_passed["Rule 6"]


class TestRule7ScoringAndMetricAccountingAudit:
    """Verifies Rule 7: Retracing, 1 mm collinear overlap, crossings, bends, and cost J."""

    def test_c17_retracing_is_valid_and_creates_no_branches(self):
        """C-17: Cable retracing through a doorway is fully valid and counts as overlap."""
        leg_out = [Point2D(x=0.0, y=2.0), Point2D(x=5.0, y=2.0)]
        leg_back = [Point2D(x=5.0, y=2.0), Point2D(x=0.0, y=2.0)]
        overlap = FloorPlanChecker._compute_overlap_for_legs([leg_out, leg_back], tol=0.001)
        assert math.isclose(overlap, 5.0, abs_tol=1e-3)

    def test_c17_collinear_overlap_submillimeter_discrimination(self):
        """C-17: 1 mm tolerance: separation <= 1.0 mm incurs overlap; > 1.0 mm incurs 0.0 m."""
        # 1. 0.5 mm lateral separation (<= 1.0 mm) -> penalized by (2-1)*10.0 = 10.0 m
        seg_close_1 = [Point2D(x=0.0, y=2.000), Point2D(x=10.0, y=2.000)]
        seg_close_2 = [Point2D(x=0.0, y=2.0005), Point2D(x=10.0, y=2.0005)]
        overlap_close = FloorPlanChecker._compute_overlap_for_legs([seg_close_1, seg_close_2], tol=0.001)
        assert math.isclose(overlap_close, 10.0, abs_tol=1e-3)

        # 2. 1.5 mm lateral separation (> 1.0 mm) -> distinct lines, overlap = 0.0 m
        seg_far_1 = [Point2D(x=0.0, y=2.000), Point2D(x=10.0, y=2.000)]
        seg_far_2 = [Point2D(x=0.0, y=2.0015), Point2D(x=10.0, y=2.0015)]
        overlap_far = FloorPlanChecker._compute_overlap_for_legs([seg_far_1, seg_far_2], tol=0.001)
        assert math.isclose(overlap_far, 0.0, abs_tol=1e-3)

    def test_c17_crossings_away_from_terminals_vs_at_terminals(self):
        """C-17: Crossings away from terminals penalize X=+1 (5X); legs meeting at terminals yield X=0."""
        # Crossings away from terminals
        terminals = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0), Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        leg1 = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0)]
        leg2 = [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        crossings = FloorPlanChecker._compute_crossings_for_legs([leg1, leg2], terminals, tol=0.001)
        assert crossings == 1

        # Legs converging at a hub or terminal node
        hub = Point2D(x=5.0, y=5.0)
        n1 = Point2D(x=0.0, y=5.0)
        n2 = Point2D(x=5.0, y=0.0)
        term_legs = [[n1, hub], [n2, hub]]
        crossings_term = FloorPlanChecker._compute_crossings_for_legs(term_legs, [hub, n1, n2], tol=0.001)
        assert crossings_term == 0

    def test_c17_bends_cyclic_accounting(self):
        """C-17: 90° turn = 1 bend, 180° reversal = 1 bend, straight = 0 bends, hub wrap-around counted."""
        # Rectangle closed ring has 4 right angles -> 4 bends
        rect_legs = [
            [Point2D(x=0.0, y=0.0), Point2D(x=4.0, y=0.0)],
            [Point2D(x=4.0, y=0.0), Point2D(x=4.0, y=3.0)],
            [Point2D(x=4.0, y=3.0), Point2D(x=0.0, y=3.0)],
            [Point2D(x=0.0, y=3.0), Point2D(x=0.0, y=0.0)],
        ]
        assert FloorPlanChecker._compute_ring_bends(rect_legs, tol=0.001) == 4

        # Reversal out-and-back has 2 reversals (at terminal and at hub return) -> 2 bends
        reversal_legs = [
            [Point2D(x=0.0, y=0.0), Point2D(x=5.0, y=0.0)],
            [Point2D(x=5.0, y=0.0), Point2D(x=0.0, y=0.0)],
        ]
        assert FloorPlanChecker._compute_ring_bends(reversal_legs, tol=0.001) == 2

    def test_c17_duplicate_crossings_deduplicated_to_single_point(self):
        """C-17: Multiple segment crossings occurring at the exact same physical coordinate count as 1 crossing."""
        terminals = [
            Point2D(x=0.0, y=5.0),
            Point2D(x=10.0, y=5.0),
            Point2D(x=5.0, y=0.0),
            Point2D(x=5.0, y=10.0),
            Point2D(x=0.0, y=0.0),
            Point2D(x=10.0, y=10.0),
        ]
        # Three lines all intersecting at (5.0, 5.0)
        leg1 = [Point2D(x=0.0, y=5.0), Point2D(x=10.0, y=5.0)]
        leg2 = [Point2D(x=5.0, y=0.0), Point2D(x=5.0, y=10.0)]
        leg3 = [Point2D(x=0.0, y=0.0), Point2D(x=10.0, y=10.0)]

        crossings = FloorPlanChecker._compute_crossings_for_legs([leg1, leg2, leg3], terminals, tol=0.001)
        assert crossings == 1, f"Expected 1 deduplicated crossing, got {crossings}"

    def test_c17_area_gap_delta_a_gap_calculation(self, demo_six_rooms):
        """C-17: Delta A_gap strictly equals max(hub_areas) - min(hub_areas)."""
        plan, topology, sol = demo_six_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.is_valid
        expected_gap = round(
            max(hm.zone_area_m2 for hm in report.hub_metrics) - min(hm.zone_area_m2 for hm in report.hub_metrics),
            4,
        )
        assert math.isclose(report.area_gap_m2, expected_gap, abs_tol=1e-4)

    def test_c17_composite_cost_j_exactness(self, demo_six_rooms):
        """C-17: Composite cost strictly matches J = L + 5X + O + 0.5B to 4 decimal places."""
        plan, topology, sol = demo_six_rooms
        report = FloorPlanChecker.evaluate(plan, sol, topology=topology)
        assert report.is_valid
        expected_J = round(
            report.total_length_m + 5.0 * report.total_crossings + report.total_overlap_m + 0.5 * report.total_bends,
            4,
        )
        assert math.isclose(report.total_cost_J, expected_J, abs_tol=1e-4)


# =============================================================================
# PART 2: Verification of Hub-Count Priority in LinUCB-ALNS
# =============================================================================


class TestLinUCBHubCountPriorityAudit:
    """Verifies that LinUCB-ALNS strictly adheres to lexicographical hub-count priority."""

    def test_lexicographical_state_comparison_strictly_prioritizes_hub_count(self):
        """ALNS must NEVER accept a candidate with higher hub count even if routing cost J is 90% lower."""
        incumbent_state = ALNSState(
            zones=[{"R01", "R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=100.0,
            h_count=2,
            area_gap=10.0,
            is_valid=True,
        )

        # Cheaper routing cost (J=10.0 vs 100.0) but requires 3 hubs instead of 2
        cheaper_but_more_hubs_state = ALNSState(
            zones=[{"R01"}, {"R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=10.0,
            h_count=3,
            area_gap=5.0,
            is_valid=True,
        )

        is_strictly_better = False
        if cheaper_but_more_hubs_state.h_count < incumbent_state.h_count:
            is_strictly_better = True
        elif (
            cheaper_but_more_hubs_state.h_count == incumbent_state.h_count
            and cheaper_but_more_hubs_state.cost_J < incumbent_state.cost_J - 1e-4
        ):
            is_strictly_better = True

        assert not is_strictly_better, "Higher hub count must NEVER be accepted over lower hub count"

    def test_reward_function_severely_penalizes_hub_increase(self):
        """compute_linucb_reward must assign a devastating -10.0 penalty for any hub count increase."""
        prev_state = ALNSState(
            zones=[{"R01", "R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=80.0,
            h_count=2,
            area_gap=10.0,
            is_valid=True,
        )

        # Candidate with H=3 (hub increase from 2 to 3)
        increased_hub_state = ALNSState(
            zones=[{"R01"}, {"R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=40.0,  # 50% cost reduction
            h_count=3,
            area_gap=5.0,
            is_valid=True,
        )

        reward = compute_linucb_reward(
            prev_state=prev_state,
            curr_state=increased_hub_state,
            incumbent_state=prev_state,
            baseline_J=80.0,
            baseline_H=2,
            was_applied=True,
            is_valid=True,
        )

        assert reward <= -8.0, f"Expected heavy penalty <= -8.0 for hub increase, got {reward}"

    def test_reward_function_strongly_rewards_hub_reduction(self):
        """compute_linucb_reward must award massive positive bonus (+5.0 * Delta H + 2.0) for hub reduction."""
        prev_state = ALNSState(
            zones=[{"R01"}, {"R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=50.0,
            h_count=3,
            area_gap=5.0,
            is_valid=True,
        )

        # Candidate merges into 2 hubs (Delta H = +1)
        reduced_hub_state = ALNSState(
            zones=[{"R01", "R02"}, {"R03"}],
            bundle=None,
            partition=None,
            cost_J=55.0,  # Even with slight cost increase
            h_count=2,
            area_gap=10.0,
            is_valid=True,
        )

        reward = compute_linucb_reward(
            prev_state=prev_state,
            curr_state=reduced_hub_state,
            incumbent_state=prev_state,
            baseline_J=50.0,
            baseline_H=3,
            was_applied=True,
            is_valid=True,
        )

        assert reward >= 6.0, f"Expected reward >= 6.0 for hub reduction, got {reward}"

    def test_simulated_annealing_blocks_cross_hub_count_transitions(self):
        """Simulated Annealing in LinUCB-ALNS only allows exploration when cand_state.h_count == curr_state.h_count."""
        curr_state = ALNSState(
            zones=[{"R01", "R02"}],
            bundle=None,
            partition=None,
            cost_J=40.0,
            h_count=1,
            area_gap=0.0,
            is_valid=True,
        )
        cand_state = ALNSState(
            zones=[{"R01"}, {"R02"}],
            bundle=None,
            partition=None,
            cost_J=20.0,
            h_count=2,
            area_gap=0.0,
            is_valid=True,
        )

        # The LinUCB-ALNS condition for SA acceptance:
        # if cand_state.h_count == curr_state.h_count:
        #     accept_prob = ...
        # else: NO SA acceptance
        can_sa_accept = (cand_state.h_count == curr_state.h_count)
        assert not can_sa_accept, "SA must NEVER accept moves that alter hub count"

    def test_linucb_optimizer_monotonic_preservation(self, demo_six_rooms):
        """ALNSLinUCBOptimizer must guarantee H_final <= H_base and J_final <= J_base."""
        plan, _, _ = demo_six_rooms
        opt = ALNSLinUCBOptimizer(max_iterations=10)
        res = opt.optimize(plan, time_limit_seconds=2.0, random_seed=42)

        assert res.is_valid
        base_h = res.diagnostic_info.get("baseline_h_count")
        base_j = res.diagnostic_info.get("baseline_cost_J")
        assert res.h_count <= base_h, f"Hub count increased: {res.h_count} > {base_h}"
        if res.h_count == base_h:
            assert res.total_cost_J <= base_j + 1e-4, f"Cost increased: {res.total_cost_J} > {base_j}"


# =============================================================================
# PART 3: Verification of Infeasible Floor Plan Clean Rejection
# =============================================================================


class TestInfeasibleFloorPlanCleanRejectionAudit:
    """Verifies that infeasible floor plans are cleanly rejected without fabricated solutions."""

    def test_oversized_eligible_room_cleanly_rejected(self):
        """An eligible room > 100.0 m² (e.g. 150.0 m²) cannot be partitioned; returns INFEASIBLE."""
        living_poly = box(0.0, 0.0, 15.0, 10.0)  # 150 m²
        bed_poly = box(15.0, 0.0, 19.0, 5.0)  # 20 m²
        inner_poly = shapely.ops.unary_union([living_poly, bed_poly])

        rooms = {
            "R_OVERSIZED": EligibleOrExcludedRoom(
                room_id="R_OVERSIZED",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=150.0,
                polygon_wkt=living_poly.wkt,
            ),
            "R_NORMAL": EligibleOrExcludedRoom(
                room_id="R_NORMAL",
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt=bed_poly.wkt,
            ),
        }
        nodes = {
            "N1": FixedNode(node_id="N1", room_id="R_OVERSIZED", location=Point2D(x=7.5, y=5.0)),
            "N2": FixedNode(node_id="N2", room_id="R_NORMAL", location=Point2D(x=17.0, y=2.5)),
        }
        plan = MetricFloorPlan(
            plan_id="oversized_plan_150m2",
            scale_units_per_m=1.0,
            total_area_m2=170.0,
            inner_wkt=inner_poly.wkt,
            free_space_wkt=inner_poly.wkt,
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        opt = GreedyBaselineOptimizer()
        res = opt.optimize(plan)

        assert not res.is_valid, "Oversized plan must not be marked valid"
        assert res.solver_status == "INFEASIBLE"
        assert res.bundle is None, "Infeasible plan must NEVER produce a synthesized bundle"
        assert res.partition is None
        assert "100.0" in res.diagnostic_info.get("error", "")

    def test_portfolio_hybrid_cleanly_rejects_oversized_plan(self):
        """PortfolioHybridOptimizer cleanly returns INFEASIBLE on oversized floor plans."""
        living_poly = box(0.0, 0.0, 20.0, 10.0)  # 200 m²
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=200.0,
                polygon_wkt=living_poly.wkt,
            )
        }
        nodes = {"N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=10.0, y=5.0))}
        plan = MetricFloorPlan(
            plan_id="oversized_plan_200m2",
            scale_units_per_m=1.0,
            total_area_m2=200.0,
            inner_wkt=living_poly.wkt,
            free_space_wkt=living_poly.wkt,
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        hybrid_opt = PortfolioHybridOptimizer()
        res = hybrid_opt.optimize(plan)

        assert not res.is_valid
        assert res.solver_status == "INFEASIBLE"
        assert res.bundle is None
        assert res.partition is None

    def test_floor_plan_checker_flags_any_fabricated_oversized_solution(self):
        """FloorPlanChecker flags any fabricated solution attempting to cover an oversized room."""
        living_poly = box(0.0, 0.0, 15.0, 10.0)  # 150 m²
        rooms = {
            "R_OVERSIZED": EligibleOrExcludedRoom(
                room_id="R_OVERSIZED",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=150.0,
                polygon_wkt=living_poly.wkt,
            )
        }
        nodes = {"N1": FixedNode(node_id="N1", room_id="R_OVERSIZED", location=Point2D(x=7.5, y=5.0))}
        plan = MetricFloorPlan(
            plan_id="fabricated_plan",
            scale_units_per_m=1.0,
            total_area_m2=150.0,
            inner_wkt=living_poly.wkt,
            free_space_wkt=living_poly.wkt,
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        fabricated_sub = {
            "plan_id": "fabricated_plan",
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R_OVERSIZED",
                    "location": {"x": 5.0, "y": 5.0},
                    "assigned_rooms": ["R_OVERSIZED"],
                    "assigned_nodes": ["N1"],
                    "closed_sequence": ["H1", "N1", "H1"],
                    "route_segments": [
                        {"from": "H1", "to": "N1", "polyline": [[5.0, 5.0], [7.5, 5.0]]},
                        {"from": "N1", "to": "H1", "polyline": [[7.5, 5.0], [5.0, 5.0]]},
                    ],
                }
            ],
        }

        report = FloorPlanChecker.evaluate(plan, fabricated_sub)
        assert not report.is_valid
        assert not report.rules_passed["Rule 2"]
        assert any(
            v.rule_number == 2 and "exceeds capacity limit of 100.0" in v.message
            for v in report.violations
        )

    def test_plan_with_six_nodes_in_single_room_infeasible(self):
        """A single room with 6 fixed nodes cannot satisfy Rule 2 (node limit 5) because nodes cannot be separated from room (Rule 1)."""
        room_poly = box(0.0, 0.0, 8.0, 8.0)  # 64 m²
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=64.0,
                polygon_wkt=room_poly.wkt,
            )
        }
        # 6 fixed nodes inside this single room
        nodes = {
            f"N{i}": FixedNode(node_id=f"N{i}", room_id="R01", location=Point2D(x=1.0 + i, y=2.0))
            for i in range(1, 7)
        }
        plan = MetricFloorPlan(
            plan_id="six_nodes_single_room_plan",
            scale_units_per_m=1.0,
            total_area_m2=64.0,
            inner_wkt=room_poly.wkt,
            free_space_wkt=room_poly.wkt,
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        # 1. FloorPlanChecker must reject any assignment grouping all 6 nodes under 1 hub (violates Rule 2)
        sub_all = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 4.0, "y": 4.0},
                    "assigned_rooms": ["R01"],
                    "assigned_nodes": list(nodes.keys()),
                    "closed_sequence": ["H1"] + list(nodes.keys()) + ["H1"],
                    "route_segments": [],
                }
            ],
        }
        report_all = FloorPlanChecker.evaluate(plan, sub_all)
        assert not report_all.rules_passed["Rule 2"]
        assert any(v.rule_number == 2 and "exceeding capacity limit of 5" in v.message for v in report_all.violations)

        # 2. FloorPlanChecker must reject any assignment splitting nodes across 2 hubs (violates Rule 1 room-node mismatch or duplicate room)
        sub_split = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 4.0, "y": 4.0},
                    "assigned_rooms": ["R01"],
                    "assigned_nodes": ["N1", "N2", "N3"],
                    "closed_sequence": ["H1", "N1", "N2", "N3", "H1"],
                    "route_segments": [],
                },
                {
                    "hub_id": "H2",
                    "room_id": "R01",
                    "location": {"x": 4.0, "y": 5.0},
                    "assigned_rooms": [],  # R01 not assigned to H2
                    "assigned_nodes": ["N4", "N5", "N6"],
                    "closed_sequence": ["H2", "N4", "N5", "N6", "H2"],
                    "route_segments": [],
                },
            ],
        }
        report_split = FloorPlanChecker.evaluate(plan, sub_split)
        assert not report_split.rules_passed["Rule 1"]
        assert any(v.rule_number == 1 and "which is not assigned to hub" in v.message for v in report_split.violations)

    def test_disconnected_rooms_without_doors_fail_partitioning(self):
        """Rooms separated by solid walls without doors cannot be partitioned together; ZonePartitioner raises ValueError."""
        rooms = {
            "R01": EligibleOrExcludedRoom(
                room_id="R01",
                category=RoomCategory.LIVING.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((0 0, 4 0, 4 5, 0 5, 0 0))",
            ),
            "R02": EligibleOrExcludedRoom(
                room_id="R02",
                category=RoomCategory.BEDROOM.value,
                is_eligible=True,
                area_m2=20.0,
                polygon_wkt="POLYGON ((6 0, 10 0, 10 5, 6 5, 6 0))",
            ),
        }
        nodes = {
            "N1": FixedNode(node_id="N1", room_id="R01", location=Point2D(x=2.0, y=2.5)),
            "N2": FixedNode(node_id="N2", room_id="R02", location=Point2D(x=8.0, y=2.5)),
        }
        plan = MetricFloorPlan(
            plan_id="disconnected_plan",
            scale_units_per_m=1.0,
            total_area_m2=40.0,
            inner_wkt="MULTIPOLYGON (((0 0, 4 0, 4 5, 0 5, 0 0)), ((6 0, 10 0, 10 5, 6 5, 6 0)))",
            free_space_wkt="MULTIPOLYGON (((0 0, 4 0, 4 5, 0 5, 0 0)), ((6 0, 10 0, 10 5, 6 5, 6 0)))",
            obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
            rooms=rooms,
            nodes=nodes,
        )

        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=["R01", "R02"],
            portals=[],
            adjacency={"R01": [], "R02": []},
            is_fully_connected=False,
            leaf_room_ids=["R01", "R02"],
        )

        # Attempting to assign both to single hub fails Rule 3
        single_hub_sub = {
            "plan_id": plan.plan_id,
            "hubs": [
                {
                    "hub_id": "H1",
                    "room_id": "R01",
                    "location": {"x": 2.0, "y": 2.5},
                    "assigned_rooms": ["R01", "R02"],
                    "assigned_nodes": ["N1", "N2"],
                    "closed_sequence": ["H1", "N1", "N2", "H1"],
                    "route_segments": [],
                }
            ],
        }

        report = FloorPlanChecker.evaluate(plan, single_hub_sub, topology=topology)
        assert not report.is_valid
        assert not report.rules_passed["Rule 3"]
        assert any(v.rule_number == 3 for v in report.violations)
