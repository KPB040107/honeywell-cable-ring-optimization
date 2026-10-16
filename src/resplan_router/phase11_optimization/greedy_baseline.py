"""Deterministic Greedy Baseline Solver for Cable-Ring Routing.

Constructs a valid benchmark incumbent by:
1. Greedily growing door-connected zones under 100 m^2 and 5-device capacity.
2. Placing hubs at polylabel (pole-of-inaccessibility) locations with >= 0.50m clearance.
3. Ordering ring terminals using Nearest-Neighbor heuristic followed by 2-opt tour improvement.
4. Routing legs using DirectionStateRouter on the Hanan grid.
5. Evaluating full solution with the independent FloorPlanChecker.
6. Recording H, L, B, X, O, J, area gap, runtime, validity, and seed.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
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
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import HananRoutingGrid, build_hanan_grid
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase11_optimization.objective import LexicographicCost, compute_J, evaluate_solution
from resplan_router.phase11_optimization.ring_pool import compute_ring_internal_conflicts


@dataclass
class GreedyBaselineResult:
    """Outcome and benchmark scoreboard of deterministic greedy baseline solver."""

    bundle: SynthesizedRingsBundle
    partition: HubZonePartition
    cost: LexicographicCost
    h_count: int
    total_length_m: float
    total_bends: int
    total_crossings: int
    total_overlap_m: float
    total_cost_J: float
    delta_area_gap_m2: float
    is_valid: bool
    runtime_s: float
    seed: int = 42

    def as_dict(self) -> Dict[str, Any]:
        return {
            "H": self.h_count,
            "L": self.total_length_m,
            "B": self.total_bends,
            "X": self.total_crossings,
            "O": self.total_overlap_m,
            "J": self.total_cost_J,
            "delta_area_gap_m2": self.delta_area_gap_m2,
            "is_valid": self.is_valid,
            "runtime_s": round(self.runtime_s, 4),
            "seed": self.seed,
        }


def solve_greedy_baseline(
    plan: MetricFloorPlan,
    topology: Optional[Any] = None,
    seed: int = 42,
) -> GreedyBaselineResult:
    """Run deterministic greedy baseline solver."""
    t0 = time.perf_counter()

    if topology is None:
        portals = extract_all_portals(plan)
        topology = MacroTopologyGraph.build_topology(plan, portals)

    eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
    if not eligible_rooms:
        empty_bundle = SynthesizedRingsBundle(
            plan_id=plan.plan_id,
            hub_rings=[],
            total_length_m=0.0,
            total_bends=0,
            total_crossings=0,
            total_overlap_m=0.0,
            total_cost_J=0.0,
        )
        empty_partition = HubZonePartition(
            plan_id=plan.plan_id,
            hub_count=0,
            lower_bound_hubs=0,
            area_gap_m2=0.0,
            hubs=[],
        )
        cost = LexicographicCost(H=0, J=0.0, delta_area_gap=0.0, is_valid=True)
        return GreedyBaselineResult(
            bundle=empty_bundle,
            partition=empty_partition,
            cost=cost,
            h_count=0,
            total_length_m=0.0,
            total_bends=0,
            total_crossings=0,
            total_overlap_m=0.0,
            total_cost_J=0.0,
            delta_area_gap_m2=0.0,
            is_valid=True,
            runtime_s=time.perf_counter() - t0,
            seed=seed,
        )

    # 1. Door connectivity graph
    door_graph = nx.Graph()
    door_graph.add_nodes_from(eligible_rooms)
    for r_id in eligible_rooms:
        for nbr in topology.adjacency.get(r_id, []):
            if nbr in eligible_rooms:
                door_graph.add_edge(r_id, nbr)

    # 2. Greedily grow zones
    unassigned = set(eligible_rooms)
    zones: List[List[str]] = []

    while unassigned:
        # Pick unassigned room with largest area
        seed_room = max(unassigned, key=lambda r: plan.rooms[r].area_m2)
        current_zone = [seed_room]
        current_area = plan.rooms[seed_room].area_m2
        current_devs = sum(1 for n in plan.nodes.values() if n.room_id == seed_room)
        unassigned.remove(seed_room)

        # Greedily add adjacent rooms
        growing = True
        while growing:
            candidates: List[Tuple[float, str]] = []
            for r in current_zone:
                for nbr in door_graph.neighbors(r):
                    if nbr in unassigned:
                        nbr_area = plan.rooms[nbr].area_m2
                        nbr_devs = sum(1 for n in plan.nodes.values() if n.room_id == nbr)
                        if current_area + nbr_area <= 100.0 and current_devs + nbr_devs <= 5:
                            candidates.append((nbr_area, nbr))

            if candidates:
                # Add candidate with largest area that fits
                candidates.sort(reverse=True)
                best_area, best_nbr = candidates[0]
                current_zone.append(best_nbr)
                current_area += best_area
                current_devs += sum(1 for n in plan.nodes.values() if n.room_id == best_nbr)
                unassigned.remove(best_nbr)
            else:
                growing = False

        zones.append(current_zone)

    # 3. For each zone, place hub at polylabel
    hubs: List[HubAssignment] = []
    hub_rings: List[CompletedHubRing] = []

    router = DirectionStateRouter()

    for z_idx, z_rooms in enumerate(zones, start=1):
        hub_id = f"H{z_idx}"
        zone_area = sum(plan.rooms[r].area_m2 for r in z_rooms)
        zone_nodes = [n for n in plan.nodes.values() if n.room_id in z_rooms]

        # Find best hub room and location (polylabel on safe region >= 0.50m)
        best_hub_loc: Optional[Point2D] = None
        best_hub_room = z_rooms[0]
        max_dist = -1.0

        for r_id in z_rooms:
            r_geom = plan.rooms[r_id].geometry
            obs_buf = plan.obstacles_geom.buffer(0.50) if plan.obstacles_geom else None
            safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
            safe_poly = shapely.make_valid(safe_poly)
            if not safe_poly.is_empty and safe_poly.area > 1e-4:
                try:
                    poly_target = safe_poly if isinstance(safe_poly, Polygon) else max(safe_poly.geoms, key=lambda g: g.area)
                    pole = polylabel(poly_target, tolerance=0.005)
                    pt = Point(pole.x, pole.y)
                    dist = plan.obstacles_geom.distance(pt) if plan.obstacles_geom else 1.0
                    if dist > max_dist:
                        max_dist = dist
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

        # 2-opt local search on tour [1:-1]
        improved = True
        while improved and len(seq) > 4:
            improved = False
            for i in range(1, len(seq) - 2):
                for j in range(i + 1, len(seq) - 1):
                    # Current cost
                    d_cur = (
                        math.hypot(terminals[seq[i-1]].x - terminals[seq[i]].x, terminals[seq[i-1]].y - terminals[seq[i]].y) +
                        math.hypot(terminals[seq[j]].x - terminals[seq[j+1]].x, terminals[seq[j]].y - terminals[seq[j+1]].y)
                    )
                    # Swapped cost
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
            plan=plan,
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
                # Direct fallback
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

    # 6. Assemble partition and bundle
    areas = [h.zone_area_m2 for h in hubs]
    area_gap = round(max(areas) - min(areas), 4) if len(areas) >= 2 else 0.0

    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=len(hubs),
        lower_bound_hubs=len(hubs),
        area_gap_m2=area_gap,
        hubs=hubs,
    )

    bundle = SynthesizedRingsBundle(
        plan_id=plan.plan_id,
        hub_rings=hub_rings,
        total_length_m=round(sum(r.ring_length_m for r in hub_rings), 4),
        total_bends=sum(r.ring_bends for r in hub_rings),
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=0.0,
    )

    # 7. Evaluate with independent FloorPlanChecker
    cost, report = evaluate_solution(plan=plan, bundle=bundle, partition=partition, topology=topology)

    bundle = bundle.model_copy(update={
        "total_crossings": report.total_crossings,
        "total_overlap_m": report.total_overlap_m,
        "total_cost_J": report.total_cost_J,
    })

    elapsed = time.perf_counter() - t0

    return GreedyBaselineResult(
        bundle=bundle,
        partition=partition,
        cost=cost,
        h_count=report.hub_count,
        total_length_m=report.total_length_m,
        total_bends=report.total_bends,
        total_crossings=report.total_crossings,
        total_overlap_m=report.total_overlap_m,
        total_cost_J=report.total_cost_J,
        delta_area_gap_m2=report.area_gap_m2,
        is_valid=report.is_valid,
        runtime_s=elapsed,
        seed=seed,
    )
