"""Pairwise directional cost tensor and routing cache for axis-aligned micro-routes.

Precomputes and caches minimum-cost rectilinear paths across all terminal pairs
and entry/exit compass directions (N, S, E, W)^2 in < 400 ms.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

from resplan_router.contracts import (
    CompassDirection,
    MetricFloorPlan,
    PairRouteMetric,
    Point2D,
    PortalTopologyGraph,
    RectilinearRoutingIndex,
)
from resplan_router.phase3_micro_routing.astar_rectilinear import (
    DirectionStateRouter,
    NoPathFoundError,
)
from resplan_router.phase3_micro_routing.rectilinear_grid import (
    HananRoutingGrid,
    build_hanan_grid,
)


class PairwiseCostTensor:
    """Precomputed all-pairs directional routing tensor between terminals.

    Evaluates and caches optimal axis-aligned routes between:
    - Fixed nodes (N1, N2, ...)
    - Portal waypoints (threshold midpoint, Lane A, Lane B, step points)
    - Candidate hub sites
    across all combinations of departure and arrival compass directions.
    """

    ALL_DIRECTIONS: Tuple[CompassDirection, ...] = (
        CompassDirection.NORTH,
        CompassDirection.SOUTH,
        CompassDirection.EAST,
        CompassDirection.WEST,
    )

    def __init__(
        self,
        plan_id: str,
        grid: HananRoutingGrid,
        terminals: Dict[str, Point2D],
        index: RectilinearRoutingIndex,
        router: Optional[DirectionStateRouter] = None,
        computation_time_ms: float = 0.0,
    ) -> None:
        self.plan_id = plan_id
        self.grid = grid
        self.terminals = dict(terminals)
        self.index = index
        self.router = router or DirectionStateRouter()
        self.computation_time_ms = computation_time_ms

    @property
    def pair_count(self) -> int:
        """Return total number of cached directional route metrics."""
        return len(self.index.pair_metrics)

    def get_metric(
        self,
        u_id: str,
        v_id: str,
        d_start: Optional[CompassDirection] = None,
        d_end: Optional[CompassDirection] = None,
    ) -> Optional[PairRouteMetric]:
        """Look up precomputed route metric, falling back to on-demand calculation if missing.

        Args:
            u_id: Start terminal ID.
            v_id: End terminal ID.
            d_start: Optional departure direction from u_id.
            d_end: Optional arrival direction at v_id.

        Returns:
            Optional[PairRouteMetric]: Optimal route metric, or None if unreachable.
        """
        # 1. Check precomputed cache via RectilinearRoutingIndex
        cached = self.index.get_metric(u_id, v_id, d_start, d_end)
        if cached is not None:
            return cached

        # 2. On-demand fallback calculation if terminals exist
        if u_id not in self.terminals or v_id not in self.terminals:
            return None

        u_pt = self.terminals[u_id]
        v_pt = self.terminals[v_id]

        try:
            metric = self.router.find_shortest_path(
                grid=self.grid,
                start=u_pt,
                end=v_pt,
                start_dir=d_start,
                end_dir=d_end,
                start_id=u_id,
                end_id=v_id,
            )
            # Cache computed metric
            key = f"{u_id}_{v_id}_{d_start.value if d_start else '*'}_{d_end.value if d_end else '*'}"
            self.index.pair_metrics[key] = metric
            return metric
        except NoPathFoundError:
            return None

    @classmethod
    def build_tensor(
        cls,
        grid: HananRoutingGrid,
        terminals: Dict[str, Point2D],
        plan_id: str = "plan",
        directions: Optional[Sequence[CompassDirection]] = None,
        allowed_rooms: Optional[Set[str]] = None,
        router: Optional[DirectionStateRouter] = None,
    ) -> "PairwiseCostTensor":
        """Precompute all-pairs shortest paths across all terminals and cardinal directions.

        Uses single-source Dijkstra passes (one per source per departure direction)
        to populate the full directional tensor in < 400 ms.

        Args:
            grid: Hanan routing grid.
            terminals: Dict mapping terminal ID to Point2D coordinates.
            plan_id: Floor plan identifier.
            directions: Directions to evaluate (default: N, S, E, W).
            allowed_rooms: Optional set of allowed room IDs.
            router: Optional DirectionStateRouter instance.

        Returns:
            PairwiseCostTensor: Fully precomputed and indexed cost tensor.
        """
        start_time = time.perf_counter()
        r = router or DirectionStateRouter()
        dirs = list(directions or cls.ALL_DIRECTIONS)

        pair_metrics: Dict[str, PairRouteMetric] = {}

        # For every source terminal
        for u_id, u_pt in terminals.items():
            u_vertex = grid.snap_to_vertex(u_pt, tolerance=0.005)

            # 1. Unconstrained departure Dijkstra pass
            unconstrained_tree = r.compute_single_source(
                grid=grid,
                start=u_vertex,
                start_dir=None,
                allowed_rooms=allowed_rooms,
            )

            for v_id, v_pt in terminals.items():
                if u_id == v_id:
                    # Identity zero metric
                    zero_metric = PairRouteMetric(
                        start_id=u_id,
                        end_id=v_id,
                        start_dir=None,
                        end_dir=None,
                        length_m=0.0,
                        bends=0,
                        cost_J=0.0,
                        polyline=[u_vertex],
                    )
                    pair_metrics[f"{u_id}_{v_id}"] = zero_metric
                    pair_metrics[f"{u_id}_{v_id}_*_*"] = zero_metric
                    continue

                v_vertex = grid.snap_to_vertex(v_pt, tolerance=0.005)
                m_unconstrained = unconstrained_tree.get_metric_to(
                    end_v=v_vertex,
                    end_dir=None,
                    start_id=u_id,
                    end_id=v_id,
                )
                if m_unconstrained is not None:
                    pair_metrics[f"{u_id}_{v_id}"] = m_unconstrained
                    pair_metrics[f"{u_id}_{v_id}_*_*"] = m_unconstrained

            # 2. Directional departure passes for each d_start in dirs
            for d_start in dirs:
                dir_tree = r.compute_single_source(
                    grid=grid,
                    start=u_vertex,
                    start_dir=d_start,
                    allowed_rooms=allowed_rooms,
                )

                for v_id, v_pt in terminals.items():
                    if u_id == v_id:
                        continue

                    v_vertex = grid.snap_to_vertex(v_pt, tolerance=0.005)

                    # Directional departure with unconstrained arrival
                    m_dstart_any = dir_tree.get_metric_to(
                        end_v=v_vertex,
                        end_dir=None,
                        start_id=u_id,
                        end_id=v_id,
                    )
                    if m_dstart_any is not None:
                        pair_metrics[f"{u_id}_{v_id}_{d_start.value}_*"] = m_dstart_any

                    # Exact directional arrival (d_start -> d_end)
                    for d_end in dirs:
                        m_exact = dir_tree.get_metric_to(
                            end_v=v_vertex,
                            end_dir=d_end,
                            start_id=u_id,
                            end_id=v_id,
                        )
                        if m_exact is not None:
                            pair_metrics[
                                f"{u_id}_{v_id}_{d_start.value}_{d_end.value}"
                            ] = m_exact

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        index = RectilinearRoutingIndex(
            plan_id=plan_id,
            grid_node_count=grid.vertex_count,
            grid_edge_count=grid.edge_count,
            pair_metrics=pair_metrics,
        )

        return cls(
            plan_id=plan_id,
            grid=grid,
            terminals=terminals,
            index=index,
            router=r,
            computation_time_ms=round(elapsed_ms, 2),
        )

    @classmethod
    def build_from_plan(
        cls,
        plan: MetricFloorPlan,
        topology: PortalTopologyGraph,
        grid: Optional[HananRoutingGrid] = None,
        candidate_hubs: Optional[Dict[str, Point2D]] = None,
        include_portals: bool = True,
    ) -> "PairwiseCostTensor":
        """Build full directional cost tensor from ingested floor plan and topology.

        Args:
            plan: Normalized MetricFloorPlan.
            topology: Macro PortalTopologyGraph.
            grid: Optional prebuilt HananRoutingGrid.
            candidate_hubs: Optional dict of candidate hub coordinates.
            include_portals: Whether to index portal waypoints as terminals.

        Returns:
            PairwiseCostTensor: Precomputed directional tensor.
        """
        routing_grid = grid or build_hanan_grid(plan, topology)

        terminals: Dict[str, Point2D] = {}

        # 1. Fixed interior nodes
        for node_id, node in plan.nodes.items():
            terminals[node_id] = node.location

        # 2. Portal waypoints
        if include_portals:
            for p in topology.portals:
                terminals[f"{p.portal_id}_center"] = p.center_point
                terminals[f"{p.portal_id}_lane_a"] = p.lane_a_point
                terminals[f"{p.portal_id}_lane_b"] = p.lane_b_point

        # 3. Candidate hubs
        if candidate_hubs:
            for hub_id, hub_pt in candidate_hubs.items():
                terminals[hub_id] = hub_pt

        return cls.build_tensor(
            grid=routing_grid,
            terminals=terminals,
            plan_id=plan.plan_id,
        )
