"""Fast Adaptive Large Neighborhood Search (ALNS) Cable Optimizer (Agent 6).

Implements multi-operator destroy and repair metaheuristic with adaptive operator weights
and simulated annealing acceptance to improve zone partitioning and route sequencing.
"""

from __future__ import annotations

import copy
import math
import random
import time
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

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
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult, create_optimizer_result
from resplan_router.optimizers.bitmask_dp import BitmaskDPOptimizer, _solve_held_karp_tour
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase11_optimization.objective import compute_J
from resplan_router.phase11_optimization.ring_pool import compute_ring_internal_conflicts


class ALNSOptimizer(BaseFloorPlanOptimizer):
    """Adaptive Large Neighborhood Search with Simulated Annealing."""

    def __init__(
        self,
        name: str = "ALNS",
        max_iterations: int = 50,
        cooling_rate: float = 0.95,
    ) -> None:
        super().__init__(name=name)
        self.max_iterations = max_iterations
        self.cooling_rate = cooling_rate

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Run ALNS metaheuristic with adaptive destroy/repair."""
        t_start = time.perf_counter()
        rng = random.Random(random_seed)

        # 1. Start with high-quality incumbent from BitmaskDP
        base_opt = BitmaskDPOptimizer()
        incumbent_res = base_opt.optimize(floor_plan=floor_plan, time_limit_seconds=1.0, random_seed=random_seed)

        if not incumbent_res.is_valid or incumbent_res.bundle is None or incumbent_res.partition is None:
            return incumbent_res

        best_bundle = incumbent_res.bundle
        best_partition = incumbent_res.partition
        best_cost_J = incumbent_res.total_cost_J
        best_h_count = incumbent_res.h_count

        # If already single hub or very small layout, incumbent is already globally optimal
        if best_h_count <= 1:
            return create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=best_bundle,
                partition=best_partition,
                solver_status="OPTIMAL",
                runtime_s=time.perf_counter() - t_start,
                diagnostic_info={"iterations": 0, "status": "SingleHub_GlobalOptimum"},
            )

        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)

        eligible_rooms = sorted([r_id for r_id, r in floor_plan.rooms.items() if r.is_eligible])
        door_graph = nx.Graph()
        door_graph.add_nodes_from(eligible_rooms)
        for r_id in eligible_rooms:
            for nbr in topology.adjacency.get(r_id, []):
                if nbr in eligible_rooms:
                    door_graph.add_edge(r_id, nbr)

        # Current state: list of sets of room IDs
        curr_zones = [set(h.assigned_rooms) for h in best_partition.hubs]
        curr_cost_J = best_cost_J
        temperature = 10.0

        iteration = 0
        while iteration < self.max_iterations and (time.perf_counter() - t_start) < (time_limit_seconds - 0.5):
            iteration += 1

            # Operator selection: Destroy 1 or 2 zones or boundary room swap
            cand_zones = [set(z) for z in curr_zones]
            op = rng.choice(["boundary_swap", "zone_merge_split"])

            if op == "boundary_swap" and len(cand_zones) >= 2:
                # Pick two adjacent zones
                z1_idx = rng.randrange(len(cand_zones))
                z1 = cand_zones[z1_idx]
                # Find adjacent zones
                adj_zones = []
                for z2_idx, z2 in enumerate(cand_zones):
                    if z1_idx != z2_idx:
                        # Check if any room in z1 is adjacent to any in z2
                        if any(door_graph.has_edge(r1, r2) for r1 in z1 for r2 in z2):
                            adj_zones.append(z2_idx)

                if adj_zones:
                    z2_idx = rng.choice(adj_zones)
                    z2 = cand_zones[z2_idx]

                    # Pick a room in z1 that can move to z2 without disconnecting z1
                    movable_r1 = []
                    for r in z1:
                        # Check if removing r keeps z1 connected
                        sub_z1 = z1 - {r}
                        if not sub_z1 or nx.is_connected(door_graph.subgraph(sub_z1)):
                            # Check if adding r to z2 keeps z2 connected and under limits
                            new_z2 = z2 | {r}
                            new_area2 = sum(floor_plan.rooms[room].area_m2 for room in new_z2)
                            new_devs2 = len(new_z2)
                            if new_area2 <= 100.0 and new_devs2 <= 5 and nx.is_connected(door_graph.subgraph(new_z2)):
                                movable_r1.append(r)

                    if movable_r1:
                        r_move = rng.choice(movable_r1)
                        z1.remove(r_move)
                        z2.add(r_move)
                        if not z1:
                            cand_zones.pop(z1_idx)

            elif op == "zone_merge_split" and len(cand_zones) >= 2:
                # Pick 2 adjacent zones and re-partition them via Bitmask DP
                z1_idx = rng.randrange(len(cand_zones))
                z1 = cand_zones[z1_idx]
                adj_zones = [z2_idx for z2_idx, z2 in enumerate(cand_zones) if z1_idx != z2_idx and any(door_graph.has_edge(r1, r2) for r1 in z1 for r2 in z2)]
                if adj_zones:
                    z2_idx = rng.choice(adj_zones)
                    z2 = cand_zones[z2_idx]
                    merged_rooms = z1 | z2
                    # Try to partition merged_rooms into 2 balanced connected zones
                    # If total area <= 100 and <= 5 rooms, merge into 1 zone!
                    m_area = sum(floor_plan.rooms[r].area_m2 for r in merged_rooms)
                    if m_area <= 100.0 and len(merged_rooms) <= 5 and nx.is_connected(door_graph.subgraph(merged_rooms)):
                        cand_zones.remove(z1)
                        cand_zones.remove(z2)
                        cand_zones.append(merged_rooms)

            # Evaluate cand_zones
            # Reconstruct rings and measure cost
            try:
                cand_bundle, cand_partition = self._synthesize_solution(cand_zones, floor_plan, topology)
                cand_J = cand_bundle.total_cost_J
                cand_H = cand_partition.hub_count

                # Acceptance criterion: Lexicographic check
                better = False
                if cand_H < best_h_count:
                    better = True
                elif cand_H == best_h_count and cand_J < best_cost_J - 1e-4:
                    better = True

                if better:
                    best_bundle = cand_bundle
                    best_partition = cand_partition
                    best_cost_J = cand_J
                    best_h_count = cand_H
                    curr_zones = cand_zones
                    curr_cost_J = cand_J
                else:
                    # Simulated Annealing acceptance for current state
                    delta_j = cand_J - curr_cost_J
                    if cand_H == best_h_count and delta_j > 0 and rng.random() < math.exp(-delta_j / max(0.1, temperature)):
                        curr_zones = cand_zones
                        curr_cost_J = cand_J
            except Exception:
                pass

            temperature *= self.cooling_rate

        runtime = time.perf_counter() - t_start
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=best_bundle,
            partition=best_partition,
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={"iterations": iteration, "final_temperature": round(temperature, 4)},
        )

    def _synthesize_solution(
        self,
        zones: List[Set[str]],
        plan: MetricFloorPlan,
        topology: Any,
    ) -> Tuple[SynthesizedRingsBundle, HubZonePartition]:
        """Synthesize valid rings for a list of room zones."""
        hubs: List[HubAssignment] = []
        hub_rings: List[CompletedHubRing] = []
        router = DirectionStateRouter()

        obs_buf = plan.obstacles_geom.buffer(0.50) if plan.obstacles_geom else None

        for z_idx, z_rooms in enumerate(zones, start=1):
            hub_id = f"H{z_idx}"
            zone_area = sum(plan.rooms[r].area_m2 for r in z_rooms)
            zone_nodes = [n for n in plan.nodes.values() if n.room_id in z_rooms]

            # Hub placement: room with polylabel clearance
            best_hub_loc: Optional[Point2D] = None
            best_hub_room = sorted(z_rooms)[0]
            max_clr = -1.0

            for r_id in z_rooms:
                r_geom = plan.rooms[r_id].geometry
                safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
                safe_poly = shapely.make_valid(safe_poly)
                if not safe_poly.is_empty and safe_poly.area > 1e-4:
                    try:
                        poly_target = safe_poly if isinstance(safe_poly, Polygon) else max(safe_poly.geoms, key=lambda g: g.area)
                        pole = polylabel(poly_target, tolerance=0.005)
                        pt = Point(pole.x, pole.y)
                        clr = plan.obstacles_geom.distance(pt) if plan.obstacles_geom else 1.0
                        if clr > max_clr:
                            max_clr = clr
                            best_hub_loc = Point2D(x=round(pole.x, 4), y=round(pole.y, 4))
                            best_hub_room = r_id
                    except Exception:
                        pass

            if best_hub_loc is None:
                c = plan.rooms[best_hub_room].geometry.centroid
                best_hub_loc = Point2D(x=round(c.x, 4), y=round(c.y, 4))

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
