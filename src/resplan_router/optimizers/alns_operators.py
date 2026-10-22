"""Modular ALNS Operator Engine for Honeywell Cable Ring Optimization.

Implements 8 discrete Destroy & Repair operators for PPO/ALNS guidance:
0: BOUNDARY_EXCHANGE      - Boundary room swap between adjacent hubs + repair
1: MERGE_SPLIT           - 2-Zone merge (H-reduction) or DP balanced re-split
2: CROSSING_ELIM         - 2-opt tour inversion on highest-crossing ring
3: OVERLAP_REDUCE        - Collinear overlap reduction via tour reversal/alternates
4: BEND_SMOOTH           - Bend minimization via coordinate-aligned TSP tour
5: WORST_ROUTE_REBUILD   - Re-solve worst cost/node ring with multi-room hub search
6: RANDOM_EXPLORATION    - Perturbative 1-2 room relocation across feasible zones
7: HUB_CENTROID_RELOC    - Relocate hub to alternative room or clearance offset
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import math
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx
import shapely
from shapely.geometry import Point, Polygon
from shapely.ops import polylabel

from resplan_router.contracts import (
    CompletedHubRing,
    FixedNode,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.bitmask_dp import _solve_held_karp_tour
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import HananRoutingGrid, build_hanan_grid
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase11_optimization.objective import compute_J
from resplan_router.phase11_optimization.ring_pool import compute_ring_internal_conflicts


NUM_OPERATORS = 8
OPERATOR_NAMES = [
    "BOUNDARY_EXCHANGE",
    "MERGE_SPLIT",
    "CROSSING_ELIM",
    "OVERLAP_REDUCE",
    "BEND_SMOOTH",
    "WORST_ROUTE_REBUILD",
    "RANDOM_EXPLORATION",
    "HUB_CENTROID_RELOC",
]


@dataclass
class ALNSState:
    """State of a candidate solution in ALNS / RL environment."""

    zones: List[Set[str]]
    bundle: SynthesizedRingsBundle
    partition: HubZonePartition
    cost_J: float
    h_count: int
    area_gap: float
    is_valid: bool
    hub_rooms: Optional[Dict[int, str]] = None  # zone_index -> hub_room_id override
    hub_locations: Optional[Dict[int, Point2D]] = None  # zone_index -> hub_loc override

    def clone(self) -> ALNSState:
        return ALNSState(
            zones=[set(z) for z in self.zones],
            bundle=self.bundle,
            partition=self.partition,
            cost_J=self.cost_J,
            h_count=self.h_count,
            area_gap=self.area_gap,
            is_valid=self.is_valid,
            hub_rooms=dict(self.hub_rooms) if self.hub_rooms else None,
            hub_locations=dict(self.hub_locations) if self.hub_locations else None,
        )


def build_door_connectivity_graph(plan: MetricFloorPlan, topology: Any) -> nx.Graph:
    """Construct the door-adjacency graph for eligible rooms."""
    eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
    door_graph = nx.Graph()
    door_graph.add_nodes_from(eligible_rooms)
    for r_id in eligible_rooms:
        for nbr in topology.adjacency.get(r_id, []):
            if nbr in eligible_rooms:
                door_graph.add_edge(r_id, nbr)
    return door_graph


def find_safe_hub_location(
    plan: MetricFloorPlan,
    room_id: str,
    obs_buf: Optional[Any] = None,
) -> Tuple[Point2D, float]:
    """Find a point inside room_id with maximum clearance from obstacles."""
    if obs_buf is None:
        obs_buf = plan.obstacles_geom.buffer(0.50) if plan.obstacles_geom else None

    r_geom = plan.rooms[room_id].geometry
    safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
    safe_poly = shapely.make_valid(safe_poly)

    if not safe_poly.is_empty and safe_poly.area > 1e-4:
        try:
            target = safe_poly if isinstance(safe_poly, Polygon) else max(safe_poly.geoms, key=lambda g: g.area)
            pole = polylabel(target, tolerance=0.005)
            pt = Point(pole.x, pole.y)
            clr = plan.obstacles_geom.distance(pt) if plan.obstacles_geom else 1.0
            return Point2D(x=round(pole.x, 4), y=round(pole.y, 4)), clr
        except Exception:
            pass

    # Fallback to room centroid
    c = plan.rooms[room_id].geometry.centroid
    clr = plan.obstacles_geom.distance(c) if plan.obstacles_geom else 0.5
    return Point2D(x=round(c.x, 4), y=round(c.y, 4)), clr


def synthesize_rings_for_zones(
    zones: List[Set[str]],
    plan: MetricFloorPlan,
    topology: Any,
    hub_room_overrides: Optional[Dict[int, str]] = None,
    hub_loc_overrides: Optional[Dict[int, Point2D]] = None,
    tour_reversals: Optional[Dict[int, bool]] = None,
    reusable_rings: Optional[Dict[int, CompletedHubRing]] = None,
    reusable_hubs: Optional[Dict[int, HubAssignment]] = None,
) -> Tuple[SynthesizedRingsBundle, HubZonePartition]:
    """Synthesize complete rectilinear rings and hub assignments for given zones with ring reuse acceleration."""
    hubs: List[HubAssignment] = []
    hub_rings: List[CompletedHubRing] = []
    router = DirectionStateRouter()
    obs_buf = plan.obstacles_geom.buffer(0.50) if plan.obstacles_geom else None

    for z_idx, z_rooms in enumerate(zones, start=1):
        idx_0 = z_idx - 1
        # Fast path: check if this zone's hub assignment and ring can be reused directly
        can_reuse = (
            reusable_rings is not None
            and reusable_hubs is not None
            and idx_0 in reusable_rings
            and idx_0 in reusable_hubs
            and (not hub_room_overrides or idx_0 not in hub_room_overrides)
            and (not hub_loc_overrides or idx_0 not in hub_loc_overrides)
            and (not tour_reversals or not tour_reversals.get(idx_0, False))
            and set(reusable_rings[idx_0].assigned_rooms) == z_rooms
        )

        if can_reuse:
            hubs.append(reusable_hubs[idx_0])
            hub_rings.append(reusable_rings[idx_0])
            continue

        hub_id = f"H{z_idx}"
        zone_area = sum(plan.rooms[r].area_m2 for r in z_rooms)
        zone_nodes = [n for n in plan.nodes.values() if n.room_id in z_rooms]

        best_hub_loc: Optional[Point2D] = None
        best_hub_room = sorted(z_rooms)[0]

        # Check explicit overrides
        if hub_loc_overrides and (z_idx - 1) in hub_loc_overrides:
            best_hub_loc = hub_loc_overrides[z_idx - 1]
            if hub_room_overrides and (z_idx - 1) in hub_room_overrides:
                best_hub_room = hub_room_overrides[z_idx - 1]
        elif hub_room_overrides and (z_idx - 1) in hub_room_overrides:
            cand_room = hub_room_overrides[z_idx - 1]
            if cand_room in z_rooms:
                best_hub_room = cand_room
                loc, _ = find_safe_hub_location(plan, best_hub_room, obs_buf)
                best_hub_loc = loc

        if best_hub_loc is None:
            max_clr = -1.0
            for r_id in z_rooms:
                loc, clr = find_safe_hub_location(plan, r_id, obs_buf)
                if clr > max_clr:
                    max_clr = clr
                    best_hub_loc = loc
                    best_hub_room = r_id

        if best_hub_loc is None:
            best_hub_loc, _ = find_safe_hub_location(plan, best_hub_room, obs_buf)

        hub_assign = HubAssignment(
            hub_id=hub_id,
            room_id=best_hub_room,
            location=best_hub_loc,
            assigned_rooms=sorted(z_rooms),
            assigned_nodes=sorted([n.node_id for n in zone_nodes]),
            zone_area_m2=round(zone_area, 4),
        )
        hubs.append(hub_assign)

        terminals: Dict[str, Point2D] = {hub_id: best_hub_loc}
        for n in zone_nodes:
            terminals[n.node_id] = n.location

        seq = _solve_held_karp_tour(terminals, hub_id)
        if tour_reversals and tour_reversals.get(z_idx - 1, False):
            # Reverse internal sequence: [hub, n1, n2, ..., hub] -> [hub, ..., n2, n1, hub]
            if len(seq) > 3:
                seq = [seq[0]] + list(reversed(seq[1:-1])) + [seq[-1]]

        grid = build_hanan_grid(plan=plan, topology=topology, extra_points=list(terminals.values()))

        segments: List[RouteSegment] = []
        total_len = 0.0
        total_bends = 0

        for k in range(len(seq) - 1):
            u_id = seq[k]
            v_id = seq[k + 1]
            u_pt = terminals[u_id]
            v_pt = terminals[v_id]
            try:
                metric = router.find_shortest_path(grid=grid, start=u_pt, end=v_pt, start_id=u_id, end_id=v_id)
                poly = metric.polyline
                l_m = metric.length_m
                b_cnt = metric.bends
            except Exception:
                mid = Point2D(x=v_pt.x, y=u_pt.y)
                poly = compress_polyline([u_pt, mid, v_pt])
                l_m = round(math.hypot(mid.x - u_pt.x, mid.y - u_pt.y) + math.hypot(v_pt.x - mid.x, v_pt.y - mid.y), 4)
                b_cnt = 1

            seg = RouteSegment(from_node=u_id, to_node=v_id, length_m=l_m, polyline=poly)
            segments.append(seg)
            total_len += l_m
            total_bends += b_cnt

        x_cnt, o_len = compute_ring_internal_conflicts(segments)
        j_val = compute_J(total_len, total_bends, x_cnt, o_len)
        completed_ring = CompletedHubRing(
            hub_id=hub_id,
            room_id=best_hub_room,
            location=best_hub_loc,
            assigned_rooms=sorted(z_rooms),
            assigned_nodes=sorted([n.node_id for n in zone_nodes]),
            closed_sequence=seq,
            route_segments=segments,
            ring_length_m=round(total_len, 4),
            ring_bends=total_bends,
            ring_crossings=x_cnt,
            ring_overlap_m=round(o_len, 4),
            cost_J=round(j_val, 4),
        )
        hub_rings.append(completed_ring)

    tot_l = sum(r.ring_length_m for r in hub_rings)
    tot_b = sum(r.ring_bends for r in hub_rings)
    tot_x = sum(r.ring_crossings for r in hub_rings)
    tot_o = sum(r.ring_overlap_m for r in hub_rings)
    tot_j = compute_J(tot_l, tot_b, tot_x, tot_o)

    bundle = SynthesizedRingsBundle(
        plan_id=plan.plan_id,
        hub_rings=hub_rings,
        total_length_m=round(tot_l, 4),
        total_bends=tot_b,
        total_crossings=tot_x,
        total_overlap_m=round(tot_o, 4),
        total_cost_J=round(tot_j, 4),
    )

    areas = [h.zone_area_m2 for h in hubs]
    area_gap = max(areas) - min(areas) if len(areas) > 1 else 0.0

    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=len(hubs),
        lower_bound_hubs=len(hubs),
        area_gap_m2=round(area_gap, 4),
        hubs=hubs,
    )

    return bundle, partition


def state_from_zones(
    zones: List[Set[str]],
    plan: MetricFloorPlan,
    topology: Any,
    hub_room_overrides: Optional[Dict[int, str]] = None,
    hub_loc_overrides: Optional[Dict[int, Point2D]] = None,
    tour_reversals: Optional[Dict[int, bool]] = None,
    parent_state: Optional[ALNSState] = None,
) -> Optional[ALNSState]:
    """Build and evaluate a full ALNSState from zone specification, accelerating via ring reuse."""
    try:
        reusable_rings = None
        reusable_hubs = None
        if parent_state is not None and parent_state.bundle and parent_state.partition:
            reusable_rings = {i: ring for i, ring in enumerate(parent_state.bundle.hub_rings)}
            reusable_hubs = {i: hub for i, hub in enumerate(parent_state.partition.hubs)}

        bundle, partition = synthesize_rings_for_zones(
            zones=zones,
            plan=plan,
            topology=topology,
            hub_room_overrides=hub_room_overrides,
            hub_loc_overrides=hub_loc_overrides,
            tour_reversals=tour_reversals,
            reusable_rings=reusable_rings,
            reusable_hubs=reusable_hubs,
        )
        report = FloorPlanChecker.evaluate(plan, bundle)
        return ALNSState(
            zones=[set(z) for z in zones],
            bundle=bundle,
            partition=partition,
            cost_J=report.total_cost_J,
            h_count=report.hub_count,
            area_gap=report.area_gap_m2,
            is_valid=report.is_valid,
            hub_rooms=dict(hub_room_overrides) if hub_room_overrides else None,
            hub_locations=dict(hub_loc_overrides) if hub_loc_overrides else None,
        )
    except Exception:
        return None


# =========================================================================
# OPERATOR 0: BOUNDARY_EXCHANGE
# =========================================================================
def operator_boundary_exchange(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Move an adjacent boundary room from zone A to zone B."""
    if len(state.zones) < 2:
        return None

    cand_zones = [set(z) for z in state.zones]
    indices = list(range(len(cand_zones)))
    rng.shuffle(indices)

    for z1_idx in indices:
        z1 = cand_zones[z1_idx]
        adj_zones = [
            z2_idx
            for z2_idx in indices
            if z1_idx != z2_idx and any(door_graph.has_edge(r1, r2) for r1 in z1 for r2 in cand_zones[z2_idx])
        ]
        if not adj_zones:
            continue

        rng.shuffle(adj_zones)
        for z2_idx in adj_zones:
            z2 = cand_zones[z2_idx]
            movable_rooms = []
            for r in sorted(z1)[:2]:
                sub_z1 = z1 - {r}
                if not sub_z1 or nx.is_connected(door_graph.subgraph(sub_z1)):
                    new_z2 = z2 | {r}
                    new_area2 = sum(plan.rooms[rm].area_m2 for rm in new_z2)
                    if len(new_z2) <= 5 and new_area2 <= 100.0 and nx.is_connected(door_graph.subgraph(new_z2)):
                        movable_rooms.append(r)

            if movable_rooms:
                r_move = rng.choice(movable_rooms)
                cand_zones[z1_idx] = z1 - {r_move}
                cand_zones[z2_idx] = z2 | {r_move}
                cand_zones = [z for z in cand_zones if z]
                new_state = state_from_zones(cand_zones, plan, topology, parent_state=state)
                if new_state is not None and new_state.is_valid:
                    return new_state

    return None


# =========================================================================
# OPERATOR 1: MERGE_SPLIT
# =========================================================================
def operator_merge_split(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Attempt to merge two connected zones into one, or rebalance two zones."""
    if len(state.zones) < 2:
        return None

    cand_zones = [set(z) for z in state.zones]
    pair_candidates: List[Tuple[int, int]] = []
    for i in range(len(cand_zones)):
        for j in range(i + 1, len(cand_zones)):
            if any(door_graph.has_edge(r1, r2) for r1 in cand_zones[i] for r2 in cand_zones[j]):
                pair_candidates.append((i, j))

    if not pair_candidates:
        return None

    rng.shuffle(pair_candidates)
    for z1_idx, z2_idx in pair_candidates:
        z1 = cand_zones[z1_idx]
        z2 = cand_zones[z2_idx]
        merged = z1 | z2
        merged_area = sum(plan.rooms[r].area_m2 for r in merged)

        # 1. Direct merge (Hub count reduction!)
        if len(merged) <= 5 and merged_area <= 100.0 and nx.is_connected(door_graph.subgraph(merged)):
            new_zones = [z for idx, z in enumerate(cand_zones) if idx not in (z1_idx, z2_idx)] + [merged]
            new_state = state_from_zones(new_zones, plan, topology, parent_state=state)
            if new_state is not None and new_state.is_valid:
                return new_state

        # 2. Re-split: Find an alternative connected 2-partition of merged
        subg = door_graph.subgraph(merged)
        merged_list = sorted(merged)
        rng.shuffle(merged_list)
        split_attempts = 0
        for cut_size in range(1, len(merged_list)):
            for seed_comb in itertools.combinations(merged_list, cut_size):
                sub_a = set(seed_comb)
                sub_b = merged - sub_a
                if sub_a == z1 or sub_a == z2:
                    continue  # Same partition
                if (
                    len(sub_a) <= 5
                    and len(sub_b) <= 5
                    and sum(plan.rooms[r].area_m2 for r in sub_a) <= 100.0
                    and sum(plan.rooms[r].area_m2 for r in sub_b) <= 100.0
                    and nx.is_connected(door_graph.subgraph(sub_a))
                    and nx.is_connected(door_graph.subgraph(sub_b))
                ):
                    split_attempts += 1
                    new_zones = [z for idx, z in enumerate(cand_zones) if idx not in (z1_idx, z2_idx)] + [sub_a, sub_b]
                    new_state = state_from_zones(new_zones, plan, topology, parent_state=state)
                    if new_state is not None and new_state.is_valid:
                        return new_state
                    if split_attempts >= 2:
                        break
            if split_attempts >= 2:
                break

    return None


# =========================================================================
# OPERATOR 2: CROSSING_ELIM
# =========================================================================
def operator_crossing_elim(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Target the ring with crossings or most nodes and reverse tour direction."""
    # Find ring with crossings or largest ring
    reversals = {}
    rings = state.bundle.hub_rings
    if not rings:
        return None

    # Check which rings have crossings
    crossing_rings = [i for i, r in enumerate(rings) if r.ring_crossings > 0]
    target_idx = rng.choice(crossing_rings) if crossing_rings else max(range(len(rings)), key=lambda i: len(rings[i].assigned_nodes))

    reversals[target_idx] = True
    new_state = state_from_zones(state.zones, plan, topology, tour_reversals=reversals, parent_state=state)
    if new_state is not None and new_state.is_valid:
        return new_state
    return None


# =========================================================================
# OPERATOR 3: OVERLAP_REDUCE
# =========================================================================
def operator_overlap_reduce(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Target rings with overlap and attempt alternate route synthesis."""
    rings = state.bundle.hub_rings
    if not rings:
        return None

    overlap_rings = [i for i, r in enumerate(rings) if r.ring_overlap_m > 1e-4]
    target_idx = rng.choice(overlap_rings) if overlap_rings else rng.randrange(len(rings))

    # Reverse direction for that ring to change entry/exit approach angles
    reversals = {target_idx: True}
    new_state = state_from_zones(state.zones, plan, topology, tour_reversals=reversals, parent_state=state)
    if new_state is not None and new_state.is_valid:
        return new_state
    return None


# =========================================================================
# OPERATOR 4: BEND_SMOOTH
# =========================================================================
def operator_bend_smooth(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Target highest-bend ring and attempt hub location recentering to reduce turns."""
    rings = state.bundle.hub_rings
    if not rings:
        return None

    worst_bend_idx = max(range(len(rings)), key=lambda i: rings[i].ring_bends)
    target_zone = state.zones[worst_bend_idx]
    if len(target_zone) <= 1:
        return None

    # Try shifting hub to another room in the same zone
    alt_rooms = [r for r in target_zone if r != state.partition.hubs[worst_bend_idx].room_id]
    if alt_rooms:
        rng.shuffle(alt_rooms)
        for cand_room in alt_rooms:
            hub_rooms = {worst_bend_idx: cand_room}
            new_state = state_from_zones(state.zones, plan, topology, hub_room_overrides=hub_rooms, parent_state=state)
            if new_state is not None and new_state.is_valid:
                return new_state

    return None


# =========================================================================
# OPERATOR 5: WORST_ROUTE_REBUILD
# =========================================================================
def operator_worst_route_rebuild(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Find zone with worst cost per node and re-evaluate all eligible hub locations."""
    rings = state.bundle.hub_rings
    if not rings:
        return None

    worst_idx = max(
        range(len(rings)),
        key=lambda i: rings[i].cost_J / max(1, len(rings[i].assigned_nodes)),
    )
    current_hub_room = rings[worst_idx].room_id
    # Exclude current hub room so we test genuine relocation alternatives
    target_zone = [r for r in state.zones[worst_idx] if r != current_hub_room]
    if not target_zone:
        return None
    rng.shuffle(target_zone)

    best_cand_state: Optional[ALNSState] = None
    for cand_room in target_zone[:2]:
        hub_rooms = {worst_idx: cand_room}
        cand_state = state_from_zones(state.zones, plan, topology, hub_room_overrides=hub_rooms, parent_state=state)
        if cand_state is not None and cand_state.is_valid:
            if best_cand_state is None or cand_state.cost_J < best_cand_state.cost_J:
                best_cand_state = cand_state

    return best_cand_state


# =========================================================================
# OPERATOR 6: RANDOM_EXPLORATION
# =========================================================================
def operator_random_exploration(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Select a random room and relocate it to any feasible adjacent zone."""
    if len(state.zones) < 2:
        return None

    cand_zones = [set(z) for z in state.zones]
    all_rooms = [r for z in cand_zones for r in z]
    rng.shuffle(all_rooms)

    for r in all_rooms[:2]:
        # Find which zone currently contains r
        source_idx = next(i for i, z in enumerate(cand_zones) if r in z)
        source_zone = cand_zones[source_idx]

        rem_source = source_zone - {r}
        if not rem_source or not nx.is_connected(door_graph.subgraph(rem_source)):
            continue

        # Find target zones adjacent to r
        adj_target_indices = [
            i
            for i, z in enumerate(cand_zones)
            if i != source_idx and any(door_graph.has_edge(r, target_r) for target_r in z)
        ]
        if not adj_target_indices:
            continue

        rng.shuffle(adj_target_indices)
        for target_idx in adj_target_indices:
            target_zone = cand_zones[target_idx]
            cand_target = target_zone | {r}
            if (
                len(cand_target) <= 5
                and sum(plan.rooms[x].area_m2 for x in cand_target) <= 100.0
                and nx.is_connected(door_graph.subgraph(cand_target))
            ):
                new_zones = [set(z) for z in cand_zones]
                new_zones[source_idx] = rem_source
                new_zones[target_idx] = cand_target
                new_zones = [z for z in new_zones if z]
                new_state = state_from_zones(new_zones, plan, topology, parent_state=state)
                if new_state is not None and new_state.is_valid:
                    return new_state

    return None


# =========================================================================
# OPERATOR 7: HUB_CENTROID_RELOC
# =========================================================================
def operator_hub_centroid_reloc(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Optional[ALNSState]:
    """Perturb the hub coordinates within safe clearance bounds."""
    rings = state.bundle.hub_rings
    if not rings:
        return None

    target_idx = rng.randrange(len(rings))
    curr_hub = state.partition.hubs[target_idx]
    loc = curr_hub.location
    room_id = curr_hub.room_id

    # Test small orthogonal displacements (e.g. +/- 0.3m, +/- 0.5m)
    deltas = [(0.3, 0.0), (-0.3, 0.0), (0.0, 0.3), (0.0, -0.3), (0.5, 0.0), (-0.5, 0.0), (0.0, 0.5), (0.0, -0.5)]
    rng.shuffle(deltas)

    for dx, dy in deltas[:2]:
        cand_pt = Point(loc.x + dx, loc.y + dy)
        # Check inside room and clearance >= 0.50m
        if plan.rooms[room_id].geometry.contains(cand_pt):
            clr = plan.obstacles_geom.distance(cand_pt) if plan.obstacles_geom else 1.0
            if clr >= 0.50:
                hub_locs = {target_idx: Point2D(x=round(cand_pt.x, 4), y=round(cand_pt.y, 4))}
                new_state = state_from_zones(state.zones, plan, topology, hub_loc_overrides=hub_locs, parent_state=state)
                if new_state is not None and new_state.is_valid:
                    return new_state

    return None


# =========================================================================
# MASTER OPERATOR DISPATCHER
# =========================================================================
OPERATOR_FUNCS = [
    operator_boundary_exchange,
    operator_merge_split,
    operator_crossing_elim,
    operator_overlap_reduce,
    operator_bend_smooth,
    operator_worst_route_rebuild,
    operator_random_exploration,
    operator_hub_centroid_reloc,
]


def apply_alns_operator(
    operator_idx: int,
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Tuple[ALNSState, bool, str]:
    """Execute a discrete ALNS operator on the state.

    Args:
        operator_idx: Action index in [0, 7].
        state: Current solution state.
        plan: Metric floor plan.
        topology: Precomputed macro topology.
        door_graph: Door connectivity graph between rooms.
        rng: Random number generator for reproducibility.

    Returns:
        (result_state, was_applied, operator_name)
    """
    if not (0 <= operator_idx < NUM_OPERATORS):
        raise ValueError(f"Operator index {operator_idx} out of range [0, {NUM_OPERATORS - 1}]")

    op_name = OPERATOR_NAMES[operator_idx]
    op_func = OPERATOR_FUNCS[operator_idx]

    cand_state = op_func(state, plan, topology, door_graph, rng)
    if cand_state is not None and cand_state.is_valid:
        # Detect genuine no-op move (identical cost, identical hub count, and identical zones/hubs)
        if (
            abs(cand_state.cost_J - state.cost_J) < 1e-6
            and cand_state.h_count == state.h_count
            and cand_state.zones == state.zones
            and [r.room_id for r in cand_state.bundle.hub_rings] == [r.room_id for r in state.bundle.hub_rings]
        ):
            return state, False, op_name
        return cand_state, True, op_name

    # Inapplicable or no valid improvement found: return unchanged state
    return state, False, op_name
