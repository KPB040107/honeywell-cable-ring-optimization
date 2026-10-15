"""Direction-aware Held-Karp dynamic programming closed-ring TSP solver.

Determines the optimal visiting order H_k -> N_{sigma(1)} -> ... -> N_{sigma(m)} -> H_k
for each hub (m <= 5), explicitly pricing 90-degree turns and 180-degree reversals
at nodes and the hub wrap-around connection in accordance with Slide 7 rules.
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Sequence, Tuple

from resplan_router.contracts import (
    CompassDirection,
    FixedNode,
    HubAssignment,
    PairRouteMetric,
)
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor


class DirectionalRingSolver:
    """Direction-aware Held-Karp exact ring TSP solver for hub clusters (m <= 5).

    Optimizes the closed ring cost J = L + 0.5 * B over the state space
    (S, u, d_in, d_hub_start) where:
    - S: bitmask of visited nodes
    - u: current node index
    - d_in: incoming arrival direction at node u
    - d_hub_start: initial departure direction from hub H_k
    """

    ALL_DIRECTIONS: Tuple[CompassDirection, ...] = (
        CompassDirection.NORTH,
        CompassDirection.SOUTH,
        CompassDirection.EAST,
        CompassDirection.WEST,
    )

    def __init__(
        self,
        hub: HubAssignment,
        nodes: Sequence[FixedNode],
        cost_tensor: PairwiseCostTensor,
    ) -> None:
        self.hub = hub
        self.nodes = list(nodes)
        self.cost_tensor = cost_tensor

        # Ensure hub and nodes are registered in cost_tensor terminals
        if self.hub.hub_id not in self.cost_tensor.terminals:
            self.cost_tensor.terminals[self.hub.hub_id] = self.hub.location
        for n in self.nodes:
            if n.node_id not in self.cost_tensor.terminals:
                self.cost_tensor.terminals[n.node_id] = n.location

    def _get_metric(
        self,
        u_id: str,
        v_id: str,
        d_start: Optional[CompassDirection] = None,
        d_end: Optional[CompassDirection] = None,
    ) -> Optional[PairRouteMetric]:
        """Look up or compute directional route metric between terminals u_id and v_id."""
        if d_start is not None and d_end is not None:
            key = f"{u_id}_{v_id}_{d_start.value}_{d_end.value}"
            cached = self.cost_tensor.index.pair_metrics.get(key)
            if cached is not None and cached.start_dir == d_start and cached.end_dir == d_end:
                return cached

            # On-demand calculation if grid and terminals are available
            grid = getattr(self.cost_tensor, "grid", None)
            router = getattr(self.cost_tensor, "router", None)
            if grid is not None and router is not None:
                if u_id in self.cost_tensor.terminals and v_id in self.cost_tensor.terminals:
                    try:
                        m = router.find_shortest_path(
                            grid=grid,
                            start=self.cost_tensor.terminals[u_id],
                            end=self.cost_tensor.terminals[v_id],
                            start_dir=d_start,
                            end_dir=d_end,
                            start_id=u_id,
                            end_id=v_id,
                        )
                        if m.start_dir == d_start and m.end_dir == d_end:
                            self.cost_tensor.index.pair_metrics[key] = m
                            return m
                    except Exception:
                        pass
            return None

        # Unconstrained metric lookup
        return self.cost_tensor.get_metric(u_id, v_id)

    def solve(
        self,
    ) -> Tuple[List[str], List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]]]:
        """Solve optimal direction-aware ring sequence and return (closed_sequence, leg_directions).

        Returns:
            Tuple[List[str], List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]]]:
            - closed_sequence: [H_k, N_sigma(1), ..., N_sigma(m), H_k]
            - leg_directions: [(d_start_0, d_end_0), ..., (d_start_m, d_end_m)]
        """
        m = len(self.nodes)

        # Base case 1: 0 nodes assigned to hub
        if m == 0:
            return [self.hub.hub_id, self.hub.hub_id], [(None, None)]

        # Base case 2: 1 node assigned to hub [H, N1, H]
        if m == 1:
            node = self.nodes[0]
            best_cost = float("inf")
            best_tuple: Optional[
                Tuple[
                    Tuple[Optional[CompassDirection], Optional[CompassDirection]],
                    Tuple[Optional[CompassDirection], Optional[CompassDirection]],
                ]
            ] = None

            for d_hs in self.ALL_DIRECTIONS:
                for d_in in self.ALL_DIRECTIONS:
                    m1 = self._get_metric(self.hub.hub_id, node.node_id, d_hs, d_in)
                    if m1 is None:
                        continue
                    for d_out in self.ALL_DIRECTIONS:
                        turn_node = 0.5 if d_out != d_in else 0.0
                        for d_he in self.ALL_DIRECTIONS:
                            m2 = self._get_metric(node.node_id, self.hub.hub_id, d_out, d_he)
                            if m2 is None:
                                continue
                            turn_hub = 0.5 if d_he != d_hs else 0.0
                            tot = m1.cost_J + turn_node + m2.cost_J + turn_hub
                            if tot < best_cost:
                                best_cost = tot
                                best_tuple = ((d_hs, d_in), (d_out, d_he))

            if best_tuple is not None:
                return (
                    [self.hub.hub_id, node.node_id, self.hub.hub_id],
                    [best_tuple[0], best_tuple[1]],
                )

            # Fallback unconstrained for m=1
            m1_u = self._get_metric(self.hub.hub_id, node.node_id)
            m2_u = self._get_metric(node.node_id, self.hub.hub_id)
            d1 = (m1_u.start_dir, m1_u.end_dir) if m1_u else (None, None)
            d2 = (m2_u.start_dir, m2_u.end_dir) if m2_u else (None, None)
            return [self.hub.hub_id, node.node_id, self.hub.hub_id], [d1, d2]

        # Case 3: m >= 2 nodes (Held-Karp Dynamic Programming)
        # State: (S, u, d_in, d_hub_start)
        # S: visited bitmask (1 to (1 << m) - 1)
        # u: last visited node index (0 to m - 1)
        # d_in: incoming arrival direction at node u
        # d_hub_start: initial departure direction from hub H_k
        dp: Dict[Tuple[int, int, CompassDirection, CompassDirection], float] = {}
        parent: Dict[
            Tuple[int, int, CompassDirection, CompassDirection],
            Tuple[int, int, Optional[CompassDirection], CompassDirection],
        ] = {}

        # 1. Base cases: size |S| = 1
        for u in range(m):
            mask = 1 << u
            for d_hs in self.ALL_DIRECTIONS:
                for d_in in self.ALL_DIRECTIONS:
                    metric = self._get_metric(self.hub.hub_id, self.nodes[u].node_id, d_hs, d_in)
                    if metric is not None:
                        state = (mask, u, d_in, d_hs)
                        dp[state] = metric.cost_J
                        parent[state] = (0, -1, None, d_hs)

        # 2. Inductive step: masks of size 1 to m-1
        for mask_size in range(1, m):
            for S in range(1, 1 << m):
                if bin(S).count("1") != mask_size:
                    continue

                for u in range(m):
                    if not (S & (1 << u)):
                        continue

                    for d_in in self.ALL_DIRECTIONS:
                        for d_hs in self.ALL_DIRECTIONS:
                            curr_state = (S, u, d_in, d_hs)
                            curr_cost = dp.get(curr_state)
                            if curr_cost is None:
                                continue

                            for v in range(m):
                                if S & (1 << v):
                                    continue

                                next_mask = S | (1 << v)

                                for d_out in self.ALL_DIRECTIONS:
                                    turn_cost = 0.5 if d_out != d_in else 0.0

                                    for d_next_in in self.ALL_DIRECTIONS:
                                        m_leg = self._get_metric(
                                            self.nodes[u].node_id,
                                            self.nodes[v].node_id,
                                            d_out,
                                            d_next_in,
                                        )
                                        if m_leg is None:
                                            continue

                                        next_state = (next_mask, v, d_next_in, d_hs)
                                        cand_cost = curr_cost + turn_cost + m_leg.cost_J

                                        if cand_cost < dp.get(next_state, float("inf")):
                                            dp[next_state] = cand_cost
                                            parent[next_state] = (S, u, d_in, d_out)

        # 3. Final return leg to hub
        full_mask = (1 << m) - 1
        best_total_cost = float("inf")
        best_final: Optional[
            Tuple[int, CompassDirection, CompassDirection, CompassDirection, CompassDirection]
        ] = None

        for u in range(m):
            for d_in in self.ALL_DIRECTIONS:
                for d_hs in self.ALL_DIRECTIONS:
                    state = (full_mask, u, d_in, d_hs)
                    curr_cost = dp.get(state)
                    if curr_cost is None:
                        continue

                    for d_out in self.ALL_DIRECTIONS:
                        turn_node = 0.5 if d_out != d_in else 0.0

                        for d_he in self.ALL_DIRECTIONS:
                            m_return = self._get_metric(
                                self.nodes[u].node_id,
                                self.hub.hub_id,
                                d_out,
                                d_he,
                            )
                            if m_return is None:
                                continue

                            turn_hub = 0.5 if d_he != d_hs else 0.0
                            total_cost = curr_cost + turn_node + m_return.cost_J + turn_hub

                            if total_cost < best_total_cost:
                                best_total_cost = total_cost
                                best_final = (u, d_in, d_hs, d_out, d_he)

        # 4. Reconstruction if optimal state found
        if best_final is not None:
            u_last, d_in_last, d_hs, d_out_last, d_he = best_final
            reversed_nodes = [u_last]
            reversed_dirs: List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]] = [
                (d_out_last, d_he)
            ]

            curr_state = (full_mask, u_last, d_in_last, d_hs)

            while True:
                prev_S, prev_u, prev_d_in, prev_d_out = parent[curr_state]
                if prev_u == -1:
                    # Initial departure from hub
                    reversed_dirs.append((d_hs, curr_state[2]))
                    break

                reversed_nodes.append(prev_u)
                reversed_dirs.append((prev_d_out, curr_state[2]))
                curr_state = (prev_S, prev_u, prev_d_in, d_hs)

            node_order = list(reversed(reversed_nodes))
            leg_directions = list(reversed(reversed_dirs))
            closed_sequence = (
                [self.hub.hub_id]
                + [self.nodes[idx].node_id for idx in node_order]
                + [self.hub.hub_id]
            )
            return closed_sequence, leg_directions

        # 5. Robust Fallback: Unconstrained permutation search if exact directions had gaps
        return self._solve_unconstrained_fallback()

    def _solve_unconstrained_fallback(
        self,
    ) -> Tuple[List[str], List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]]]:
        """Fallback solver using unconstrained metrics across node permutations."""
        m = len(self.nodes)
        node_ids = [n.node_id for n in self.nodes]
        best_cost = float("inf")
        best_seq: List[str] = [self.hub.hub_id] + node_ids + [self.hub.hub_id]
        best_dirs: List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]] = []

        for p in itertools.permutations(range(m)):
            seq = [self.hub.hub_id] + [node_ids[i] for i in p] + [self.hub.hub_id]
            seq_cost = 0.0
            dirs: List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]] = []
            valid = True

            for i in range(len(seq) - 1):
                u_id, v_id = seq[i], seq[i + 1]
                metric = self._get_metric(u_id, v_id)
                if metric is None:
                    valid = False
                    break
                seq_cost += metric.cost_J
                dirs.append((metric.start_dir, metric.end_dir))

            if valid and seq_cost < best_cost:
                best_cost = seq_cost
                best_seq = seq
                best_dirs = dirs

        return best_seq, best_dirs

    @classmethod
    def solve_hub_ring_sequence(
        cls,
        hub: HubAssignment,
        nodes: Sequence[FixedNode],
        cost_tensor: PairwiseCostTensor,
    ) -> Tuple[List[str], List[Tuple[Optional[CompassDirection], Optional[CompassDirection]]]]:
        """Convenience classmethod solving optimal direction-aware ring sequence."""
        solver = cls(hub=hub, nodes=nodes, cost_tensor=cost_tensor)
        return solver.solve()
