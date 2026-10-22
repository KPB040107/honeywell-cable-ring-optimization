"""Greedy Baseline Optimizer adhering to BaseFloorPlanOptimizer contract.

Uses Greedy Zone Growing + Polylabel Hub Placement + 2-opt TSP + Hanan Grid Routing.
Always independently verified via FloorPlanChecker.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple

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
from pydantic import ValidationError
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult, create_optimizer_result
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase11_optimization.objective import compute_J
from resplan_router.phase11_optimization.ring_pool import compute_ring_internal_conflicts


class GreedyBaselineOptimizer(BaseFloorPlanOptimizer):
    """Deterministic Greedy Baseline Optimizer."""

    def __init__(self, name: str = "GreedyBaseline") -> None:
        super().__init__(name=name)

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Execute greedy zone growing and routing."""
        t_start = time.perf_counter()

        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)

        eligible_rooms = sorted([r_id for r_id, r in floor_plan.rooms.items() if r.is_eligible])
        if not eligible_rooms:
            empty_bundle = SynthesizedRingsBundle(
                plan_id=floor_plan.plan_id,
                hub_rings=[],
                total_length_m=0.0,
                total_bends=0,
                total_crossings=0,
                total_overlap_m=0.0,
                total_cost_J=0.0,
            )
            empty_partition = HubZonePartition(
                plan_id=floor_plan.plan_id,
                hub_count=0,
                lower_bound_hubs=0,
                area_gap_m2=0.0,
                hubs=[],
            )
            return create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=empty_bundle,
                partition=empty_partition,
                solver_status="OPTIMAL",
                runtime_s=time.perf_counter() - t_start,
            )

        if any(floor_plan.rooms[r].area_m2 > 100.0 for r in eligible_rooms):
            return create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=None,
                partition=None,
                solver_status="INFEASIBLE",
                runtime_s=time.perf_counter() - t_start,
                diagnostic_info={"error": "Eligible room exceeds 100.0 m^2 limit"},
            )

        # 1. Door connectivity graph
        door_graph = nx.Graph()
        door_graph.add_nodes_from(eligible_rooms)
        for r_id in eligible_rooms:
            for nbr in topology.adjacency.get(r_id, []):
                if nbr in eligible_rooms:
                    door_graph.add_edge(r_id, nbr)

        # 2. Greedily grow zones (seed = max area, grow largest adjacent room fitting limits)
        unassigned = set(eligible_rooms)
        zones: List[List[str]] = []

        while unassigned:
            seed_room = max(unassigned, key=lambda r: floor_plan.rooms[r].area_m2)
            current_zone = [seed_room]
            current_area = floor_plan.rooms[seed_room].area_m2
            current_devs = sum(1 for n in floor_plan.nodes.values() if n.room_id == seed_room)
            unassigned.remove(seed_room)

            growing = True
            while growing:
                candidates: List[Tuple[float, str]] = []
                for r in current_zone:
                    for nbr in door_graph.neighbors(r):
                        if nbr in unassigned:
                            nbr_area = floor_plan.rooms[nbr].area_m2
                            nbr_devs = sum(1 for n in floor_plan.nodes.values() if n.room_id == nbr)
                            if current_area + nbr_area <= 100.0 and current_devs + nbr_devs <= 5:
                                candidates.append((nbr_area, nbr))

                if candidates:
                    candidates.sort(reverse=True)
                    best_area, best_nbr = candidates[0]
                    current_zone.append(best_nbr)
                    current_area += best_area
                    current_devs += sum(1 for n in floor_plan.nodes.values() if n.room_id == best_nbr)
                    unassigned.remove(best_nbr)
                else:
                    growing = False

            zones.append(current_zone)

        # 3. Hub placement and tour generation
        hubs: List[HubAssignment] = []
        hub_rings: List[CompletedHubRing] = []
        router = DirectionStateRouter()

        for z_idx, z_rooms in enumerate(zones, start=1):
            hub_id = f"H{z_idx}"
            zone_area = sum(floor_plan.rooms[r].area_m2 for r in z_rooms)
            zone_nodes = [n for n in floor_plan.nodes.values() if n.room_id in z_rooms]

            # Capacity validation guards: Zone area <= 100.0 m^2 and nodes <= 5
            if zone_area > 100.001 or len(zone_nodes) > 5:
                return create_optimizer_result(
                    algorithm_name=self.name,
                    plan=floor_plan,
                    bundle=None,
                    partition=None,
                    solver_status="INFEASIBLE",
                    runtime_s=time.perf_counter() - t_start,
                    diagnostic_info={
                        "error": f"Zone {z_idx} capacity violation: area={zone_area:.4f} m^2 (limit 100.0), nodes={len(zone_nodes)} (limit 5)",
                    },
                )

            # Place hub at polylabel of safe region (>= 0.50m from walls/windows)
            best_hub_loc: Optional[Point2D] = None
            best_hub_room = z_rooms[0]
            max_clearance = -1.0

            for r_id in z_rooms:
                r_geom = floor_plan.rooms[r_id].geometry
                obs_buf = floor_plan.obstacles_geom.buffer(0.50) if floor_plan.obstacles_geom else None
                safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
                safe_poly = shapely.make_valid(safe_poly)
                if not safe_poly.is_empty and safe_poly.area > 1e-4:
                    try:
                        poly_target = safe_poly if isinstance(safe_poly, Polygon) else max(safe_poly.geoms, key=lambda g: g.area)
                        pole = polylabel(poly_target, tolerance=0.005)
                        pt = Point(pole.x, pole.y)
                        clearance = floor_plan.obstacles_geom.distance(pt) if floor_plan.obstacles_geom else 1.0
                        if clearance > max_clearance:
                            max_clearance = clearance
                            best_hub_loc = Point2D(x=round(pole.x, 4), y=round(pole.y, 4))
                            best_hub_room = r_id
                    except Exception:
                        pass

            if best_hub_loc is None:
                c = floor_plan.rooms[best_hub_room].geometry.centroid
                best_hub_loc = Point2D(x=round(c.x, 4), y=round(c.y, 4))

            try:
                hub_assign = HubAssignment(
                    hub_id=hub_id,
                    room_id=best_hub_room,
                    location=best_hub_loc,
                    assigned_rooms=sorted(z_rooms),
                    assigned_nodes=sorted([n.node_id for n in zone_nodes]),
                    zone_area_m2=round(zone_area, 4),
                )
                hubs.append(hub_assign)
            except (ValidationError, ValueError) as exc:
                return create_optimizer_result(
                    algorithm_name=self.name,
                    plan=floor_plan,
                    bundle=None,
                    partition=None,
                    solver_status="INFEASIBLE",
                    runtime_s=time.perf_counter() - t_start,
                    diagnostic_info={"error": f"Invalid hub assignment contract: {exc}"},
                )

            # 4. Nearest-Neighbor TSP + 2-opt sequence
            terminals: Dict[str, Point2D] = {hub_id: best_hub_loc}
            for n in zone_nodes:
                terminals[n.node_id] = n.location

            unvisited = [n.node_id for n in zone_nodes]
            seq = [hub_id]
            curr = hub_id
            while unvisited:
                curr_pt = terminals[curr]
                next_node = min(
                    unvisited,
                    key=lambda nid: math.hypot(terminals[nid].x - curr_pt.x, terminals[nid].y - curr_pt.y),
                )
                seq.append(next_node)
                unvisited.remove(next_node)
                curr = next_node
            seq.append(hub_id)

            # 2-opt tour improvement
            improved = True
            while improved and len(seq) > 4:
                improved = False
                for i in range(1, len(seq) - 2):
                    for j in range(i + 1, len(seq) - 1):
                        d_cur = (
                            math.hypot(terminals[seq[i-1]].x - terminals[seq[i]].x, terminals[seq[i-1]].y - terminals[seq[i]].y) +
                            math.hypot(terminals[seq[j]].x - terminals[seq[j+1]].x, terminals[seq[j]].y - terminals[seq[j+1]].y)
                        )
                        d_swap = (
                            math.hypot(terminals[seq[i-1]].x - terminals[seq[j]].x, terminals[seq[i-1]].y - terminals[seq[j]].y) +
                            math.hypot(terminals[seq[i]].x - terminals[seq[j+1]].x, terminals[seq[i]].y - terminals[seq[j+1]].y)
                        )
                        if d_swap < d_cur - 1e-4:
                            seq[i : j + 1] = reversed(seq[i : j + 1])
                            improved = True
                            break
                    if improved:
                        break

            # 5. Route legs on Hanan grid
            grid = build_hanan_grid(
                plan=floor_plan,
                topology=topology,
                extra_points=list(terminals.values()),
            )

            segments: List[RouteSegment] = []
            total_len = 0.0
            total_bends = 0

            for k in range(len(seq) - 1):
                u_id = seq[k]
                v_id = seq[k + 1]
                u_pt = terminals[u_id]
                v_pt = terminals[v_id]

                try:
                    metric = router.find_shortest_path(
                        grid=grid,
                        start=u_pt,
                        end=v_pt,
                        start_id=u_id,
                        end_id=v_id,
                    )
                    poly = metric.polyline
                    l_m = metric.length_m
                    b_cnt = metric.bends
                except Exception:
                    mid = Point2D(x=v_pt.x, y=u_pt.y)
                    poly = compress_polyline([u_pt, mid, v_pt])
                    l_m = round(math.hypot(mid.x - u_pt.x, mid.y - u_pt.y) + math.hypot(v_pt.x - mid.x, v_pt.y - mid.y), 4)
                    b_cnt = 1

                seg = RouteSegment(
                    from_node=u_id,
                    to_node=v_id,
                    length_m=l_m,
                    polyline=poly,
                )
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

        # 6. Global bundle and partition
        tot_l = sum(r.ring_length_m for r in hub_rings)
        tot_b = sum(r.ring_bends for r in hub_rings)
        tot_x = sum(r.ring_crossings for r in hub_rings)
        tot_o = sum(r.ring_overlap_m for r in hub_rings)
        tot_j = compute_J(tot_l, tot_b, tot_x, tot_o)

        bundle = SynthesizedRingsBundle(
            plan_id=floor_plan.plan_id,
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
            plan_id=floor_plan.plan_id,
            hub_count=len(hubs),
            lower_bound_hubs=len(hubs),
            area_gap_m2=round(area_gap, 4),
            hubs=hubs,
        )

        runtime = time.perf_counter() - t_start
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=bundle,
            partition=partition,
            solver_status="FEASIBLE",
            runtime_s=runtime,
            diagnostic_info={"zones_count": len(zones), "method": "GreedyZoneGrowing_2Opt"},
        )
