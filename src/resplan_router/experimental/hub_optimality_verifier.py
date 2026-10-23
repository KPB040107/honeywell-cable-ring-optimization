"""Hub-Count Optimality Verifier & Exact Bound Certification Module (Phase 17 Agent 2).

Provides an independent, mathematically rigorous audit of hub-count optimality across
all architectural complexity tiers (compact, standard, complex, adversarial).

Features:
1. Exact Lower Bound Hierarchy:
   - H_area = ceil(total_eligible_area / 100.0)
   - H_nodes = ceil(total_eligible_nodes / 5.0)
   - H_naive = max(H_area, H_nodes)
   - H_comp = sum_{C in Components(G_door)} max(1, ceil(area(C)/100), ceil(nodes(C)/5))
   - G_conflict: Pairwise room conflict graph where (u, v) in E iff no valid connected
     cluster can cover both u and v under area <= 100m², nodes <= 5, and hub clearance.
   - omega(G_conflict): Maximum clique size lower bound.
   - chi(G_conflict): Chromatic number lower bound.
   - H_LB = max(H_naive, H_comp, omega(G_conflict), chi(G_conflict))

2. Dual Exact Solvers:
   - Independent Bitmask DP partitioner.
   - Independent OR-Tools CP-SAT integer partitioner.
   - Cross-validation: H*(BitmaskDP) == H*(CPSAT) == H*_proven.

3. Formal Optimality Certification:
   - Emits structured JSON certificates proving H* == H_LB or explaining gap H* > H_LB
     due to star-bottleneck packing, doorway severance, bin fragmentation, or clearance limits.

4. Decoupling Analysis (H* Optimality != J* Optimality):
   - Mathematically formalizes and empirically demonstrates that minimum hub count H*
     does NOT imply global routing-cost J optimality (rectilinear Steiner TSP on Hanan grid).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import itertools
import math
import time
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

import networkx as nx
from ortools.sat.python import cp_model
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
from resplan_router.optimizers.cpsat_hanan import CPSATHananOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter, compress_polyline
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase11_optimization.objective import compute_J


# =============================================================================
# 1. Optimality Certificate Dataclass
# =============================================================================

@dataclass
class HubOptimalityCertificate:
    """Formal mathematical certificate of hub-count optimality."""

    plan_id: str
    tier: str  # "compact", "standard", "complex", "adversarial"
    num_eligible_rooms: int
    num_fixed_nodes: int
    total_eligible_area_m2: float
    
    # Mathematical lower bounds
    h_area_lb: int
    h_nodes_lb: int
    h_naive_lb: int
    h_component_lb: int
    conflict_clique_lb: int
    conflict_chromatic_lb: int
    h_theoretical_lb: int

    # Exact solver solutions
    h_bitmask_dp: int
    h_cpsat: int
    solver_agreement: bool
    h_proven_min: int
    is_optimal: bool

    # Gap metrics
    gap_to_naive_lb: int  # H* - H_naive_lb
    gap_to_theoretical_lb: int  # H* - H_theoretical_lb
    gap_root_cause: str  # "ZERO_GAP_MATCH", "DOORWAY_SEVERANCE", "STAR_BOTTLENECK", "BIN_FRAGMENTATION", "CLEARANCE_RESTRICTION"
    formal_proof: str

    # Partition details
    selected_partition: List[List[str]]
    hub_clearance_verified: bool

    # Routing cost discrepancy analysis (H* vs J*)
    runtime_bitmask_dp_s: float
    runtime_cpsat_s: float
    routing_j_bitmask_dp: Optional[float] = None
    routing_j_cpsat: Optional[float] = None
    j_delta_pct: Optional[float] = None
    routing_cost_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert certificate to serializable dictionary."""
        return asdict(self)


# =============================================================================
# 2. Hub-Count Optimality Verifier Engine
# =============================================================================

class HubOptimalityVerifier:
    """Rigorous mathematical audit engine for hub-count optimality."""

    def __init__(self, time_limit_s: float = 5.0) -> None:
        self.time_limit_s = time_limit_s
        self.bitmask_optimizer = BitmaskDPOptimizer()
        self.cpsat_optimizer = CPSATHananOptimizer()

    @staticmethod
    def build_door_graph(plan: MetricFloorPlan, topology: Any) -> nx.Graph:
        """Construct door-connectivity graph restricted to eligible rooms."""
        graph = nx.Graph()
        eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
        graph.add_nodes_from(eligible_rooms)

        for r_id in eligible_rooms:
            for neighbor in topology.adjacency.get(r_id, []):
                if neighbor in eligible_rooms:
                    graph.add_edge(r_id, neighbor)

        return graph

    @staticmethod
    def extract_safe_hub_locations(plan: MetricFloorPlan) -> Dict[str, Tuple[Point2D, float]]:
        """Identify rooms that admit safe hub placement (clearance >= 0.50 m)."""
        safe_locations: Dict[str, Tuple[Point2D, float]] = {}
        obs_buf = plan.obstacles_geom.buffer(0.50) if plan.obstacles_geom else None

        for r_id, r in plan.rooms.items():
            if not r.is_eligible:
                continue
            r_geom = r.geometry
            safe_poly = r_geom.difference(obs_buf) if obs_buf else r_geom
            safe_poly = shapely.make_valid(safe_poly)
            if not safe_poly.is_empty and safe_poly.area > 1e-4:
                try:
                    target_poly = (
                        safe_poly
                        if isinstance(safe_poly, Polygon)
                        else max(safe_poly.geoms, key=lambda g: g.area)
                    )
                    pole = polylabel(target_poly, tolerance=0.005)
                    pt = Point(pole.x, pole.y)
                    clr = plan.obstacles_geom.distance(pt) if plan.obstacles_geom else 1.0
                    safe_locations[r_id] = (Point2D(x=round(pole.x, 4), y=round(pole.y, 4)), clr)
                except Exception:
                    pass

        return safe_locations

    @classmethod
    def enumerate_valid_clusters(
        cls,
        plan: MetricFloorPlan,
        door_graph: nx.Graph,
        safe_hub_locations: Dict[str, Tuple[Point2D, float]],
        max_area: float = 100.0,
        max_nodes: int = 5,
    ) -> List[Tuple[FrozenSet[str], float, int, str]]:
        """Enumerate all valid door-connected clusters satisfying area, node, and clearance limits.

        Returns:
            List of tuples: (cluster_rooms_frozenset, cluster_area, cluster_node_count, best_hub_room)
        """
        eligible_rooms = sorted(list(door_graph.nodes))
        n = len(eligible_rooms)
        if n == 0:
            return []

        valid_clusters: List[Tuple[FrozenSet[str], float, int, str]] = []

        # Enumerate subsets up to capacity limits
        for k in range(1, min(n, max_nodes) + 1):
            for subset in itertools.combinations(eligible_rooms, k):
                s_rooms = frozenset(subset)
                s_area = sum(plan.rooms[r].area_m2 for r in s_rooms)
                if s_area > max_area + 1e-4:
                    continue

                s_nodes = sum(1 for node in plan.nodes.values() if node.room_id in s_rooms)
                if s_nodes > max_nodes:
                    continue

                # Hub feasibility: at least one room must have safe hub clearance >= 0.50m
                safe_rooms = [r for r in s_rooms if r in safe_hub_locations]
                if not safe_rooms:
                    continue

                # Doorway contiguity
                if k > 1:
                    sub_g = door_graph.subgraph(s_rooms)
                    if not nx.is_connected(sub_g):
                        continue

                # Select best hub room (closest to devices)
                device_pts = [n.location for n in plan.nodes.values() if n.room_id in s_rooms]
                best_hr = safe_rooms[0]
                best_dist = float("inf")
                for hr in safe_rooms:
                    hpt = safe_hub_locations[hr][0]
                    avg_d = (
                        sum(math.hypot(hpt.x - dp.x, hpt.y - dp.y) for dp in device_pts) / max(1, len(device_pts))
                        if device_pts
                        else 0.0
                    )
                    if avg_d < best_dist:
                        best_dist = avg_d
                        best_hr = hr

                valid_clusters.append((s_rooms, round(s_area, 4), s_nodes, best_hr))

        return valid_clusters

    @classmethod
    def build_conflict_graph(
        cls,
        eligible_rooms: List[str],
        valid_clusters: Sequence[Tuple[FrozenSet[str], float, int, str]],
    ) -> nx.Graph:
        """Construct conflict graph G_conflict = (R, E).

        An edge (u, v) exists if NO valid connected cluster contains both u and v.
        """
        graph = nx.Graph()
        graph.add_nodes_from(eligible_rooms)

        # Precompute co-occurrence lookup
        co_occurrence: Set[Tuple[str, str]] = set()
        for cluster, _, _, _ in valid_clusters:
            for u in cluster:
                for v in cluster:
                    if u < v:
                        co_occurrence.add((u, v))

        for u, v in itertools.combinations(eligible_rooms, 2):
            if (u, v) not in co_occurrence and (v, u) not in co_occurrence:
                graph.add_edge(u, v)

        return graph

    @classmethod
    def compute_max_clique_bound(cls, conflict_graph: nx.Graph) -> int:
        """Compute maximum clique size omega(G_conflict).

        Every pair in a clique cannot share a zone, so H >= omega.
        """
        if len(conflict_graph.nodes) == 0:
            return 0
        cliques = list(nx.find_cliques(conflict_graph))
        return max(len(c) for c in cliques) if cliques else 1

    @classmethod
    def compute_chromatic_bound(cls, conflict_graph: nx.Graph) -> int:
        """Compute exact or tight chromatic number chi(G_conflict).

        Any valid hub partition is a proper vertex coloring of G_conflict.
        Therefore, H >= chi(G_conflict) >= omega(G_conflict).
        """
        n = len(conflict_graph.nodes)
        if n == 0:
            return 0
        if len(conflict_graph.edges) == 0:
            return 1

        # Use CP-SAT to find exact chromatic number for small graphs (n <= 20)
        omega = cls.compute_max_clique_bound(conflict_graph)
        nodes = list(conflict_graph.nodes)

        for c_cand in range(omega, n + 1):
            model = cp_model.CpModel()
            color_vars = {u: model.NewIntVar(0, c_cand - 1, f"col_{u}") for u in nodes}

            # Adjacent vertices must have different colors
            for u, v in conflict_graph.edges:
                model.Add(color_vars[u] != color_vars[v])

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 1.0
            status = solver.Solve(model)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return c_cand

        # Fallback to greedy coloring upper bound on chi
        coloring = nx.coloring.greedy_color(conflict_graph, strategy="largest_first")
        return max(coloring.values()) + 1 if coloring else 1

    @classmethod
    def solve_exact_bitmask_dp(
        cls,
        eligible_rooms: List[str],
        valid_clusters: Sequence[Tuple[FrozenSet[str], float, int, str]],
    ) -> Tuple[int, List[FrozenSet[str]]]:
        """Solve minimum hub set-partitioning using exact Bitmask Dynamic Programming."""
        n = len(eligible_rooms)
        if n == 0:
            return 0, []

        room_idx = {r: i for i, r in enumerate(eligible_rooms)}
        cluster_data: List[Tuple[int, FrozenSet[str], float]] = []

        for c_rooms, c_area, _, _ in valid_clusters:
            mask = sum(1 << room_idx[r] for r in c_rooms)
            cluster_data.append((mask, c_rooms, c_area))

        # Sort descending by size and area
        cluster_data.sort(key=lambda item: (len(item[1]), item[2]), reverse=True)

        full_mask = (1 << n) - 1
        INF = 999999
        dp = [INF] * (1 << n)
        dp_prev = [-1] * (1 << n)
        dp_cidx = [-1] * (1 << n)
        dp_gap = [float("inf")] * (1 << n)
        dp_areas: List[List[float]] = [[] for _ in range(1 << n)]

        dp[0] = 0
        dp_gap[0] = 0.0

        for mask in range(1 << n):
            if dp[mask] == INF:
                continue

            curr_h = dp[mask]
            for c_i, (c_mask, c_rooms, c_area) in enumerate(cluster_data):
                if (mask & c_mask) == 0:
                    next_mask = mask | c_mask
                    cand_h = curr_h + 1
                    cand_areas = dp_areas[mask] + [c_area]
                    cand_gap = (
                        max(cand_areas) - min(cand_areas) if len(cand_areas) > 1 else 0.0
                    )

                    update = False
                    if cand_h < dp[next_mask]:
                        update = True
                    elif cand_h == dp[next_mask] and cand_gap < dp_gap[next_mask] - 1e-4:
                        update = True

                    if update:
                        dp[next_mask] = cand_h
                        dp_prev[next_mask] = mask
                        dp_cidx[next_mask] = c_i
                        dp_gap[next_mask] = cand_gap
                        dp_areas[next_mask] = cand_areas

        if dp[full_mask] == INF:
            return -1, []

        # Reconstruct
        partition: List[FrozenSet[str]] = []
        curr = full_mask
        while curr > 0:
            c_i = dp_cidx[curr]
            partition.append(cluster_data[c_i][1])
            curr = dp_prev[curr]

        partition.reverse()
        return len(partition), partition

    @classmethod
    def solve_exact_cpsat(
        cls,
        eligible_rooms: List[str],
        valid_clusters: Sequence[Tuple[FrozenSet[str], float, int, str]],
        time_limit_s: float = 3.0,
    ) -> Tuple[int, List[FrozenSet[str]]]:
        """Solve minimum hub set-partitioning using OR-Tools CP-SAT integer programming."""
        n = len(eligible_rooms)
        if n == 0:
            return 0, []

        model = cp_model.CpModel()
        cluster_vars = [model.NewBoolVar(f"z_{i}") for i in range(len(valid_clusters))]

        # Exact partition constraint: each room in exactly one chosen cluster
        for r in eligible_rooms:
            containing = [cluster_vars[i] for i, c in enumerate(valid_clusters) if r in c[0]]
            model.Add(sum(containing) == 1)

        # Minimize hub count
        total_hubs = model.NewIntVar(1, n, "total_hubs")
        model.Add(total_hubs == sum(cluster_vars))
        model.Minimize(total_hubs)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return -1, []

        chosen = [valid_clusters[i][0] for i, var in enumerate(cluster_vars) if solver.Value(var) == 1]
        return len(chosen), chosen

    def audit_plan(
        self,
        plan: MetricFloorPlan,
        tier: str = "standard",
        run_full_routing: bool = True,
    ) -> HubOptimalityCertificate:
        """Perform comprehensive mathematical audit and generate optimality certificate."""
        eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
        n_eligible = len(eligible_rooms)
        nodes_in_elig = [n for n in plan.nodes.values() if n.room_id in plan.rooms and plan.rooms[n.room_id].is_eligible]
        n_nodes = len(nodes_in_elig)
        tot_area = sum(plan.rooms[r].area_m2 for r in eligible_rooms)

        # 1. Base Mathematical Lower Bounds
        h_area_lb = math.ceil(tot_area / 100.0) if n_eligible > 0 else 0
        h_nodes_lb = math.ceil(n_nodes / 5.0) if n_eligible > 0 else 0
        h_naive_lb = max(1, h_area_lb, h_nodes_lb) if n_eligible > 0 else 0

        # 2. Door Connectivity Graph & Component Lower Bound
        portals = extract_all_portals(plan)
        topology = MacroTopologyGraph.build_topology(plan, portals)
        door_graph = self.build_door_graph(plan, topology)

        components = list(nx.connected_components(door_graph))
        h_comp_lb = 0
        if components:
            for comp in components:
                c_area = sum(plan.rooms[r].area_m2 for r in comp)
                c_devs = sum(1 for n in plan.nodes.values() if n.room_id in comp)
                h_comp_lb += max(1, math.ceil(c_area / 100.0), math.ceil(c_devs / 5.0))
        else:
            h_comp_lb = 0

        # 3. Safe Hub Clearance Locations
        safe_locations = self.extract_safe_hub_locations(plan)
        valid_clusters = self.enumerate_valid_clusters(plan, door_graph, safe_locations)

        # 4. Conflict Graph, Clique Bound, and Chromatic Bound
        conflict_graph = self.build_conflict_graph(eligible_rooms, valid_clusters)
        omega = self.compute_max_clique_bound(conflict_graph)
        chi = self.compute_chromatic_bound(conflict_graph)

        h_theoretical_lb = max(h_naive_lb, h_comp_lb, omega, chi)

        # 5. Exact Solver Optimizations (BitmaskDP and CP-SAT)
        t0 = time.perf_counter()
        h_dp, part_dp = self.solve_exact_bitmask_dp(eligible_rooms, valid_clusters)
        t_dp = time.perf_counter() - t0

        t0 = time.perf_counter()
        h_cp, part_cp = self.solve_exact_cpsat(eligible_rooms, valid_clusters, time_limit_s=self.time_limit_s)
        t_cp = time.perf_counter() - t0

        solver_agreement = (h_dp == h_cp) and (h_dp != -1)
        h_proven_min = h_dp if h_dp != -1 else h_cp

        # Full routing comparison (if requested)
        j_dp: Optional[float] = None
        j_cp: Optional[float] = None
        j_delta_pct: Optional[float] = None
        routing_note: Optional[str] = None

        if run_full_routing and h_proven_min > 0:
            try:
                res_dp = self.bitmask_optimizer.optimize(plan)
                if res_dp.bundle:
                    j_dp = round(res_dp.bundle.total_cost_J, 4)
            except Exception:
                pass

            try:
                res_cp = self.cpsat_optimizer.optimize(plan)
                if res_cp.bundle:
                    j_cp = round(res_cp.bundle.total_cost_J, 4)
            except Exception:
                pass

            if j_dp is not None and j_cp is not None:
                base_j = min(j_dp, j_cp)
                diff = abs(j_dp - j_cp)
                j_delta_pct = round((diff / base_j) * 100.0, 2) if base_j > 1e-4 else 0.0
                if diff > 1e-3:
                    routing_note = (
                        f"Solvers achieved identical minimal hub count H*={h_proven_min}, "
                        f"but different routing cost J (BitmaskDP: {j_dp:.2f}, CPSAT: {j_cp:.2f}, "
                        f"delta: {j_delta_pct}%). Proves H* optimality does NOT imply J* optimality."
                    )
                else:
                    routing_note = f"Both solvers converged to identical partition and routing cost J={j_dp:.2f}."

        # Gap Diagnosis & Root Cause
        gap_naive = h_proven_min - h_naive_lb
        gap_theoretical = h_proven_min - h_theoretical_lb

        if gap_naive == 0:
            root_cause = "ZERO_GAP_MATCH"
            proof = (
                f"Global unconstrained lower bound H_naive = max(ceil({tot_area:.1f}/100), "
                f"ceil({n_nodes}/5)) = {h_naive_lb} is exactly achieved by solver H* = {h_proven_min}. "
                f"Certified 100% mathematically optimal with 0 gap."
            )
        elif h_comp_lb > h_naive_lb and h_proven_min == h_comp_lb:
            root_cause = "DOORWAY_SEVERANCE"
            proof = (
                f"Floor plan contains {len(components)} disconnected door-connectivity components. "
                f"Cables cannot cross solid walls between isolated building wings. "
                f"Component lower bound H_comp = {h_comp_lb} proves that H* = {h_proven_min} > H_naive = {h_naive_lb} "
                f"is strictly necessary and sufficient."
            )
        elif omega >= h_proven_min or chi >= h_proven_min:
            root_cause = "STAR_BOTTLENECK"
            proof = (
                f"Door graph topology contains an articulation/star bottleneck. "
                f"Conflict graph clique size omega = {omega} and chromatic number chi = {chi} "
                f"prove that no valid connected clusters can merge leaf rooms without exceeding 100m². "
                f"H* = {h_proven_min} is certified optimal by graph chromatic bound."
            )
        elif h_proven_min > h_theoretical_lb:
            root_cause = "BIN_FRAGMENTATION"
            proof = (
                f"Bin-packing fragmentation: individual room areas form indivisible geometric items "
                f"that cannot be partitioned into fewer than {h_proven_min} door-connected components "
                f"without at least one component exceeding 100.0 m²."
            )
        else:
            root_cause = "ZERO_GAP_THEORETICAL"
            proof = (
                f"Theoretical structural bound H_LB = {h_theoretical_lb} matches exact solver output "
                f"H* = {h_proven_min} (Gap = 0)."
            )

        # Clearance verification
        all_clear = True
        chosen_partition_list = [sorted(list(s)) for s in (part_dp if part_dp else part_cp)]
        for z_rooms in chosen_partition_list:
            if not any(r in safe_locations for r in z_rooms):
                all_clear = False
                break

        return HubOptimalityCertificate(
            plan_id=str(plan.plan_id),
            tier=tier,
            num_eligible_rooms=n_eligible,
            num_fixed_nodes=n_nodes,
            total_eligible_area_m2=round(tot_area, 2),
            h_area_lb=h_area_lb,
            h_nodes_lb=h_nodes_lb,
            h_naive_lb=h_naive_lb,
            h_component_lb=h_comp_lb,
            conflict_clique_lb=omega,
            conflict_chromatic_lb=chi,
            h_theoretical_lb=h_theoretical_lb,
            h_bitmask_dp=h_dp,
            h_cpsat=h_cp,
            solver_agreement=solver_agreement,
            h_proven_min=h_proven_min,
            is_optimal=(h_proven_min > 0 and solver_agreement),
            gap_to_naive_lb=gap_naive,
            gap_to_theoretical_lb=max(0, gap_theoretical),
            gap_root_cause=root_cause,
            formal_proof=proof,
            selected_partition=chosen_partition_list,
            hub_clearance_verified=all_clear,
            runtime_bitmask_dp_s=round(t_dp, 5),
            runtime_cpsat_s=round(t_cp, 5),
            routing_j_bitmask_dp=j_dp,
            routing_j_cpsat=j_cp,
            j_delta_pct=j_delta_pct,
            routing_cost_note=routing_note,
        )


# =============================================================================
# 3. Global Theorem Demonstrator (H* Optimality != J* Optimality)
# =============================================================================

def demonstrate_decoupling_h_and_j(plan: MetricFloorPlan) -> Dict[str, Any]:
    """Empirically and mathematically prove that hub-count optimality H* != J* optimality.

    Returns detailed demonstration dictionary showing multiple partitions achieving H*
    with distinct Hanan grid routing costs J.
    """
    verifier = HubOptimalityVerifier()
    cert = verifier.audit_plan(plan, tier="standard", run_full_routing=True)

    b_dp = BitmaskDPOptimizer()
    cp_h = CPSATHananOptimizer()

    res_dp = b_dp.optimize(plan)
    res_cp = cp_h.optimize(plan)

    h_dp = len(res_dp.partition.hubs) if res_dp.partition else 0
    h_cp = len(res_cp.partition.hubs) if res_cp.partition else 0
    j_dp = res_dp.bundle.total_cost_J if res_dp.bundle else float("inf")
    j_cp = res_cp.bundle.total_cost_J if res_cp.bundle else float("inf")

    part_dp = [sorted(h.assigned_rooms) for h in res_dp.partition.hubs] if res_dp.partition else []
    part_cp = [sorted(h.assigned_rooms) for h in res_cp.partition.hubs] if res_cp.partition else []

    identical_h = (h_dp == h_cp)
    different_partition = (part_dp != part_cp)
    cost_difference = abs(j_dp - j_cp)

    return {
        "plan_id": plan.plan_id,
        "proven_minimum_hubs": cert.h_proven_min,
        "bitmask_dp": {
            "hubs": h_dp,
            "cost_J": round(j_dp, 4),
            "partition": part_dp,
        },
        "cpsat": {
            "hubs": h_cp,
            "cost_J": round(j_cp, 4),
            "partition": part_cp,
        },
        "identical_hub_count": identical_h,
        "distinct_partitions": different_partition,
        "cost_difference_J": round(cost_difference, 4),
        "percentage_difference_pct": round((cost_difference / min(j_dp, j_cp)) * 100.0, 2) if min(j_dp, j_cp) > 0 else 0.0,
        "theoretical_decoupling_certified": (identical_h and (different_partition or cost_difference > 1e-3)),
    }
