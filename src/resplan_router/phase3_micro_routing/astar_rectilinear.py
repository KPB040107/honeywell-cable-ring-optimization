"""Obstacle-aware direction-state Dijkstra and A* micro-router.

Finds optimal axis-aligned paths inside floor plan free space, pricing 90-degree turns
at +0.5, 180-degree reversals at +0.5, and straight passes at 0.0 per Slide 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

import shapely
from shapely.geometry import LineString, Point

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
    if len(points) <= 2:
        return list(points)

    compressed: List[Point2D] = [points[0]]
    for i in range(1, len(points) - 1):
        prev = compressed[-1]
        curr = points[i]
        nxt = points[i + 1]

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

    compressed.append(points[-1])
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
    """Precomputed single-source shortest path tree from a given start vertex and departure direction."""

    start_v: Point2D
    start_dir: Optional[CompassDirection]
    dist: Dict[Tuple[Point2D, CompassDirection], float] = field(default_factory=dict)
    dist_details: Dict[Tuple[Point2D, CompassDirection], Tuple[float, int]] = field(
        default_factory=dict
    )
    parent: Dict[
        Tuple[Point2D, CompassDirection],
        Tuple[Tuple[Point2D, Optional[CompassDirection]], GridEdge],
    ] = field(default_factory=dict)

    def get_metric_to(
        self,
        end_v: Point2D,
        end_dir: Optional[CompassDirection] = None,
        start_id: str = "start",
        end_id: str = "end",
    ) -> Optional[PairRouteMetric]:
        """Extract optimal route metric to target end_v with optional arrival end_dir."""
        # Find reachable states matching end_v and end_dir
        matching_states = [
            (state, self.dist[state])
            for state in self.dist
            if state[0] == end_v and (end_dir is None or state[1] == end_dir)
        ]

        if not matching_states:
            return None

        # Select minimum cost state
        best_state, best_cost = min(matching_states, key=lambda s: s[1])
        actual_end_dir = best_state[1]
        best_length, best_bends = self.dist_details[best_state]

        # Reconstruct path vertices backwards
        path_vertices: List[Point2D] = [end_v]
        curr_state = best_state

        while curr_state in self.parent:
            prev_state, edge = self.parent[curr_state]
            path_vertices.append(edge.u)
            if prev_state[1] is None:
                # Reached start
                break
            curr_state = (prev_state[0], prev_state[1])

        path_vertices.reverse()
        compressed = compress_polyline(path_vertices)

        actual_start_dir: Optional[CompassDirection] = None
        if len(compressed) >= 2:
            actual_start_dir = CompassDirection.from_delta(
                compressed[1].x - compressed[0].x,
                compressed[1].y - compressed[0].y,
            )

        return PairRouteMetric(
            start_id=start_id,
            end_id=end_id,
            start_dir=actual_start_dir or self.start_dir,
            end_dir=actual_end_dir,
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

        # If edge belongs to a portal connecting rooms
        if edge.portal_id is not None:
            # If portal_rooms is tracked on grid, check connection
            portal_rooms = getattr(grid, "portal_rooms", None)
            if portal_rooms and edge.portal_id in portal_rooms:
                r1, r2 = portal_rooms[edge.portal_id]
                return (r1 in allowed_rooms) or (r2 in allowed_rooms)
            return True

        # If edge belongs to a room
        if edge.room_id is not None:
            return edge.room_id in allowed_rooms

        # Unassigned edges allowed only if no room filtering
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

        occ_edges = occupied_edges or set()
        occ_verts = occupied_vertices or set()

        result = SingleSourceResult(start_v=start_v, start_dir=start_dir)

        # Priority queue entries: (cost_g, step_idx, current_v, d_in)
        heap: List[Tuple[float, int, Point2D, Optional[CompassDirection]]] = []
        step_idx = 0

        # Initialize from start_v
        neighbors = grid.get_neighbors(start_v)
        for edge in neighbors:
            if start_dir is not None and edge.direction != start_dir:
                continue
            if not self._is_edge_allowed(edge, grid, allowed_rooms):
                continue

            d_out = edge.direction
            L = edge.length_m

            # Step penalties
            step_cost = L
            if (edge.u, edge.v) in occ_edges or (edge.v, edge.u) in occ_edges:
                step_cost += self.overlap_weight * L
            if edge.v in occ_verts:
                step_cost += self.crossing_weight

            state_1 = (edge.v, d_out)
            result.dist[state_1] = step_cost
            result.dist_details[state_1] = (L, 0)
            result.parent[state_1] = ((start_v, None), edge)

            heapq.heappush(heap, (step_cost, step_idx, edge.v, d_out))
            step_idx += 1

        # Standard Dijkstra expansion
        while heap:
            curr_g, _, u, d_in = heapq.heappop(heap)

            state_u = (u, d_in)
            if curr_g > result.dist.get(state_u, float("inf")) + 1e-9:
                continue

            prev_L, prev_bends = result.dist_details[state_u]

            for edge in grid.get_neighbors(u):
                v = edge.v
                d_out = edge.direction

                if not self._is_edge_allowed(edge, grid, allowed_rooms):
                    continue

                bends_inc, turn_penalty = self.evaluate_turn(d_in, d_out)
                L = edge.length_m

                edge_cost = L + turn_penalty
                if (edge.u, edge.v) in occ_edges or (edge.v, edge.u) in occ_edges:
                    edge_cost += self.overlap_weight * L
                if v in occ_verts:
                    edge_cost += self.crossing_weight

                new_g = curr_g + edge_cost
                next_state = (v, d_out)

                if next_state not in result.dist or new_g < result.dist[next_state] - 1e-9:
                    result.dist[next_state] = new_g
                    result.dist_details[next_state] = (prev_L + L, prev_bends + bends_inc)
                    result.parent[next_state] = (state_u, edge)
                    heapq.heappush(heap, (new_g, step_idx, v, d_out))
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

        Uses A* state-space search with admissible Manhattan distance heuristic.

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

        occ_edges = occupied_edges or set()
        occ_verts = occupied_vertices or set()

        def heuristic(p: Point2D) -> float:
            # Strictly admissible L1 Manhattan distance
            return abs(p.x - end_v.x) + abs(p.y - end_v.y)

        # A* priority queue entries: (f_score, g_cost, step_idx, u, d_in)
        heap: List[Tuple[float, float, int, Point2D, Optional[CompassDirection]]] = []
        step_idx = 0

        dist: Dict[Tuple[Point2D, CompassDirection], float] = {}
        dist_details: Dict[Tuple[Point2D, CompassDirection], Tuple[float, int]] = {}
        parent: Dict[
            Tuple[Point2D, CompassDirection],
            Tuple[Tuple[Point2D, Optional[CompassDirection]], GridEdge],
        ] = {}

        # Initialize from start_v
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
            if edge.v in occ_verts and edge.v != end_v:
                step_cost += self.crossing_weight

            state_1 = (edge.v, d_out)
            f_score = step_cost + heuristic(edge.v)
            dist[state_1] = step_cost
            dist_details[state_1] = (L, 0)
            parent[state_1] = ((start_v, None), edge)

            heapq.heappush(heap, (f_score, step_cost, step_idx, edge.v, d_out))
            step_idx += 1

        goal_state: Optional[Tuple[Point2D, CompassDirection]] = None

        while heap:
            f_score, curr_g, _, u, d_in = heapq.heappop(heap)

            state_u = (u, d_in)
            if curr_g > dist.get(state_u, float("inf")) + 1e-9:
                continue

            # Check if reached target end_v
            if u == end_v:
                if end_dir is None or d_in == end_dir:
                    goal_state = state_u
                    break

            prev_L, prev_bends = dist_details[state_u]

            for edge in grid.get_neighbors(u):
                v = edge.v
                d_out = edge.direction

                if not self._is_edge_allowed(edge, grid, allowed_rooms):
                    continue

                bends_inc, turn_penalty = self.evaluate_turn(d_in, d_out)
                L = edge.length_m

                edge_cost = L + turn_penalty
                if (edge.u, edge.v) in occ_edges or (edge.v, edge.u) in occ_edges:
                    edge_cost += self.overlap_weight * L
                if v in occ_verts and v != end_v:
                    edge_cost += self.crossing_weight

                new_g = curr_g + edge_cost
                next_state = (v, d_out)

                if next_state not in dist or new_g < dist[next_state] - 1e-9:
                    dist[next_state] = new_g
                    dist_details[next_state] = (prev_L + L, prev_bends + bends_inc)
                    parent[next_state] = (state_u, edge)
                    f_val = new_g + heuristic(v)
                    heapq.heappush(heap, (f_val, new_g, step_idx, v, d_out))
                    step_idx += 1

        if goal_state is None:
            raise NoPathFoundError(
                f"No rectilinear path found from {start.coords} to {end.coords} "
                f"(start_dir={start_dir}, end_dir={end_dir})"
            )

        # Reconstruct path
        best_cost = dist[goal_state]
        best_length, best_bends = dist_details[goal_state]
        actual_end_dir = goal_state[1]

        path_vertices: List[Point2D] = [end_v]
        curr = goal_state
        while curr in parent:
            prev_st, edge = parent[curr]
            path_vertices.append(edge.u)
            if prev_st[1] is None:
                break
            curr = (prev_st[0], prev_st[1])

        path_vertices.reverse()
        compressed = compress_polyline(path_vertices)

        actual_start_dir: Optional[CompassDirection] = None
        if len(compressed) >= 2:
            actual_start_dir = CompassDirection.from_delta(
                compressed[1].x - compressed[0].x,
                compressed[1].y - compressed[0].y,
            )

        return PairRouteMetric(
            start_id=start_id,
            end_id=end_id,
            start_dir=actual_start_dir or start_dir,
            end_dir=actual_end_dir,
            length_m=round(best_length, 4),
            bends=best_bends,
            cost_J=round(best_cost, 4),
            polyline=compressed,
        )
