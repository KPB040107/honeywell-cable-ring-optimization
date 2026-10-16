"""ALNS Destroy and Repair Neighborhood Operators for Phase 11.

Destroy Operators:
1. remove_highest_cost_ring: Unassigns all rooms in the worst-scoring ring.
2. remove_boundary_room: Identifies boundary rooms adjacent to another zone and unassigns them.
3. target_crossing_rings: Selects rings that participate in cable crossings.
4. target_overlap_rings: Selects rings that participate in shared cable overlap.
5. relocate_hub: Clears the hub position of a ring with high routing length.
6. relocate_device: Shifts a device location within its room to a different candidate.
7. replace_high_cost_leg: Targets individual high-cost cable segments for re-routing.

Repair Operators:
1. reassign_boundary_rooms: Reassigns unassigned rooms to adjacent connected zones respecting limits.
2. rerun_held_karp: Recomputes optimal visiting order on modified zones using DirectionalRingSolver.
3. alternative_paths: Substitutes Yen alternative paths for legs involved in crossings or overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import networkx as nx

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
from resplan_router.phase1_ingestion.device_candidates import generate_room_device_candidates
from resplan_router.phase11_optimization.ring_pool import (
    CompleteRingCandidate,
    ZoneRingGenerator,
    compute_ring_internal_conflicts,
)


@dataclass
class ALNSSolutionState:
    """Current mutable solution state inside ALNS."""

    rings: List[CompleteRingCandidate]
    unassigned_rooms: Set[str]


def destroy_highest_cost_ring(
    state: ALNSSolutionState,
) -> Tuple[ALNSSolutionState, str]:
    """Destroy operator 1: Remove the single highest-cost ring from the solution."""
    if not state.rings:
        return state, "highest_cost_ring"

    # Find highest J ring
    worst_idx = max(range(len(state.rings)), key=lambda i: state.rings[i].total_cost_J)
    removed = state.rings[worst_idx]

    new_rings = [r for i, r in enumerate(state.rings) if i != worst_idx]
    new_unassigned = set(state.unassigned_rooms).union(removed.room_ids)

    return ALNSSolutionState(rings=new_rings, unassigned_rooms=new_unassigned), "highest_cost_ring"


def destroy_boundary_room(
    state: ALNSSolutionState,
    plan: MetricFloorPlan,
    topology: Any,
    rng: random.Random,
) -> Tuple[ALNSSolutionState, str]:
    """Destroy operator 2: Unassign boundary rooms that border other zones."""
    if len(state.rings) < 2:
        return destroy_highest_cost_ring(state)

    # Find rooms adjacent to a room in a different ring
    boundary_candidates: List[Tuple[int, str]] = []  # (ring_idx, room_id)
    room_to_ring = {}
    for r_idx, ring in enumerate(state.rings):
        for r_id in ring.room_ids:
            room_to_ring[r_id] = r_idx

    for r_idx, ring in enumerate(state.rings):
        if len(ring.room_ids) <= 1:
            continue  # Don't destroy a 1-room ring completely with this operator
        for r_id in ring.room_ids:
            for nbr in topology.adjacency.get(r_id, []):
                nbr_ring = room_to_ring.get(nbr)
                if nbr_ring is not None and nbr_ring != r_idx:
                    boundary_candidates.append((r_idx, r_id))
                    break

    if not boundary_candidates:
        return destroy_highest_cost_ring(state)

    # Pick 1 or 2 boundary rooms to release
    chosen_ring_idx, chosen_room = rng.choice(boundary_candidates)
    target_ring = state.rings[chosen_ring_idx]

    remaining_rooms = frozenset(r for r in target_ring.room_ids if r != chosen_room)
    # Check if remaining rooms stay connected
    sub_g = nx.Graph()
    sub_g.add_nodes_from(remaining_rooms)
    for r in remaining_rooms:
        for nbr in topology.adjacency.get(r, []):
            if nbr in remaining_rooms:
                sub_g.add_edge(r, nbr)

    if nx.is_connected(sub_g):
        # We can safely shrink this ring
        new_rings = [r for i, r in enumerate(state.rings) if i != chosen_ring_idx]
        new_unassigned = set(state.unassigned_rooms)
        new_unassigned.add(chosen_room)
        # Add remaining part of target ring to unassigned or regenerate later
        new_unassigned.update(remaining_rooms)
        return ALNSSolutionState(rings=new_rings, unassigned_rooms=new_unassigned), "boundary_room"

    return destroy_highest_cost_ring(state)


def destroy_hub_relocation(
    state: ALNSSolutionState,
    rng: random.Random,
) -> Tuple[ALNSSolutionState, str]:
    """Destroy operator 3: Remove a ring with high bend/length count to regenerate with a different hub."""
    if not state.rings:
        return state, "hub_relocation"

    # Pick a ring with > 1 room to relocate hub
    multi_room = [i for i, r in enumerate(state.rings) if len(r.room_ids) > 1]
    target_idx = rng.choice(multi_room) if multi_room else rng.choice(range(len(state.rings)))

    removed = state.rings[target_idx]
    new_rings = [r for i, r in enumerate(state.rings) if i != target_idx]
    new_unassigned = set(state.unassigned_rooms).union(removed.room_ids)

    return ALNSSolutionState(rings=new_rings, unassigned_rooms=new_unassigned), "hub_relocation"


def repair_reassign_and_solve(
    state: ALNSSolutionState,
    plan: MetricFloorPlan,
    topology: Any,
    rng: random.Random,
) -> ALNSSolutionState:
    """Repair operator: Reassemble unassigned rooms into valid door-connected zones and solve rings."""
    if not state.unassigned_rooms:
        return state

    generator = ZoneRingGenerator(plan, topology, beam_width=10, candidates_per_zone=4)
    new_rings = list(state.rings)
    unassigned = set(state.unassigned_rooms)

    # 1. Try to merge unassigned rooms into existing rings if capacity allows
    for ring_idx in range(len(new_rings)):
        ring = new_rings[ring_idx]
        if not unassigned:
            break
        # Find adjacent unassigned rooms
        adj_unassigned = []
        for ur in list(unassigned):
            for rr in ring.room_ids:
                if rr in topology.adjacency.get(ur, []):
                    adj_unassigned.append(ur)
                    break

        for cand_room in adj_unassigned:
            cand_area = plan.rooms[cand_room].area_m2
            cand_devs = sum(1 for n in plan.nodes.values() if n.room_id == cand_room)
            cur_area = sum(plan.rooms[r].area_m2 for r in ring.room_ids)
            cur_devs = len(ring.device_nodes)

            if cur_area + cand_area <= 100.0 and cur_devs + cand_devs <= 5:
                # Merge into this zone
                merged_rooms = frozenset(set(ring.room_ids).union({cand_room}))
                unassigned.remove(cand_room)
                cands = generator.generate_zone_candidates(merged_rooms, zone_idx=ring_idx + 1)
                if cands:
                    new_rings[ring_idx] = cands[0]
                    ring = cands[0]

    # 2. For any remaining unassigned rooms, form new connected zones
    while unassigned:
        # Form a connected component
        seed = max(unassigned, key=lambda r: plan.rooms[r].area_m2)
        zone_set = {seed}
        unassigned.remove(seed)
        tot_area = plan.rooms[seed].area_m2
        tot_devs = sum(1 for n in plan.nodes.values() if n.room_id == seed)

        for other in list(unassigned):
            if any(other in topology.adjacency.get(z, []) for z in zone_set):
                oa = plan.rooms[other].area_m2
                od = sum(1 for n in plan.nodes.values() if n.room_id == other)
                if tot_area + oa <= 100.0 and tot_devs + od <= 5:
                    zone_set.add(other)
                    unassigned.remove(other)
                    tot_area += oa
                    tot_devs += od

        cands = generator.generate_zone_candidates(frozenset(zone_set), zone_idx=len(new_rings) + 1)
        if cands:
            new_rings.append(cands[0])

    return ALNSSolutionState(rings=new_rings, unassigned_rooms=set())
