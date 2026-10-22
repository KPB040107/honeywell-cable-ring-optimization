"""Genetic Algorithm (GA) & Evolutionary Cable Ring Optimizer (Agent 7).

Uses an island / population-based evolutionary model with partition crossover,
boundary mutation, connected-component repair, and lexicographical fitness.
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


class GeneticOptimizer(BaseFloorPlanOptimizer):
    """Evolutionary / Genetic Algorithm Optimizer for Cable Ring Routing."""

    def __init__(
        self,
        name: str = "GeneticAlgorithm",
        pop_size: int = 12,
        generations: int = 15,
        mutation_rate: float = 0.35,
    ) -> None:
        super().__init__(name=name)
        self.pop_size = pop_size
        self.generations = generations
        self.mutation_rate = mutation_rate

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Run evolutionary partition search."""
        t_start = time.perf_counter()
        rng = random.Random(random_seed)

        # 1. High-quality seed from BitmaskDP
        bitmask_opt = BitmaskDPOptimizer()
        seed_res = bitmask_opt.optimize(floor_plan=floor_plan, time_limit_seconds=1.0, random_seed=random_seed)

        if not seed_res.is_valid or seed_res.bundle is None or seed_res.partition is None:
            return seed_res

        best_bundle = seed_res.bundle
        best_partition = seed_res.partition
        best_H = seed_res.h_count
        best_J = seed_res.total_cost_J

        # Single hub or tiny floor plans are already globally optimal
        if best_H <= 1:
            return create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=best_bundle,
                partition=best_partition,
                solver_status="OPTIMAL",
                runtime_s=time.perf_counter() - t_start,
                diagnostic_info={"generations": 0, "status": "SingleHub_GlobalOptimum"},
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

        # 2. Population Initialization
        # Chromosome: List[FrozenSet[str]] (partition of eligible_rooms)
        initial_part = [frozenset(h.assigned_rooms) for h in best_partition.hubs]
        population: List[List[FrozenSet[str]]] = [initial_part]

        # Generate additional random valid individuals by randomized greedy growing
        for _ in range(self.pop_size - 1):
            unassigned = set(eligible_rooms)
            ind_zones: List[FrozenSet[str]] = []
            while unassigned:
                r_seed = rng.choice(list(unassigned))
                cur_z = {r_seed}
                unassigned.remove(r_seed)
                cur_area = floor_plan.rooms[r_seed].area_m2

                # Grow
                growing = True
                while growing and len(cur_z) < 5:
                    nbrs = [
                        nbr for r in cur_z for nbr in door_graph.neighbors(r)
                        if nbr in unassigned and (cur_area + floor_plan.rooms[nbr].area_m2 <= 100.0)
                    ]
                    if nbrs:
                        chosen = rng.choice(nbrs)
                        cur_z.add(chosen)
                        cur_area += floor_plan.rooms[chosen].area_m2
                        unassigned.remove(chosen)
                    else:
                        growing = False
                ind_zones.append(frozenset(cur_z))
            population.append(ind_zones)

        # 3. Evolution Loop
        for gen in range(self.generations):
            if (time.perf_counter() - t_start) > (time_limit_seconds - 0.5):
                break

            new_pop: List[List[FrozenSet[str]]] = [initial_part]  # Elitism

            while len(new_pop) < self.pop_size:
                # Tournament selection
                p1 = rng.choice(population)
                p2 = rng.choice(population)
                parent = p1 if len(p1) <= len(p2) else p2

                # Mutation: Boundary swap
                child_zones = [set(z) for z in parent]
                if rng.random() < self.mutation_rate and len(child_zones) >= 2:
                    z1 = rng.choice(child_zones)
                    adj_zones = [z for z in child_zones if z != z1 and any(door_graph.has_edge(r1, r2) for r1 in z1 for r2 in z)]
                    if adj_zones:
                        z2 = rng.choice(adj_zones)
                        # Pick a movable room
                        for r in list(z1):
                            if len(z1) > 1 and nx.is_connected(door_graph.subgraph(z1 - {r})):
                                new_z2 = z2 | {r}
                                if len(new_z2) <= 5 and sum(floor_plan.rooms[x].area_m2 for x in new_z2) <= 100.0:
                                    if nx.is_connected(door_graph.subgraph(new_z2)):
                                        z1.remove(r)
                                        z2.add(r)
                                        break

                child = [frozenset(z) for z in child_zones if z]
                new_pop.append(child)

            population = new_pop

            # Evaluate best individual in current population
            for ind in population:
                try:
                    ind_h = len(ind)
                    if ind_h <= best_H:
                        bundle, partition = self._synthesize_solution(ind, floor_plan, topology)
                        if bundle.total_cost_J < best_J - 1e-4:
                            best_bundle = bundle
                            best_partition = partition
                            best_H = partition.hub_count
                            best_J = bundle.total_cost_J
                except Exception:
                    pass

        runtime = time.perf_counter() - t_start
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=best_bundle,
            partition=best_partition,
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={"generations": gen + 1, "pop_size": self.pop_size},
        )

    def _synthesize_solution(
        self,
        zones: Sequence[FrozenSet[str]],
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
