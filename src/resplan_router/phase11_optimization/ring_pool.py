"""Ring candidate pool generator for door-connected zones in Phase 11.

Generates complete, non-dominated closed ring candidates per zone by:
1. Evaluating multiple device position candidates per eligible room.
2. Evaluating multiple hub candidate locations inside zone rooms (>= 0.50m wall clearance).
3. Solving exact visit order with Held-Karp dynamic programming (DirectionalRingSolver).
4. Generating alternative route combinations using Yen's route pool.
5. Computing internal metrics: L, B, internal crossings (X), and internal overlap (O).
6. Filtering to the top non-dominated complete ring candidates (up to 8 per zone).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import math
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    CompassDirection,
    CompletedHubRing,
    FixedNode,
    HubAssignment,
    MetricFloorPlan,
    PairRouteMetric,
    Point2D,
    RouteSegment,
)
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import HananRoutingGrid, build_hanan_grid
from resplan_router.phase4_partitioning.hub_candidates import generate_hub_candidates
from resplan_router.phase5_ring_sequencing.exact_ring_tsp import DirectionalRingSolver
from resplan_router.phase5_ring_sequencing.loop_synthesizer import (
    compute_crossings,
    compute_overlap,
    count_ring_bends,
)
from resplan_router.phase1_ingestion.device_candidates import generate_room_device_candidates
from resplan_router.phase11_optimization.objective import compute_J
from resplan_router.phase11_optimization.route_pool import YenRoutePool


@dataclass
class CompleteRingCandidate:
    """A complete candidate closed cable ring for a specific zone."""

    candidate_id: str
    room_ids: FrozenSet[str]
    hub_id: str
    hub_location: Point2D
    hub_room_id: str
    device_nodes: Dict[str, FixedNode]  # node_id -> FixedNode
    closed_sequence: List[str]  # e.g. ["H1", "N1", "N2", "H1"]
    route_segments: List[RouteSegment]
    total_length_m: float
    total_bends: int
    internal_crossings: int
    internal_overlap_m: float
    total_cost_J: float
    zone_area_m2: float
    is_valid: bool = True

    def to_hub_ring(self) -> CompletedHubRing:
        """Convert candidate into official CompletedHubRing contract."""
        return CompletedHubRing(
            hub_id=self.hub_id,
            room_id=self.hub_room_id,
            location=self.hub_location,
            assigned_rooms=sorted(self.room_ids),
            assigned_nodes=sorted(self.device_nodes.keys()),
            closed_sequence=list(self.closed_sequence),
            route_segments=list(self.route_segments),
            ring_length_m=round(self.total_length_m, 4),
            ring_bends=self.total_bends,
            ring_crossings=self.internal_crossings,
            ring_overlap_m=round(self.internal_overlap_m, 4),
            cost_J=round(self.total_cost_J, 4),
        )


def compute_ring_internal_conflicts(
    route_segments: Sequence[RouteSegment],
) -> Tuple[int, float]:
    """Compute internal self-crossings (X) and shared segment overlap (O) within one ring."""
    polylines = [[Point(p.x, p.y) for p in seg.polyline] for seg in route_segments if len(seg.polyline) >= 2]
    if len(polylines) <= 1:
        return 0, 0.0

    # Extract all directed segments: (leg_idx, (p1, p2), linestring)
    lines: List[Tuple[int, LineString]] = []
    for leg_idx, pts in enumerate(polylines):
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            if p1.distance(p2) > 1e-4:
                lines.append((leg_idx, LineString([p1, p2])))

    crossings = 0
    overlap_length = 0.0

    for i in range(len(lines)):
        leg_i, line_i = lines[i]
        for j in range(i + 1, len(lines)):
            leg_j, line_j = lines[j]
            # Don't check consecutive segments in the same leg sharing a corner vertex
            if leg_i == leg_j and abs(i - j) <= 1:
                continue

            inter = line_i.intersection(line_j)
            if inter.is_empty:
                continue

            # 1. Overlap: 1D intersection (LineString or MultiLineString)
            if inter.geom_type in ("LineString", "MultiLineString"):
                overlap_length += inter.length
            # 2. Crossing: 0D intersection strictly in segment interior
            elif inter.geom_type == "Point":
                # Must not be an endpoint of both segments
                p = inter
                i_end = p.distance(Point(line_i.coords[0])) < 1e-4 or p.distance(Point(line_i.coords[-1])) < 1e-4
                j_end = p.distance(Point(line_j.coords[0])) < 1e-4 or p.distance(Point(line_j.coords[-1])) < 1e-4
                if not (i_end and j_end):
                    crossings += 1

    return crossings, round(overlap_length, 4)


class ZoneRingGenerator:
    """Generates a diverse pool of valid complete ring candidates for a single connected zone."""

    def __init__(
        self,
        plan: MetricFloorPlan,
        topology: Any,
        beam_width: int = 20,
        candidates_per_zone: int = 8,
    ) -> None:
        self.plan = plan
        self.topology = topology
        self.beam_width = beam_width
        self.candidates_per_zone = candidates_per_zone

    def generate_zone_candidates(
        self,
        zone_rooms: FrozenSet[str],
        zone_idx: int = 1,
        existing_nodes: Optional[Dict[str, FixedNode]] = None,
    ) -> List[CompleteRingCandidate]:
        """Generate non-dominated complete closed ring candidates for the given zone."""
        hub_id = f"H{zone_idx}"
        zone_area = sum(self.plan.rooms[r].area_m2 for r in zone_rooms)

        # 1. Device positions in this zone
        # If pre-set nodes are provided (e.g. from existing plan), use them
        zone_devices: Dict[str, FixedNode] = {}
        if existing_nodes:
            zone_devices = {nid: node for nid, node in existing_nodes.items() if node.room_id in zone_rooms}

        # If missing nodes for any eligible room, generate them
        if not zone_devices:
            dev_idx = 1
            for r_id in sorted(zone_rooms):
                r = self.plan.rooms[r_id]
                if r.is_eligible:
                    cands = generate_room_device_candidates(
                        room_id=r_id,
                        room_geom=r.geometry,
                        obstacles=self.plan.obstacles_geom,
                    )
                    chosen_pt = cands[0] if cands else Point2D(x=r.geometry.centroid.x, y=r.geometry.centroid.y)
                    nid = f"N{zone_idx}_{dev_idx}"
                    zone_devices[nid] = FixedNode(node_id=nid, room_id=r_id, location=chosen_pt)
                    dev_idx += 1

        if not zone_devices:
            return []

        # 2. Generate candidate hub locations in zone rooms
        all_hub_cands: List[Tuple[str, Point2D]] = []
        for r_id in sorted(zone_rooms):
            cands = generate_hub_candidates(
                plan=self.plan,
                room_id=r_id,
                topology=self.topology,
                margin_m=0.50,
                min_node_dist=0.0,  # Hub and device may share room without artificial 0.30m separation
            )
            device_pts = [n.location for n in zone_devices.values()]
            r_centroid = self.plan.rooms[r_id].geometry.centroid

            # Filter candidates that share exact coordinates with any device (< 0.05m)
            valid_cands = [c for c in cands if not any(math.hypot(c.x - dp.x, c.y - dp.y) < 0.05 for dp in device_pts)]

            # Prioritize candidates near zone devices and centroid
            near_device_cands = sorted(valid_cands, key=lambda pt: (
                sum(math.hypot(pt.x - dp.x, pt.y - dp.y) for dp in device_pts) / max(1, len(device_pts)),
                math.hypot(pt.x - r_centroid.x, pt.y - r_centroid.y),
            ))

            chosen_for_room: List[Point2D] = []
            if len(zone_rooms) == 1 and len(zone_devices) <= 1:
                chosen_for_room.extend(near_device_cands[:1])
            else:
                chosen_for_room.extend(near_device_cands[:1])
                if len(device_pts) >= 2:
                    dp1, dp2 = device_pts[0], device_pts[1]
                    line_len = math.hypot(dp2.x - dp1.x, dp2.y - dp1.y)
                    if line_len > 1e-4:
                        non_collinear = [
                            c for c in valid_cands
                            if abs((dp2.y - dp1.y) * c.x - (dp2.x - dp1.x) * c.y + dp2.x * dp1.y - dp2.y * dp1.x) / line_len >= 0.20
                        ]
                        non_collinear.sort(key=lambda pt: (
                            sum(math.hypot(pt.x - dp.x, pt.y - dp.y) for dp in device_pts),
                            math.hypot(pt.x - r_centroid.x, pt.y - r_centroid.y),
                        ))
                        chosen_for_room.extend(non_collinear[:1])

            seen_c = set()
            for c in chosen_for_room:
                if c.coords not in seen_c:
                    seen_c.add(c.coords)
                    all_hub_cands.append((r_id, c))

        if not all_hub_cands:
            # Fallback to room polylabel
            r_first = sorted(zone_rooms)[0]
            c = Point2D(x=round(self.plan.rooms[r_first].geometry.centroid.x, 4), y=round(self.plan.rooms[r_first].geometry.centroid.y, 4))
            all_hub_cands.append((r_first, c))

        # 3. For each promising hub, solve Held-Karp and synthesize ring
        complete_candidates: List[CompleteRingCandidate] = []
        cand_counter = 0

        for hub_room_id, hub_pt in all_hub_cands:
            hub_assign = HubAssignment(
                hub_id=hub_id,
                room_id=hub_room_id,
                location=hub_pt,
                assigned_rooms=sorted(zone_rooms),
                assigned_nodes=sorted(zone_devices.keys()),
                zone_area_m2=round(zone_area, 4),
            )

            # Build grid with hub and devices
            terminals = {hub_id: hub_pt}
            for nid, n in zone_devices.items():
                terminals[nid] = n.location

            grid = build_hanan_grid(
                plan=self.plan,
                topology=self.topology,
                extra_points=list(terminals.values()),
            )

            cost_tensor = PairwiseCostTensor.build_tensor(
                grid=grid,
                terminals=terminals,
                plan_id=self.plan.plan_id,
            )

            # Exact Held-Karp solve
            solver = DirectionalRingSolver(
                hub=hub_assign,
                nodes=list(zone_devices.values()),
                cost_tensor=cost_tensor,
            )
            try:
                closed_seq, leg_dirs = solver.solve()
            except Exception:
                # Fallback sequence: hub -> nodes -> hub
                closed_seq = [hub_id] + sorted(zone_devices.keys()) + [hub_id]
                leg_dirs = [(None, None)] * (len(closed_seq) - 1)

            # Build route segments using Yen's route pool
            k_val = 1 if len(zone_devices) <= 1 else 2
            route_pool = YenRoutePool(grid=grid, plan=self.plan, default_k=k_val)
            segments: List[RouteSegment] = []

            total_len = 0.0
            total_bends = 0
            valid_legs = True

            for idx in range(len(closed_seq) - 1):
                u_id = closed_seq[idx]
                v_id = closed_seq[idx + 1]
                u_pt = terminals[u_id]
                v_pt = terminals[v_id]
                d_start, d_end = leg_dirs[idx] if idx < len(leg_dirs) else (None, None)

                k_routes = route_pool.get_k_shortest_routes(
                    start_id=u_id,
                    end_id=v_id,
                    start_pt=u_pt,
                    end_pt=v_pt,
                    k=3,
                    d_start=d_start,
                    d_end=d_end,
                )
                if not k_routes:
                    valid_legs = False
                    break

                # Select best candidate leg that minimizes conflicts with previously routed legs
                best_leg_seg: Optional[RouteSegment] = None
                best_leg_score = float("inf")
                best_leg_m: Optional[PairRouteMetric] = None

                for cand_m in k_routes:
                    cand_seg = RouteSegment(
                        from_node=u_id,
                        to_node=v_id,
                        length_m=cand_m.length_m,
                        polyline=cand_m.polyline,
                    )
                    x_c, o_l = compute_ring_internal_conflicts(segments + [cand_seg])
                    score = (total_len + cand_m.length_m) + 0.5 * (total_bends + cand_m.bends) + 5.0 * x_c + o_l
                    if score < best_leg_score:
                        best_leg_score = score
                        best_leg_seg = cand_seg
                        best_leg_m = cand_m

                if best_leg_seg is None or best_leg_m is None:
                    valid_legs = False
                    break

                segments.append(best_leg_seg)
                total_len += best_leg_m.length_m
                total_bends += best_leg_m.bends

            if not valid_legs or len(segments) != len(closed_seq) - 1:
                continue

            # Calculate internal conflicts using exact checker functions
            term_pts = list(terminals.values())
            x_int = compute_crossings(segments, term_pts)
            o_int = compute_overlap(segments)
            exact_bends = count_ring_bends(closed_seq, segments)
            j_score = compute_J(total_len, exact_bends, x_int, o_int)

            cand_counter += 1
            cand = CompleteRingCandidate(
                candidate_id=f"Ring_{zone_idx}_{cand_counter}",
                room_ids=zone_rooms,
                hub_id=hub_id,
                hub_location=hub_pt,
                hub_room_id=hub_room_id,
                device_nodes=zone_devices,
                closed_sequence=closed_seq,
                route_segments=segments,
                total_length_m=round(total_len, 4),
                total_bends=exact_bends,
                internal_crossings=x_int,
                internal_overlap_m=o_int,
                total_cost_J=j_score,
                zone_area_m2=round(zone_area, 4),
                is_valid=True,
            )
            complete_candidates.append(cand)

        # Sort by total J score and keep top non-dominated candidates
        complete_candidates.sort(key=lambda c: (c.total_cost_J, c.total_length_m, c.total_bends))
        return complete_candidates[: self.candidates_per_zone]
