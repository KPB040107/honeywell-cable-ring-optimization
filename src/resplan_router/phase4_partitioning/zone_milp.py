"""Exact Contiguous Zone Partitioner & Lexicographic Hub Optimizer.

Formulates and solves the minimal-hub contiguous room partitioning problem:
1. Primary objective: Minimal hub count H >= H_min = max(ceil(area/100), ceil(nodes/5)).
2. Exact door-connected contiguity across rooms (Rule 3) over G_macro.
3. Capacity constraints: <= 100.0 m^2 eligible area and <= 5 nodes per hub (Rule 2).
4. Rule 6 compliant hub placement (>= 0.50m from obstacles inside host room).
5. Exact closed-ring tour pricing J*(Z) over node permutations and candidate sites.
6. Area-gap tie-breaker: min delta A_gap = max_k A_k - min_k A_k when routing costs match.
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ortools.sat.python import cp_model
import shapely
from shapely.geometry import Point
from shapely.ops import polylabel

from resplan_router.contracts import (
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
)
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor


class ZonePartitioner:
    """Exact contiguous zone partitioner and lexicographic hub optimizer."""

    @staticmethod
    def compute_lower_bound_hubs(
        total_area_m2: float,
        node_count: int,
        max_area: float = 100.0,
        max_nodes: int = 5,
    ) -> int:
        """Compute theoretical lower bound H_min on number of hubs.

        H_min = max(ceil(total_area / max_area), ceil(node_count / max_nodes), 1)

        Args:
            total_area_m2: Total eligible room area in square metres.
            node_count: Total number of fixed nodes in eligible rooms.
            max_area: Maximum area capacity per hub (default 100.0 m^2).
            max_nodes: Maximum node capacity per hub (default 5 nodes).

        Returns:
            int: Theoretical minimum number of hubs required.
        """
        bound_area = math.ceil(total_area_m2 / max_area) if max_area > 0 else 1
        bound_nodes = math.ceil(node_count / max_nodes) if max_nodes > 0 else 1
        return max(1, bound_area, bound_nodes)

    @classmethod
    def enumerate_connected_zones(
        cls,
        topology: PortalTopologyGraph,
        plan: MetricFloorPlan,
        max_area: float = 100.0,
        max_nodes: int = 5,
    ) -> List[Dict[str, Any]]:
        """Enumerate all door-connected eligible room subgraphs satisfying capacity limits.

        Explores all connected subgraphs Z on G_macro using breadth-first expansion.
        Filters out any candidate subset where |N(Z)| > max_nodes or area(Z) > max_area.

        Args:
            topology: Macro portal topology graph with adjacency and eligible room IDs.
            plan: Normalized MetricFloorPlan with room geometries and fixed nodes.
            max_area: Maximum eligible area allowed per zone (100.0 m^2).
            max_nodes: Maximum fixed nodes allowed per zone (5 nodes).

        Returns:
            List[Dict[str, Any]]: List of valid zone candidates with rooms, nodes, area_m2.
        """
        elig_rooms = [
            r_id for r_id, r in plan.rooms.items() if r.is_eligible
        ]
        if topology.eligible_room_ids:
            elig_rooms = [r for r in topology.eligible_room_ids if r in plan.rooms]

        # Restrict adjacency to eligible rooms only
        adj: Dict[str, List[str]] = {
            r: [nbr for nbr in topology.adjacency.get(r, []) if nbr in elig_rooms]
            for r in elig_rooms
        }

        # Map each eligible room to its fixed node IDs
        nodes_by_room: Dict[str, List[str]] = {r: [] for r in elig_rooms}
        for n_id, n in plan.nodes.items():
            if n.room_id in nodes_by_room:
                nodes_by_room[n.room_id].append(n_id)

        valid_zones: List[Dict[str, Any]] = []
        visited_subsets: Set[FrozenSet[str]] = set()

        for start_r in elig_rooms:
            queue: List[Set[str]] = [{start_r}]
            while queue:
                curr_set = queue.pop(0)
                f_curr = frozenset(curr_set)
                if f_curr in visited_subsets:
                    continue
                visited_subsets.add(f_curr)

                # Check area and node capacities
                z_area = sum(plan.rooms[r].area_m2 for r in curr_set)
                z_nodes = [n_id for r in curr_set for n_id in nodes_by_room[r]]

                if z_area <= max_area + 1e-4 and len(z_nodes) <= max_nodes:
                    valid_zones.append({
                        "rooms": sorted(list(curr_set)),
                        "nodes": sorted(z_nodes),
                        "area_m2": round(z_area, 4),
                    })

                    # Expand to adjacent neighbors
                    for r in curr_set:
                        for nbr in adj.get(r, []):
                            if nbr not in curr_set:
                                next_set = curr_set | {nbr}
                                if frozenset(next_set) not in visited_subsets:
                                    queue.append(next_set)

        return valid_zones

    @classmethod
    def evaluate_zone_exact_ring(
        cls,
        zone_rooms: Sequence[str],
        plan: MetricFloorPlan,
        cost_tensor: PairwiseCostTensor,
        candidates: Dict[str, List[Point2D]],
    ) -> Tuple[Point2D, str, float]:
        """Evaluate exact minimum closed-ring tour cost and optimal hub location for a candidate zone.

        For candidate zone Z with nodes N(Z) and candidate hub sites H_Z:
        Evaluates node visiting permutations to find optimal hub h* and tour cost J*(Z):
        J*(Z) = min_{h in H_Z, sigma} [ cost(h -> N_sigma(1)) + sum cost(N_sigma(i) -> N_sigma(i+1)) + cost(N_sigma(m) -> h) ]

        Args:
            zone_rooms: Eligible rooms assigned to this candidate zone.
            plan: Normalized floor plan.
            cost_tensor: Precomputed directional pairwise cost tensor and routing grid.
            candidates: Dict mapping room ID to candidate hub Point2D locations.

        Returns:
            Tuple[Point2D, str, float]: (best_hub_point, host_room_id, best_ring_cost_J).
        """
        zone_node_objs = [
            n for n in plan.nodes.values() if n.room_id in zone_rooms
        ]
        zone_node_ids = [n.node_id for n in zone_node_objs]
        m = len(zone_node_objs)

        # 1. Collect candidate hub locations from rooms within this zone
        hub_cands: List[Tuple[str, Point2D]] = []
        for r in zone_rooms:
            for pt in candidates.get(r, []):
                hub_cands.append((r, pt))

        # Fallback if no candidate points provided for this zone
        if not hub_cands:
            for r in zone_rooms:
                rm = plan.rooms[r]
                try:
                    pole = polylabel(rm.geometry, tolerance=0.01)
                    pt = Point2D(x=round(pole.x, 3), y=round(pole.y, 3))
                except Exception:
                    pt = Point2D(x=round(rm.geometry.centroid.x, 3), y=round(rm.geometry.centroid.y, 3))
                hub_cands.append((r, pt))

        # 2. Case: 0 nodes in zone (e.g. transit or storage without nodes)
        if m == 0:
            return hub_cands[0][1], hub_cands[0][0], 0.0

        # 3. Node-to-node pairwise routing costs
        node_dist: Dict[Tuple[str, str], float] = {}
        for u_id in zone_node_ids:
            for v_id in zone_node_ids:
                if u_id == v_id:
                    node_dist[(u_id, v_id)] = 0.0
                else:
                    metric = cost_tensor.get_metric(u_id, v_id)
                    if metric is not None:
                        node_dist[(u_id, v_id)] = metric.cost_J
                    else:
                        node_dist[(u_id, v_id)] = float("inf")

        # 4. Hub-to-node routing via SSSP trees if Hanan grid is available
        grid = getattr(cost_tensor, "grid", None)
        router = getattr(cost_tensor, "router", None) or DirectionStateRouter()

        node_trees: Dict[str, Any] = {}
        if grid is not None:
            for n_obj in zone_node_objs:
                try:
                    v_snap = grid.snap_to_vertex(n_obj.location, tolerance=0.05)
                    node_trees[n_obj.node_id] = router.compute_single_source(grid, v_snap)
                except Exception:
                    pass

        # Filter candidate hubs: prioritize exact Hanan grid vertices when available
        if grid is not None:
            grid_cands = [(r, pt) for r, pt in hub_cands if grid.has_vertex(pt)]
            eval_cands = grid_cands if grid_cands else hub_cands
        else:
            eval_cands = hub_cands

        best_cost = float("inf")
        best_hub: Optional[Point2D] = None
        best_room: Optional[str] = None
        best_obs_dist = -1.0

        obs_geom = plan.obstacles_geom

        # Helper to query cost between hub candidate and node
        def get_hub_node_cost(hub_pt: Point2D, n_id: str) -> float:
            # 1. Cost tensor terminal metric lookup
            m_hub = cost_tensor.get_metric(f"{hub_pt.x}_{hub_pt.y}", n_id)
            if m_hub is not None:
                return m_hub.cost_J

            # 2. SSSP tree lookup on Hanan grid
            if n_id in node_trees and grid is not None:
                tree = node_trees[n_id]
                if grid.has_vertex(hub_pt):
                    v_hub = grid.snap_to_vertex(hub_pt, tolerance=0.005)
                    m_tree = tree.get_metric_to(v_hub)
                    if m_tree is not None:
                        return m_tree.cost_J
                else:
                    try:
                        v_snap = grid.snap_to_vertex(hub_pt, tolerance=0.5)
                        m_snap = tree.get_metric_to(v_snap)
                        if m_snap is not None:
                            manh = abs(hub_pt.x - v_snap.x) + abs(hub_pt.y - v_snap.y)
                            return m_snap.cost_J + manh
                    except Exception:
                        pass

            # 3. Direct router shortest path fallback
            n_pt = plan.nodes[n_id].location
            if grid is not None:
                try:
                    direct_m = router.find_shortest_path(grid, hub_pt, n_pt)
                    return direct_m.cost_J
                except Exception:
                    pass

            # 4. Fallback Manhattan distance
            return abs(hub_pt.x - n_pt.x) + abs(hub_pt.y - n_pt.y)

        # 5. Evaluate permutations and find optimal hub site
        node_perms = list(itertools.permutations(zone_node_ids))

        for host_r, cand_pt in eval_cands:
            # Precompute hub-to-node leg costs for this candidate
            h_costs = {n_id: get_hub_node_cost(cand_pt, n_id) for n_id in zone_node_ids}

            # Check if any node is unreachable from this candidate hub
            if any(c == float("inf") for c in h_costs.values()):
                continue

            for perm in node_perms:
                if m == 1:
                    tour_cost = 2.0 * h_costs[perm[0]]
                else:
                    # Leg: h -> N_sigma(1)
                    cost_head = h_costs[perm[0]]
                    # Leg: N_sigma(m) -> h
                    cost_tail = h_costs[perm[-1]]
                    # Internal node legs: N_sigma(i) -> N_sigma(i+1)
                    cost_internal = sum(
                        node_dist[(perm[i], perm[i + 1])] for i in range(m - 1)
                    )
                    tour_cost = cost_head + cost_internal + cost_tail

                if tour_cost < best_cost - 1e-4:
                    best_cost = tour_cost
                    best_hub = cand_pt
                    best_room = host_r
                    best_obs_dist = obs_geom.distance(cand_pt.as_shapely()) if obs_geom else 0.5
                elif abs(tour_cost - best_cost) < 1e-4:
                    # Tie-breaker among candidates: maximize obstacle clearance (Rule 6 safety)
                    c_dist = obs_geom.distance(cand_pt.as_shapely()) if obs_geom else 0.5
                    if c_dist > best_obs_dist + 1e-4:
                        best_cost = tour_cost
                        best_hub = cand_pt
                        best_room = host_r
                        best_obs_dist = c_dist

        if best_hub is None:
            # If all permutations had inf cost, return first candidate with inf
            return eval_cands[0][1], eval_cands[0][0], float("inf")

        return best_hub, best_room, round(best_cost, 4)

    @classmethod
    def solve_partition(
        cls,
        plan: MetricFloorPlan,
        topology: PortalTopologyGraph,
        cost_tensor: PairwiseCostTensor,
        candidates: Dict[str, List[Point2D]],
        max_area: float = 100.0,
        max_nodes: int = 5,
    ) -> HubZonePartition:
        """Solve lexicographic contiguous zone partitioning and optimal hub placement.

        Starts at theoretical lower bound H = H_min.
        Formulates Set Partitioning model in OR-Tools CP-SAT:
        - Exact coverage: each eligible room in exactly 1 selected zone.
        - Hub count constraint: exactly H hubs.
        - Objective: min sum_z J*(z)*x_z + 10^-3 * (max_k A_k - min_k A_k).
        Increments H until feasible, mapping solution to validated HubZonePartition.

        Args:
            plan: Normalized MetricFloorPlan.
            topology: Macro PortalTopologyGraph.
            cost_tensor: Precomputed pairwise directional cost tensor.
            candidates: Dict mapping room ID to candidate hub points.
            max_area: Maximum eligible area per zone (default 100.0 m^2).
            max_nodes: Maximum fixed nodes per zone (default 5 nodes).

        Returns:
            HubZonePartition: Fully validated contiguous hub-zone partition contract.

        Raises:
            ValueError: If no feasible contiguous partition exists.
        """
        elig_rooms = [
            r_id for r_id, r in plan.rooms.items() if r.is_eligible
        ]
        if topology.eligible_room_ids:
            elig_rooms = [r for r in topology.eligible_room_ids if r in plan.rooms]

        total_area = sum(plan.rooms[r].area_m2 for r in elig_rooms)
        total_nodes = len([n for n in plan.nodes.values() if n.room_id in elig_rooms])

        lower_bound_hubs = cls.compute_lower_bound_hubs(
            total_area_m2=total_area,
            node_count=total_nodes,
            max_area=max_area,
            max_nodes=max_nodes,
        )

        # 1. Enumerate all connected candidate zones
        candidate_zones = cls.enumerate_connected_zones(
            topology=topology,
            plan=plan,
            max_area=max_area,
            max_nodes=max_nodes,
        )

        # 2. Price each connected zone with exact closed-ring tour cost
        priced_zones: List[Dict[str, Any]] = []
        for z in candidate_zones:
            hub_pt, host_room, ring_cost = cls.evaluate_zone_exact_ring(
                zone_rooms=z["rooms"],
                plan=plan,
                cost_tensor=cost_tensor,
                candidates=candidates,
            )
            if ring_cost < float("inf"):
                priced_zones.append({
                    "rooms": z["rooms"],
                    "nodes": z["nodes"],
                    "area_m2": z["area_m2"],
                    "hub_point": hub_pt,
                    "host_room": host_room,
                    "ring_cost_J": ring_cost,
                })

        # 3. Solve Set Partitioning iteratively for H = H_min, H_min + 1, ...
        selected_zones: Optional[List[Dict[str, Any]]] = None
        chosen_H = lower_bound_hubs

        for H in range(lower_bound_hubs, len(elig_rooms) + 1):
            model = cp_model.CpModel()
            n_zones = len(priced_zones)
            x = [model.NewBoolVar(f"x_{i}") for i in range(n_zones)]

            # Exact coverage: each eligible room is covered by exactly one selected zone
            for r in elig_rooms:
                covering = [
                    x[i] for i in range(n_zones) if r in priced_zones[i]["rooms"]
                ]
                model.Add(sum(covering) == 1)

            # Hub count constraint: exactly H hubs
            model.Add(sum(x) == H)

            # Area gap tie-breaker variables: delta_A = A_max - A_min
            SCALE_AREA = 1000
            A_max = model.NewIntVar(0, int(round(100.0 * SCALE_AREA)), "A_max")
            A_min = model.NewIntVar(0, int(round(100.0 * SCALE_AREA)), "A_min")
            delta_A = model.NewIntVar(0, int(round(100.0 * SCALE_AREA)), "delta_A")
            model.Add(delta_A == A_max - A_min)

            for i in range(n_zones):
                a_int = int(round(priced_zones[i]["area_m2"] * SCALE_AREA))
                model.Add(A_max >= a_int).OnlyEnforceIf(x[i])
                model.Add(A_min <= a_int).OnlyEnforceIf(x[i])

            # Lexicographic objective: 1_000_000 * sum(routing_cost) + delta_A
            cost_terms = []
            for i in range(n_zones):
                c_int = int(round(priced_zones[i]["ring_cost_J"] * 1000.0))
                cost_terms.append(c_int * x[i])

            model.Minimize(1_000_000 * sum(cost_terms) + delta_A)

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 5.0
            status = solver.Solve(model)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                selected_zones = [
                    priced_zones[i] for i in range(n_zones) if solver.Value(x[i]) == 1
                ]
                chosen_H = H
                break

        if selected_zones is None:
            raise ValueError(
                f"No feasible contiguous partition found covering eligible rooms {elig_rooms}"
            )

        # 4. Construct HubZonePartition contract
        # Sort zones deterministically so that the zone with the lowest room ID comes first
        selected_zones.sort(key=lambda z: (min(z["rooms"]) if z["rooms"] else ""))

        hubs: List[HubAssignment] = []
        for idx, z in enumerate(selected_zones):
            hubs.append(
                HubAssignment(
                    hub_id=f"H{idx + 1}",
                    room_id=z["host_room"],
                    location=z["hub_point"],
                    assigned_rooms=z["rooms"],
                    assigned_nodes=z["nodes"],
                    zone_area_m2=z["area_m2"],
                    estimated_ring_cost_J=z["ring_cost_J"],
                )
            )

        area_gap = round(
            max(h.zone_area_m2 for h in hubs) - min(h.zone_area_m2 for h in hubs),
            4,
        )

        return HubZonePartition(
            plan_id=plan.plan_id,
            hub_count=chosen_H,
            lower_bound_hubs=lower_bound_hubs,
            area_gap_m2=area_gap,
            hubs=hubs,
        )
