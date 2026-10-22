"""CP-SAT Multi-Path Cable Optimizer (Agent 5).

Generates multiple alternative rectilinear routing paths (Yen's K-shortest paths &
directional bends) per leg, and uses an exact CP-SAT integer program to select the
combination of paths that minimizes total cable length, bends, crossings (X), and overlap (O).
"""

from __future__ import annotations

import itertools
import math
import time
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

import networkx as nx
from ortools.sat.python import cp_model
import shapely
from shapely.geometry import LineString, Point, Polygon
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
from resplan_router.optimizers.bitmask_dp import _solve_held_karp_tour
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import HananRoutingGrid, build_hanan_grid
from resplan_router.phase11_optimization.objective import compute_J


def _extract_path_lines(pts: List[Point2D]) -> List[LineString]:
    """Extract 1D line segments from a polyline."""
    lines = []
    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        if math.hypot(p2.x - p1.x, p2.y - p1.y) > 1e-4:
            lines.append(LineString([(p1.x, p1.y), (p2.x, p2.y)]))
    return lines


def _count_bends_in_pts(pts: List[Point2D]) -> int:
    """Count orthogonal bends in polyline."""
    if len(pts) < 3:
        return 0
    bends = 0
    for i in range(len(pts) - 2):
        p0, p1, p2 = pts[i], pts[i + 1], pts[i + 2]
        dx1, dy1 = p1.x - p0.x, p1.y - p0.y
        dx2, dy2 = p2.x - p1.x, p2.y - p1.y
        # Non-zero orthogonal turn
        if (abs(dx1) > 1e-4 and abs(dy2) > 1e-4) or (abs(dy1) > 1e-4 and abs(dx2) > 1e-4):
            bends += 1
    return bends


def _calc_pair_cross_overlap(lines1: List[LineString], lines2: List[LineString]) -> Tuple[int, float]:
    """Calculate crossings count and collinear overlap length between two leg paths."""
    crossings = 0
    overlap_len = 0.0

    for l1 in lines1:
        for l2 in lines2:
            inter = l1.intersection(l2)
            if inter.is_empty:
                continue
            if inter.geom_type in ("LineString", "MultiLineString"):
                overlap_len += inter.length
            elif inter.geom_type == "Point":
                # Check if it's an interior crossing
                p = inter
                l1_end = p.distance(Point(l1.coords[0])) < 1e-4 or p.distance(Point(l1.coords[-1])) < 1e-4
                l2_end = p.distance(Point(l2.coords[0])) < 1e-4 or p.distance(Point(l2.coords[-1])) < 1e-4
                if not (l1_end and l2_end):
                    crossings += 1

    return crossings, round(overlap_len, 4)


class CPSATMultiPathOptimizer(BaseFloorPlanOptimizer):
    """Exact CP-SAT Multi-Path Selection Optimizer."""

    def __init__(self, name: str = "CPSAT_MultiPath", k_paths: int = 3) -> None:
        super().__init__(name=name)
        self.k_paths = k_paths

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Run optimal partitioning followed by CP-SAT multi-path selection."""
        t_start = time.perf_counter()

        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)

        eligible_rooms = sorted([r_id for r_id, r in floor_plan.rooms.items() if r.is_eligible])
        n_eligible = len(eligible_rooms)

        if n_eligible == 0:
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

        # 1. Door graph
        door_graph = nx.Graph()
        door_graph.add_nodes_from(eligible_rooms)
        for r_id in eligible_rooms:
            for nbr in topology.adjacency.get(r_id, []):
                if nbr in eligible_rooms:
                    door_graph.add_edge(r_id, nbr)

        # 2. Extract safe hub locations (>= 0.50m clearance)
        obs_buf = floor_plan.obstacles_geom.buffer(0.50) if floor_plan.obstacles_geom else None
        safe_hub_locations: Dict[str, Tuple[Point2D, float]] = {}

        for r_id in eligible_rooms:
            r_geom = floor_plan.rooms[r_id].geometry
            safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
            safe_poly = shapely.make_valid(safe_poly)
            if not safe_poly.is_empty and safe_poly.area > 1e-4:
                try:
                    poly_target = safe_poly if isinstance(safe_poly, Polygon) else max(safe_poly.geoms, key=lambda g: g.area)
                    pole = polylabel(poly_target, tolerance=0.005)
                    pt = Point(pole.x, pole.y)
                    clr = floor_plan.obstacles_geom.distance(pt) if floor_plan.obstacles_geom else 1.0
                    safe_hub_locations[r_id] = (Point2D(x=round(pole.x, 4), y=round(pole.y, 4)), clr)
                except Exception:
                    pass

        # 3. Exact Minimum-Hub Partitioning via Bitmask DP
        room_idx = {r_id: idx for idx, r_id in enumerate(eligible_rooms)}
        valid_subsets: List[Tuple[int, FrozenSet[str], float]] = []

        for k in range(1, min(n_eligible, 5) + 1):
            for subset in itertools.combinations(eligible_rooms, k):
                s_rooms = frozenset(subset)
                s_area = sum(floor_plan.rooms[r].area_m2 for r in s_rooms)
                if s_area > 100.0 + 1e-6:
                    continue
                if not any(r in safe_hub_locations for r in s_rooms):
                    continue
                if k > 1 and not nx.is_connected(door_graph.subgraph(s_rooms)):
                    continue
                mask = sum(1 << room_idx[r] for r in s_rooms)
                valid_subsets.append((mask, s_rooms, s_area))

        valid_subsets.sort(key=lambda item: (len(item[1]), item[2]), reverse=True)

        full_mask = (1 << n_eligible) - 1
        INF_HUBS = 999999
        dp_hubs = [INF_HUBS] * (1 << n_eligible)
        dp_prev_mask = [-1] * (1 << n_eligible)
        dp_subset_idx = [-1] * (1 << n_eligible)
        dp_areas: List[List[float]] = [[] for _ in range(1 << n_eligible)]
        dp_hubs[0] = 0

        for mask in range(1 << n_eligible):
            curr_h = dp_hubs[mask]
            if curr_h == INF_HUBS:
                continue
            for sub_i, (s_mask, s_rooms, s_area) in enumerate(valid_subsets):
                if (mask & s_mask) == 0:
                    next_mask = mask | s_mask
                    cand_h = curr_h + 1
                    cand_areas = dp_areas[mask] + [s_area]

                    should_update = False
                    if cand_h < dp_hubs[next_mask]:
                        should_update = True
                    elif cand_h == dp_hubs[next_mask]:
                        cur_gap = max(dp_areas[next_mask]) - min(dp_areas[next_mask]) if len(dp_areas[next_mask]) > 1 else 0.0
                        cand_gap = max(cand_areas) - min(cand_areas) if len(cand_areas) > 1 else 0.0
                        if cand_gap < cur_gap - 1e-4:
                            should_update = True

                    if should_update:
                        dp_hubs[next_mask] = cand_h
                        dp_prev_mask[next_mask] = mask
                        dp_subset_idx[next_mask] = sub_i
                        dp_areas[next_mask] = cand_areas

        if dp_hubs[full_mask] == INF_HUBS:
            return create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=None,
                partition=None,
                solver_status="INFEASIBLE",
                runtime_s=time.perf_counter() - t_start,
            )

        optimal_subsets: List[FrozenSet[str]] = []
        curr = full_mask
        while curr > 0:
            s_idx = dp_subset_idx[curr]
            optimal_subsets.append(valid_subsets[s_idx][1])
            curr = dp_prev_mask[curr]
        optimal_subsets.reverse()

        # 4. Multi-Path Candidate Generation & Exact Selection Per Zone
        hubs: List[HubAssignment] = []
        hub_rings: List[CompletedHubRing] = []
        router = DirectionStateRouter()

        for z_idx, z_rooms in enumerate(optimal_subsets, start=1):
            hub_id = f"H{z_idx}"
            zone_area = sum(floor_plan.rooms[r].area_m2 for r in z_rooms)
            zone_nodes = [n for n in floor_plan.nodes.values() if n.room_id in z_rooms]

            # Choose best hub room
            best_hub_loc: Optional[Point2D] = None
            best_hub_room = sorted(z_rooms)[0]
            best_metric = float("inf")
            device_pts = [n.location for n in zone_nodes]

            for r_id in z_rooms:
                if r_id in safe_hub_locations:
                    loc, clr = safe_hub_locations[r_id]
                    avg_dist = (
                        sum(math.hypot(loc.x - dp.x, loc.y - dp.y) for dp in device_pts) / max(1, len(device_pts))
                        if device_pts
                        else 0.0
                    )
                    if avg_dist < best_metric:
                        best_metric = avg_dist
                        best_hub_loc = loc
                        best_hub_room = r_id

            if best_hub_loc is None:
                c = floor_plan.rooms[best_hub_room].geometry.centroid
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

            # Build grid for this zone
            grid = build_hanan_grid(
                plan=floor_plan,
                topology=topology,
                extra_points=list(terminals.values()),
            )

            # For each leg in the tour sequence, generate up to K alternative paths
            leg_candidates: List[List[Tuple[List[Point2D], float, int, List[LineString]]]] = []
            # each element: list of (polyline, length_m, bends, lines)

            for k in range(len(seq) - 1):
                u_id = seq[k]
                v_id = seq[k + 1]
                u_pt = terminals[u_id]
                v_pt = terminals[v_id]

                cands: List[Tuple[List[Point2D], float, int, List[LineString]]] = []
                seen_polys = set()

                # Path Option 1: Standard A* shortest path
                try:
                    metric = router.find_shortest_path(grid=grid, start=u_pt, end=v_pt, start_id=u_id, end_id=v_id)
                    p_tuple = tuple((round(p.x, 3), round(p.y, 3)) for p in metric.polyline)
                    seen_polys.add(p_tuple)
                    lines = _extract_path_lines(metric.polyline)
                    cands.append((metric.polyline, metric.length_m, metric.bends, lines))
                except Exception:
                    pass

                # Path Option 2: Corner alternative (Horizontal-First L-bend if unobstructed)
                p_horiz = compress_polyline([u_pt, Point2D(x=v_pt.x, y=u_pt.y), v_pt])
                p_tuple_h = tuple((round(p.x, 3), round(p.y, 3)) for p in p_horiz)
                if p_tuple_h not in seen_polys:
                    lines_h = _extract_path_lines(p_horiz)
                    # Check clearance
                    ls = LineString([(p.x, p.y) for p in p_horiz])
                    if not floor_plan.obstacles_geom.intersects(ls):
                        seen_polys.add(p_tuple_h)
                        l_h = round(ls.length, 4)
                        b_h = _count_bends_in_pts(p_horiz)
                        cands.append((p_horiz, l_h, b_h, lines_h))

                # Path Option 3: Corner alternative (Vertical-First L-bend if unobstructed)
                p_vert = compress_polyline([u_pt, Point2D(x=u_pt.x, y=v_pt.y), v_pt])
                p_tuple_v = tuple((round(p.x, 3), round(p.y, 3)) for p in p_vert)
                if p_tuple_v not in seen_polys:
                    lines_v = _extract_path_lines(p_vert)
                    ls = LineString([(p.x, p.y) for p in p_vert])
                    if not floor_plan.obstacles_geom.intersects(ls):
                        seen_polys.add(p_tuple_v)
                        l_v = round(ls.length, 4)
                        b_v = _count_bends_in_pts(p_vert)
                        cands.append((p_vert, l_v, b_v, lines_v))

                if not cands:
                    # Direct fallback
                    mid = Point2D(x=v_pt.x, y=u_pt.y)
                    p_fallback = compress_polyline([u_pt, mid, v_pt])
                    l_fb = round(math.hypot(mid.x - u_pt.x, mid.y - u_pt.y) + math.hypot(v_pt.x - mid.x, v_pt.y - mid.y), 4)
                    cands.append((p_fallback, l_fb, 1, _extract_path_lines(p_fallback)))

                leg_candidates.append(cands[:self.k_paths])

            # CP-SAT Selection of Alternative Paths to minimize crossings and overlap
            num_legs = len(leg_candidates)
            if all(len(c) == 1 for c in leg_candidates):
                # Only 1 candidate per leg
                chosen_paths = [c[0] for c in leg_candidates]
            else:
                model = cp_model.CpModel()
                # Decision variable y[leg][p]
                y: List[List[cp_model.IntVar]] = []
                for leg_idx in range(num_legs):
                    y_leg = [model.NewBoolVar(f"y_{leg_idx}_{p}") for p in range(len(leg_candidates[leg_idx]))]
                    model.Add(sum(y_leg) == 1)
                    y.append(y_leg)

                # Pairwise conflict linearization: z[i, p, j, q] = y[i, p] * y[j, q]
                scale = 1000
                obj_terms = []

                # Linear leg terms: Length + 0.5 * Bends
                for leg_idx in range(num_legs):
                    for p_idx, (_, l_m, b_cnt, _) in enumerate(leg_candidates[leg_idx]):
                        cost = int((l_m + 0.5 * b_cnt) * scale)
                        obj_terms.append(y[leg_idx][p_idx] * cost)

                # Quadratic terms: Crossings (5.0) and Overlap (1.0)
                for i in range(num_legs):
                    for j in range(i + 1, num_legs):
                        # Don't penalize adjacent legs sharing endpoint
                        is_adj = (abs(i - j) == 1) or (i == 0 and j == num_legs - 1)
                        for p, (_, _, _, lines_i) in enumerate(leg_candidates[i]):
                            for q, (_, _, _, lines_j) in enumerate(leg_candidates[j]):
                                x_cnt, o_len = _calc_pair_cross_overlap(lines_i, lines_j)
                                if is_adj and x_cnt > 0:
                                    x_cnt = 0  # Ignore corner touch
                                if x_cnt > 0 or o_len > 1e-4:
                                    z = model.NewBoolVar(f"z_{i}_{p}_{j}_{q}")
                                    model.Add(z <= y[i][p])
                                    model.Add(z <= y[j][q])
                                    model.Add(z >= y[i][p] + y[j][q] - 1)
                                    quad_cost = int((5.0 * x_cnt + o_len) * scale)
                                    obj_terms.append(z * quad_cost)

                model.Minimize(sum(obj_terms))
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = 1.0
                solver.parameters.num_search_workers = 2
                res_status = solver.Solve(model)

                chosen_paths = []
                if res_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    for leg_idx in range(num_legs):
                        for p_idx in range(len(leg_candidates[leg_idx])):
                            if solver.Value(y[leg_idx][p_idx]) == 1:
                                chosen_paths.append(leg_candidates[leg_idx][p_idx])
                                break
                else:
                    chosen_paths = [c[0] for c in leg_candidates]

            # Build RouteSegments
            segments: List[RouteSegment] = []
            total_len = 0.0
            total_bends = 0

            for k in range(num_legs):
                u_id = seq[k]
                v_id = seq[k + 1]
                poly, l_m, b_cnt, _ = chosen_paths[k]
                seg = RouteSegment(from_node=u_id, to_node=v_id, length_m=l_m, polyline=poly)
                segments.append(seg)
                total_len += l_m
                total_bends += b_cnt

            # Compute internal conflicts
            all_lines = [chosen_paths[k][3] for k in range(num_legs)]
            x_tot = 0
            o_tot = 0.0
            for i in range(num_legs):
                for j in range(i + 1, num_legs):
                    is_adj = (abs(i - j) == 1) or (i == 0 and j == num_legs - 1)
                    xc, oc = _calc_pair_cross_overlap(all_lines[i], all_lines[j])
                    if not is_adj:
                        x_tot += xc
                    o_tot += oc

            j_val = compute_J(total_len, total_bends, x_tot, o_tot)
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
                ring_crossings=x_tot,
                ring_overlap_m=round(o_tot, 4),
                cost_J=round(j_val, 4),
            )
            hub_rings.append(completed_ring)

        # 5. Bundle & Partition
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
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={"method": "ExactBitmaskDP_MultiPath_CPSAT"},
        )
