"""Obstacle-aware direction-state Dijkstra and A* micro-router.

Finds optimal axis-aligned paths inside floor plan free space, pricing 90-degree turns
at +0.5, 180-degree reversals at +0.5, and straight passes at 0.0 per Slide 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from resplan_router.contracts import (
    CompassDirection,
    PairRouteMetric,
    Point2D,
)
from resplan_router.phase3_micro_routing.rectilinear_grid import (
    GridEdge,
    HananRoutingGrid,
)


class NoPathFoundError(Exception):
    """Raised when no valid rectilinear path exists between specified endpoints."""


def compress_polyline(points: Sequence[Point2D]) -> List[Point2D]:
    """Compress polyline by merging consecutive collinear segments.

    Eliminates intermediate collinear vertices so every remaining interior
    vertex represents a true 90-degree turn or 180-degree reversal.

    Args:
        points: Ordered sequence of rectilinear vertices.

    Returns:
        List[Point2D]: Compressed sequence with at least start and end (if len >= 2).
    """
    if not points:
        return []
    # Deduplicate consecutive nearly identical points (< 0.1 mm)
    dedup: List[Point2D] = [points[0]]
    for p in points[1:]:
        if abs(p.x - dedup[-1].x) > 1e-4 or abs(p.y - dedup[-1].y) > 1e-4:
            dedup.append(p)
    if len(dedup) <= 2:
        return list(dedup)

    compressed: List[Point2D] = [dedup[0]]
    for i in range(1, len(dedup) - 1):
        prev = compressed[-1]
        curr = dedup[i]
        nxt = dedup[i + 1]

        # Check directions of (prev -> curr) and (curr -> nxt)
        dx1 = curr.x - prev.x
        dy1 = curr.y - prev.y
        dx2 = nxt.x - curr.x
        dy2 = nxt.y - curr.y

        try:
            d1 = CompassDirection.from_delta(dx1, dy1)
            d2 = CompassDirection.from_delta(dx2, dy2)
        except ValueError:
            # If zero displacement or degenerate, retain vertex
            compressed.append(curr)
            continue

        if d1 != d2:
            compressed.append(curr)

    compressed.append(dedup[-1])
    return compressed


def count_bends(points: Sequence[Point2D]) -> int:
    """Count the total number of 90-degree turns and 180-degree reversals along a polyline.

    Args:
        points: Ordered sequence of polyline vertices.

    Returns:
        int: Number of turns/reversals (equal to len(compressed) - 2 for len >= 2).
    """
    comp = compress_polyline(points)
    if len(comp) <= 2:
        return 0
    return len(comp) - 2


@dataclass
class SingleSourceResult:
    """Precomputed single-source shortest path tree from a given start vertex and departure direction.

    Uses an O(1) coordinate-indexed table:
    dist_by_node[(round(x, 4), round(y, 4))][d_in] = (cost, length, bends, parent_state, edge)
    """

    start_v: Point2D
    start_dir: Optional[CompassDirection]
    dist_by_node: Dict[
        Tuple[float, float],
        Dict[
            CompassDirection,
            Tuple[
                float,
                float,
                int,
                Tuple[Tuple[float, float], Optional[CompassDirection]],
                GridEdge,
            ],
        ],
    ] = field(default_factory=dict)

    def get_metric_to(
        self,
        end_v: Point2D,
        end_dir: Optional[CompassDirection] = None,
        start_id: str = "start",
        end_id: str = "end",
    ) -> Optional[PairRouteMetric]:
        """Extract optimal route metric to target end_v with optional arrival end_dir in O(1)."""
        end_k = (round(end_v.x, 4), round(end_v.y, 4))
        node_dict = self.dist_by_node.get(end_k)
        if not node_dict:
            return None

        if end_dir is not None:
            if end_dir not in node_dict:
                return None
            best_d_in = end_dir
            best_info = node_dict[end_dir]
        else:
            best_d_in, best_info = min(node_dict.items(), key=lambda item: item[1][0])

        best_cost, best_length, best_bends, _, _ = best_info

        # Reconstruct path vertices backwards
        path_vertices: List[Point2D] = [end_v]
        curr = (end_k, best_d_in)

        while True:
            curr_k, curr_d = curr
            curr_dict = self.dist_by_node.get(curr_k)
            if not curr_dict or curr_d not in curr_dict:
                break
            _, _, _, prev_state, edge = curr_dict[curr_d]
            path_vertices.append(edge.u)
            if prev_state[1] is None:
                # Reached start vertex
                break
            curr = (prev_state[0], prev_state[1])

        path_vertices.reverse()
        compressed = compress_polyline(path_vertices)

        actual_start_dir: Optional[CompassDirection] = None
        if len(compressed) >= 2:
            for k in range(1, len(compressed)):
                dx = compressed[k].x - compressed[0].x
                dy = compressed[k].y - compressed[0].y
                if abs(dx) >= 1e-4 or abs(dy) >= 1e-4:
                    try:
                        actual_start_dir = CompassDirection.from_delta(dx, dy)
                        break
                    except ValueError:
                        pass

        return PairRouteMetric(
            start_id=start_id,
            end_id=end_id,
            start_dir=actual_start_dir or self.start_dir,
            end_dir=best_d_in,
            length_m=round(best_length, 4),
            bends=best_bends,
            cost_J=round(best_cost, 4),
            polyline=compressed,
        )


class DirectionStateRouter:
    """Obstacle-aware rectilinear Dijkstra / A* pathfinder on Hanan routing grid.

    Searches direction-augmented state space (v, d_in) to find optimal
    axis-aligned paths strictly minimizing J = L + 0.5 * B + 1.0 * overlap + 5.0 * crossings.
    """

    def __init__(
        self,
        overlap_weight: float = 1.0,
        crossing_weight: float = 5.0,
        bend_weight: float = 0.5,
    ) -> None:
        self.overlap_weight = overlap_weight
        self.crossing_weight = crossing_weight
        self.bend_weight = bend_weight

    @staticmethod
    def evaluate_turn(
        d_in: Optional[CompassDirection],
        d_out: CompassDirection,
    ) -> Tuple[int, float]:
        """Evaluate bend count and cost penalty between arrival and departure directions.

        In accordance with Slide 7:
        - Passing straight through (d_in == d_out) incurs 0 bends (+0.0 cost).
        - A 90-degree turn (d_in != d_out, perpendicular) incurs 1 bend (+0.5 cost).
        - A 180-degree reversal (d_in != d_out, opposite) incurs 1 bend (+0.5 cost).
        - An unconstrained initial departure (d_in is None) incurs 0 bends (+0.0 cost).

        Args:
            d_in: Incoming compass direction (or None for start).
            d_out: Outgoing compass direction.

        Returns:
            Tuple[int, float]: (bends, penalty_cost)
        """
        if d_in is None:
            return 0, 0.0
        if d_in == d_out:
            return 0, 0.0
        return 1, 0.5

    def _is_edge_allowed(
        self,
        edge: GridEdge,
        grid: HananRoutingGrid,
        allowed_rooms: Optional[Set[str]],
    ) -> bool:
        """Check if an edge is traversable given room filtering constraints."""
        if allowed_rooms is None:
            return True

        if edge.portal_id is not None:
            portal_rooms = getattr(grid, "portal_rooms", None)
            if portal_rooms and edge.portal_id in portal_rooms:
                r1, r2 = portal_rooms[edge.portal_id]
                return (r1 in allowed_rooms) or (r2 in allowed_rooms)
            return True

        if edge.room_id is not None:
            return edge.room_id in allowed_rooms

        return False

    def compute_single_source(
        self,
        grid: HananRoutingGrid,
        start: Point2D,
        start_dir: Optional[CompassDirection] = None,
        allowed_rooms: Optional[Set[str]] = None,
        occupied_edges: Optional[Set[Tuple[Point2D, Point2D]]] = None,
        occupied_vertices: Optional[Set[Point2D]] = None,
    ) -> SingleSourceResult:
        """Run single-source Dijkstra on the direction-augmented state space (v, d_in).

        Computes shortest paths to ALL reachable vertices and arrival directions in one pass.
        Runs in ~3 ms using coordinate-tuple hashing.

        Args:
            grid: Hanan routing grid.
            start: Start coordinate (snapped to closest grid vertex).
            start_dir: Optional constraint forcing initial departure direction.
            allowed_rooms: Optional set of allowed room IDs.
            occupied_edges: Set of edge pairs subject to overlap penalty (+1.0/m).
            occupied_vertices: Set of vertices subject to crossing penalty (+5.0).

        Returns:
            SingleSourceResult: SSSP tree allowing instant O(1) query of any destination.
        """
        start_v = grid.snap_to_vertex(start, tolerance=0.005)
        start_k = (round(start_v.x, 4), round(start_v.y, 4))

        occ_edges = occupied_edges or set()
        occ_verts = occupied_vertices or set()

        result = SingleSourceResult(start_v=start_v, start_dir=start_dir)

        # Priority queue entries: (cost_g, step_idx, u_coords_tuple, d_in)
        heap: List[Tuple[float, int, Tuple[float, float], Optional[CompassDirection]]] = []
        step_idx = 0

        # Fast lookup mapping (x, y) -> Point2D
        coord_map = grid._coord_map

        # Initialize outgoing edges from start_v
        neighbors = grid.get_neighbors(start_v)
        for edge in neighbors:
            if start_dir is not None and edge.direction != start_dir:
                continue
            if not self._is_edge_allowed(edge, grid, allowed_rooms):
                continue

            d_out = edge.direction
            L = edge.length_m
            step_cost = L

            if (edge.u, edge.v) in occ_edges or (edge.v, edge.u) in occ_edges:
                step_cost += self.overlap_weight * L
            if edge.v in occ_verts:
                step_cost += self.crossing_weight

            v_k = (round(edge.v.x, 4), round(edge.v.y, 4))
            result.dist_by_node.setdefault(v_k, {})[d_out] = (
                step_cost,
                L,
                0,
                (start_k, None),
                edge,
            )

            heapq.heappush(heap, (step_cost, step_idx, v_k, d_out))
            step_idx += 1

        # Standard Dijkstra expansion
        while heap:
            curr_g, _, u_k, d_in = heapq.heappop(heap)

            node_dict = result.dist_by_node.get(u_k)
            if not node_dict or d_in not in node_dict:
                continue

            curr_info = node_dict[d_in]
            if curr_g > curr_info[0] + 1e-9:
                continue

            prev_L = curr_info[1]
            prev_bends = curr_info[2]

            u_pt = coord_map.get(u_k)
            if u_pt is None:
                continue

            for edge in grid.get_neighbors(u_pt):
                d_out = edge.direction

                if not self._is_edge_allowed(edge, grid, allowed_rooms):
                    continue

                bends_inc, turn_penalty = self.evaluate_turn(d_in, d_out)
                L = edge.length_m
                edge_cost = L + turn_penalty

                if (edge.u, edge.v) in occ_edges or (edge.v, edge.u) in occ_edges:
                    edge_cost += self.overlap_weight * L
                if edge.v in occ_verts:
                    edge_cost += self.crossing_weight

                new_g = curr_g + edge_cost
                v_k = (round(edge.v.x, 4), round(edge.v.y, 4))

                v_dict = result.dist_by_node.setdefault(v_k, {})
                if d_out not in v_dict or new_g < v_dict[d_out][0] - 1e-9:
                    v_dict[d_out] = (
                        new_g,
                        prev_L + L,
                        prev_bends + bends_inc,
                        (u_k, d_in),
                        edge,
                    )
                    heapq.heappush(heap, (new_g, step_idx, v_k, d_out))
                    step_idx += 1

        return result

    def find_shortest_path(
        self,
        grid: HananRoutingGrid,
        start: Point2D,
        end: Point2D,
        start_dir: Optional[CompassDirection] = None,
        end_dir: Optional[CompassDirection] = None,
        allowed_rooms: Optional[Set[str]] = None,
        occupied_edges: Optional[Set[Tuple[Point2D, Point2D]]] = None,
        occupied_vertices: Optional[Set[Point2D]] = None,
        start_id: str = "start",
        end_id: str = "end",
    ) -> PairRouteMetric:
        """Find optimal axis-aligned route between start and end with Slide 7 turn accounting.

        Args:
            grid: Hanan routing grid.
            start: Start coordinate.
            end: Target coordinate.
            start_dir: Optional departure direction from start terminal.
            end_dir: Optional arrival direction at end terminal.
            allowed_rooms: Optional set of allowed room IDs.
            occupied_edges: Set of edges subject to overlap penalty (+1.0/m).
            occupied_vertices: Set of vertices subject to crossing penalty (+5.0).
            start_id: Identifier for start terminal.
            end_id: Identifier for end terminal.

        Returns:
            PairRouteMetric: Exact route metric with compressed polyline.

        Raises:
            NoPathFoundError: If no valid path exists satisfying constraints.
        """
        start_v = grid.snap_to_vertex(start, tolerance=0.005)
        end_v = grid.snap_to_vertex(end, tolerance=0.005)

        # Handle zero-length trivial path
        if start_v == end_v:
            if start_dir is None or end_dir is None or start_dir == end_dir:
                return PairRouteMetric(
                    start_id=start_id,
                    end_id=end_id,
                    start_dir=start_dir,
                    end_dir=end_dir,
                    length_m=0.0,
                    bends=0,
                    cost_J=0.0,
                    polyline=[start_v],
                )

        sssp = self.compute_single_source(
            grid=grid,
            start=start_v,
            start_dir=start_dir,
            allowed_rooms=allowed_rooms,
            occupied_edges=occupied_edges,
            occupied_vertices=occupied_vertices,
        )

        metric = sssp.get_metric_to(
            end_v=end_v,
            end_dir=end_dir,
            start_id=start_id,
            end_id=end_id,
        )

        if metric is None:
            raise NoPathFoundError(
                f"No rectilinear path found from {start.coords} to {end.coords} "
                f"(start_dir={start_dir}, end_dir={end_dir})"
            )

        return metric
