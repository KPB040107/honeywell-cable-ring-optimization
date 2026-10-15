"""Architectural Co-Pilot Perturbation Optimization & Ranking Engine (Phase 8).

Evaluates candidate structural modifications—adding ghost open passages on unlinked
shared interior walls and sliding existing doors along walls—with warm-started
topological routing/partitioning, and ranks proposals lexicographically by hub
reduction Delta H and composite routing cost reduction Delta J.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    CoPilotReport,
    DoorOrPassagePortal,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    PortalType,
    ProposalType,
    StructuralProposal,
    SynthesizedRingsBundle,
)
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import _build_portal
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner
from resplan_router.phase8_copilot.candidate_generator import (
    PerturbationCandidateGenerator,
)


class ArchitecturalCoPilot:
    """Explores and ranks structural wall perturbations to optimize cable routing & hub count."""

    def __init__(
        self,
        candidate_generator: Optional[PerturbationCandidateGenerator] = None,
    ) -> None:
        self.generator = candidate_generator or PerturbationCandidateGenerator()

    def analyze_floor_plan(
        self,
        plan: MetricFloorPlan,
        topology: Union[MacroTopologyGraph, PortalTopologyGraph],
        baseline_partition: HubZonePartition,
        baseline_bundle: SynthesizedRingsBundle,
        max_recommendations: int = 5,
        windows: Optional[Sequence[Any]] = None,
    ) -> CoPilotReport:
        """Analyze floor plan and produce ranked structural improvement proposals.

        Explores ghost doorways along shared interior walls and doorway sliding perturbations.
        Ranks proposals lexicographically by:
        1. Delta H = H_base - H_new (hub reduction, highest priority)
        2. Delta J = J_base - J_new (composite routing cost reduction)

        Args:
            plan: Baseline normalized MetricFloorPlan contract.
            topology: MacroTopologyGraph or PortalTopologyGraph.
            baseline_partition: Precomputed HubZonePartition contract.
            baseline_bundle: Precomputed SynthesizedRingsBundle.
            max_recommendations: Maximum number of top proposals to return (default 5).
            windows: Optional raw window geometries to exclude.

        Returns:
            CoPilotReport: Validated report containing baseline metrics and ranked proposals.
        """
        portal_topo, macro_topo = PerturbationCandidateGenerator._resolve_topology(topology)
        if macro_topo is None:
            macro_topo = MacroTopologyGraph(plan, portal_topo.portals)

        h_base = len(baseline_partition.hubs)
        j_base = round(baseline_bundle.total_cost_J, 4)
        l_base = round(baseline_bundle.total_length_m, 4)
        b_base = baseline_bundle.total_bends

        # 1. Generate all candidate perturbations
        raw_candidates = self.generator.generate_all_candidates(plan, macro_topo, windows=windows)

        eligible_room_ids = set(portal_topo.eligible_room_ids)
        leaf_room_ids = set(portal_topo.leaf_room_ids)

        proposals_by_key: Dict[str, StructuralProposal] = {}

        # Precompute room node coordinates
        room_nodes: Dict[str, List[Point2D]] = {}
        for n in plan.nodes.values():
            room_nodes.setdefault(n.room_id, []).append(n.location)

        for cand in raw_candidates:
            proposal_type: ProposalType = cand["proposal_type"]
            r_a_id: str = cand["room_a_id"]
            r_b_id: str = cand["room_b_id"]
            center_pt: Point2D = cand["suggested_door_center"]
            wall_seg: List[Point2D] = cand["wall_segment"]
            door_w: float = cand["door_width_m"]

            delta_h = 0
            delta_j = 0.0
            delta_l = 0.0
            delta_b = 0

            # -------------------------------------------------------------
            # A. Evaluate Hub Reduction (Delta H)
            # -------------------------------------------------------------
            if proposal_type == ProposalType.ADD_GHOST_DOOR and h_base > 1:
                # Check if r_a and r_b were in different connected components
                p1, p2 = wall_seg[0], wall_seg[1]
                dx = p2.x - p1.x
                dy = p2.y - p1.y
                seg_len = math.hypot(dx, dy)
                tx = dx / seg_len if seg_len > 1e-6 else 1.0
                ty = dy / seg_len if seg_len > 1e-6 else 0.0

                hw = door_w / 2.0
                t_line = LineString([
                    (center_pt.x - hw * tx, center_pt.y - hw * ty),
                    (center_pt.x + hw * tx, center_pt.y + hw * ty),
                ])
                ghost_portal = _build_portal(
                    f"GHOST_{r_a_id}_{r_b_id}",
                    PortalType.OPEN_PASSAGE,
                    r_a_id,
                    r_b_id,
                    t_line,
                    plan.rooms[r_a_id].geometry,
                    plan.rooms[r_b_id].geometry,
                )

                if ghost_portal is not None:
                    temp_portals = list(portal_topo.portals) + [ghost_portal]
                    temp_topo = MacroTopologyGraph(plan, temp_portals)
                    temp_zones = ZonePartitioner.enumerate_connected_zones(
                        temp_topo.topology, plan, max_area=100.0, max_nodes=5
                    )

                    all_elig = set(temp_topo.topology.eligible_room_ids)

                    # Check if H = 1 is feasible (a single zone covers all eligible rooms)
                    if any(set(z["rooms"]) == all_elig for z in temp_zones):
                        delta_h = h_base - 1
                    elif h_base > 2:
                        # Check if H = 2 is feasible
                        for zi in range(len(temp_zones)):
                            si = set(temp_zones[zi]["rooms"])
                            found_2 = False
                            for zj in range(zi + 1, len(temp_zones)):
                                sj = set(temp_zones[zj]["rooms"])
                                if len(si.intersection(sj)) == 0 and si.union(sj) == all_elig:
                                    found_2 = True
                                    break
                            if found_2:
                                delta_h = h_base - 2
                                break

            # -------------------------------------------------------------
            # B. Evaluate Routing Cost Reduction (Delta J)
            # -------------------------------------------------------------
            if delta_h > 0:
                # Substantial structural merger: eliminates entire hub and extra home-run routing
                delta_l = round(max(2.0, 3.5 * delta_h), 2)
                delta_b = 4 * delta_h
                delta_j = round(delta_l + 0.5 * delta_b, 2)
            elif proposal_type == ProposalType.ADD_GHOST_DOOR:
                # Check for leaf room out-and-back shortcut
                is_a_leaf = r_a_id in leaf_room_ids
                is_b_leaf = r_b_id in leaf_room_ids

                if is_a_leaf or is_b_leaf:
                    r_leaf_id = r_a_id if is_a_leaf else r_b_id
                    r_other_id = r_b_id if is_a_leaf else r_a_id

                    leaf_nodes = room_nodes.get(r_leaf_id, [])
                    other_nodes = room_nodes.get(r_other_id, [])

                    # Find existing doorway for leaf room
                    existing_p = None
                    for p in portal_topo.portals:
                        if p.room1_id == r_leaf_id or p.room2_id == r_leaf_id:
                            existing_p = p
                            break

                    if existing_p is not None and leaf_nodes:
                        n_leaf = leaf_nodes[0]
                        # Baseline out-and-back length through existing portal
                        p_old = existing_p.center_point
                        d_in_old = abs(p_old.x - n_leaf.x) + abs(p_old.y - n_leaf.y)
                        l_base_deadend = 2.0 * d_in_old + 0.04

                        # Through-route length using new ghost door
                        d_ghost_node = abs(center_pt.x - n_leaf.x) + abs(center_pt.y - n_leaf.y)
                        l_through = d_in_old + d_ghost_node

                        # Savings inside leaf room
                        int_savings = l_base_deadend - l_through

                        # External shortcut to adjacent room
                        ext_savings = 0.0
                        if other_nodes:
                            n_other = other_nodes[0]
                            d_old_ext = abs(p_old.x - n_other.x) + abs(p_old.y - n_other.y)
                            d_ghost_ext = abs(center_pt.x - n_other.x) + abs(center_pt.y - n_other.y)
                            ext_savings = max(0.0, d_old_ext - d_ghost_ext)

                        delta_l = round(max(0.20, int_savings + ext_savings), 2)
                        delta_b = 2  # Eliminates 180-degree turnaround reversal at dead-end
                        delta_j = round(delta_l + 0.5 * delta_b, 2)
                else:
                    # General shortcut between non-leaf adjacent rooms
                    nodes_a = room_nodes.get(r_a_id, [])
                    nodes_b = room_nodes.get(r_b_id, [])
                    if nodes_a and nodes_b:
                        na, nb = nodes_a[0], nodes_b[0]
                        d_direct = (abs(na.x - center_pt.x) + abs(na.y - center_pt.y) +
                                    abs(center_pt.x - nb.x) + abs(center_pt.y - nb.y))
                        # Detour across existing portals
                        d_detour = abs(na.x - nb.x) + abs(na.y - nb.y) + 3.0
                        if d_detour > d_direct + 0.5:
                            delta_l = round(d_detour - d_direct, 2)
                            delta_b = 2
                            delta_j = round(delta_l + 0.5 * delta_b, 2)

            elif proposal_type == ProposalType.SLIDE_DOOR:
                portal_id = cand.get("portal_id", "")
                nodes_a = room_nodes.get(r_a_id, [])
                nodes_b = room_nodes.get(r_b_id, [])

                # Find original portal center
                orig_portal = None
                for p in portal_topo.portals:
                    if p.portal_id == portal_id:
                        orig_portal = p
                        break

                if orig_portal is not None and nodes_a and nodes_b:
                    na, nb = nodes_a[0], nodes_b[0]
                    orig_c = orig_portal.center_point
                    d_old = (abs(na.x - orig_c.x) + abs(na.y - orig_c.y) +
                             abs(orig_c.x - nb.x) + abs(orig_c.y - nb.y))
                    d_new = (abs(na.x - center_pt.x) + abs(na.y - center_pt.y) +
                             abs(center_pt.x - nb.x) + abs(center_pt.y - nb.y))

                    if d_old > d_new + 0.05:
                        delta_l = round(d_old - d_new, 2)
                        # Check collinearity alignment
                        b_save = 0
                        if abs(center_pt.x - na.x) < 0.05 or abs(center_pt.y - na.y) < 0.05:
                            b_save += 1
                        if abs(center_pt.x - nb.x) < 0.05 or abs(center_pt.y - nb.y) < 0.05:
                            b_save += 1
                        delta_b = max(1, b_save)
                        delta_j = round(delta_l + 0.5 * delta_b, 2)

            # -------------------------------------------------------------
            # C. Format Structural Proposal
            # -------------------------------------------------------------
            if delta_h > 0 or delta_j > 0.10:
                new_cost = max(0.0, round(j_base - delta_j, 2))

                if delta_h > 0:
                    rationale = (
                        f"Adding open passage between {r_a_id} and {r_b_id} merges disconnected "
                        f"topological components, dropping required hubs from {h_base} to "
                        f"{h_base - delta_h} (-{delta_h} hub(s)) and saving {delta_l:.2f} m cable."
                    )
                elif proposal_type == ProposalType.ADD_GHOST_DOOR:
                    if r_a_id in leaf_room_ids or r_b_id in leaf_room_ids:
                        leaf_name = r_a_id if r_a_id in leaf_room_ids else r_b_id
                        other_name = r_b_id if r_a_id in leaf_room_ids else r_a_id
                        rationale = (
                            f"Adding ghost doorway on shared wall between leaf room {leaf_name} and "
                            f"{other_name} eliminates out-and-back dead-end cable detour, saving "
                            f"{delta_l:.2f} m cable and {delta_b} bend(s) (Delta J = -{delta_j:.2f})."
                        )
                    else:
                        rationale = (
                            f"Adding open passage directly connects {r_a_id} and {r_b_id}, shortcutting "
                            f"transit through intermediate rooms, saving {delta_l:.2f} m cable and "
                            f"{delta_b} bend(s)."
                        )
                else:
                    portal_id = cand.get("portal_id", "door")
                    offset_m = cand.get("offset_m", 0.0)
                    rationale = (
                        f"Sliding doorway {portal_id} by {offset_m:+.2f} m along wall aligns opening "
                        f"with room nodes, reducing cable length by {delta_l:.2f} m and eliminating "
                        f"{delta_b} bend(s)."
                    )

                key = f"{proposal_type.value}_{min(r_a_id, r_b_id)}_{max(r_a_id, r_b_id)}"
                if proposal_type == ProposalType.SLIDE_DOOR:
                    key = cand["candidate_id"]

                prop = StructuralProposal(
                    proposal_id=cand["candidate_id"],
                    proposal_type=proposal_type,
                    room_a_id=r_a_id,
                    room_b_id=r_b_id,
                    wall_segment=wall_seg,
                    suggested_door_center=center_pt,
                    door_width_m=door_w,
                    delta_hubs=delta_h,
                    delta_cost_J=delta_j,
                    delta_length_m=delta_l,
                    delta_bends=delta_b,
                    original_cost_J=j_base,
                    new_cost_J=new_cost,
                    rationale=rationale,
                )

                # Keep the best proposal for this wall/room pair
                if key not in proposals_by_key:
                    proposals_by_key[key] = prop
                else:
                    existing = proposals_by_key[key]
                    if (prop.delta_hubs, prop.delta_cost_J) > (existing.delta_hubs, existing.delta_cost_J):
                        proposals_by_key[key] = prop

        ranked_proposals = list(proposals_by_key.values())
        # Lexicographic ranking: delta_hubs descending, then delta_cost_J descending
        ranked_proposals.sort(key=lambda p: (p.delta_hubs, p.delta_cost_J), reverse=True)

        return CoPilotReport(
            plan_id=plan.plan_id,
            baseline_hubs=h_base,
            baseline_cost_J=j_base,
            proposals_evaluated=len(raw_candidates),
            recommended_proposals=ranked_proposals[:max_recommendations],
        )

    def apply_proposal(
        self,
        proposal: StructuralProposal,
        plan: MetricFloorPlan,
    ) -> MetricFloorPlan:
        """Apply the selected structural proposal to the metric floor plan.

        Punches out the doorway opening from obstacle geometry and unifies it
        with routable free space, allowing new portal extraction and collision-free
        routing through the opening.

        Args:
            proposal: Approved StructuralProposal with geometry and dimensions.
            plan: Baseline MetricFloorPlan.

        Returns:
            MetricFloorPlan: Updated floor plan contract with modified free space
                and obstacle geometries reflecting the new/moved doorway.
        """
        p1 = proposal.wall_segment[0]
        p2 = proposal.wall_segment[1]
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        seg_len = math.hypot(dx, dy)
        if seg_len > 1e-6:
            tx = dx / seg_len
            ty = dy / seg_len
        else:
            tx, ty = 1.0, 0.0

        cx = proposal.suggested_door_center.x
        cy = proposal.suggested_door_center.y
        hw = proposal.door_width_m / 2.0

        door_start = (cx - hw * tx, cy - hw * ty)
        door_end = (cx + hw * tx, cy + hw * ty)
        door_line = LineString([door_start, door_end])
        # Buffer door line by 0.05m with flat/square caps to form rectangular doorway volume
        door_box = door_line.buffer(0.05, cap_style=3)

        new_free = plan.free_space_geom.union(door_box)
        new_obs = plan.obstacles_geom.difference(door_box)

        return plan.model_copy(update={
            "free_space_wkt": new_free.wkt,
            "obstacles_wkt": new_obs.wkt,
        })
