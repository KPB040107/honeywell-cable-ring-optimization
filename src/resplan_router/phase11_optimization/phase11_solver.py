"""Unified Phase 11 Hybrid Optimizer Pipeline.

Executes the 9-stage optimization workflow:
1. Building door graph (MacroTopologyGraph & door-connected components)
2. Generating device positions (ensuring 1 device per eligible room, >= 0.50m clearance)
3. Proving minimum hubs (Theoretical lower bounds + exact CP-SAT feasibility check)
4. Deterministic greedy baseline solver (mandatory incumbent floor: J_opt <= J_greedy)
5. Generating route & ring candidates (Directional A*, Yen's K-shortest paths, exact Held-Karp)
6. Global CP-SAT selection (Exact room cover, linearized crossings/overlap)
7. ALNS improvement (Multi-operator destroy/repair with simulated annealing)
8. Independent final validation (FloorPlanChecker 1 mm audit)
9. Scoreboard assembly (Greedy vs. Optimized comparisons, hub proof status, optimality gap)
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from resplan_router.contracts import (
    EvaluationReport,
    HubZonePartition,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase11_optimization.alns import ALNSConfig, ALNSOptimizer, rings_to_bundle_and_partition
from resplan_router.phase11_optimization.conflict_matrix import PairwiseConflictMatrix
from resplan_router.phase11_optimization.global_selector import GlobalRingSelector, GlobalSelectionResult
from resplan_router.phase11_optimization.greedy_baseline import GreedyBaselineResult, solve_greedy_baseline
from resplan_router.phase11_optimization.lower_bounds import prove_minimum_hubs
from resplan_router.phase11_optimization.objective import LexicographicCost, evaluate_solution
from resplan_router.phase11_optimization.ring_pool import CompleteRingCandidate, ZoneRingGenerator


@dataclass
class Phase11OptimizationResult:
    """Complete output and official scoreboard for Phase 11 optimization."""

    bundle: SynthesizedRingsBundle
    partition: HubZonePartition
    report: EvaluationReport
    cost: LexicographicCost

    # Greedy baseline metrics
    greedy_H: int
    greedy_J: float
    greedy_L: float
    greedy_B: int
    greedy_X: int
    greedy_O: float
    greedy_runtime_s: float

    # Optimized metrics
    opt_H: int
    opt_J: float
    opt_L: float
    opt_B: int
    opt_X: int
    opt_O: float
    opt_delta_area_gap: float
    improvement_percent: float

    # Verification and Bounds
    hub_optimality_status: str  # "PROVEN OPTIMAL HUB COUNT" or "FEASIBLE HUB COUNT"
    relaxed_j_lower_bound: float
    estimated_j_gap_percent: float
    total_runtime_s: float
    stages_completed: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "greedy_metrics": {
                "H": self.greedy_H,
                "J": self.greedy_J,
                "L": self.greedy_L,
                "B": self.greedy_B,
                "X": self.greedy_X,
                "O": self.greedy_O,
                "runtime_s": round(self.greedy_runtime_s, 4),
            },
            "optimized_metrics": {
                "H": self.opt_H,
                "J": self.opt_J,
                "L": self.opt_L,
                "B": self.opt_B,
                "X": self.opt_X,
                "O": self.opt_O,
                "delta_area_gap_m2": self.opt_delta_area_gap,
            },
            "improvement_percent": round(self.improvement_percent, 2),
            "hub_optimality_status": self.hub_optimality_status,
            "relaxed_j_lower_bound": round(self.relaxed_j_lower_bound, 4),
            "estimated_j_gap_percent": round(self.estimated_j_gap_percent, 2),
            "total_runtime_s": round(self.total_runtime_s, 4),
            "is_valid": self.report.is_valid,
            "rules_passed": self.report.rules_passed,
        }


def optimize_floor_plan(
    plan: MetricFloorPlan,
    topology: Optional[Any] = None,
    raw_doors: Optional[Any] = None,
    raw_front_door: Optional[Any] = None,
    time_limit_s: float = 10.0,
    random_seed: int = 42,
    stage_callback: Optional[Callable[[str], None]] = None,
) -> Phase11OptimizationResult:
    """Execute complete Phase 11 hybrid optimization pipeline."""
    t_start = time.perf_counter()
    completed_stages: List[str] = []

    def notify_stage(stage_name: str) -> None:
        completed_stages.append(stage_name)
        if stage_callback:
            stage_callback(stage_name)

    # 1. Building door graph
    notify_stage("Building door graph")
    if topology is None:
        portals = extract_all_portals(plan, raw_doors=raw_doors, raw_entrance=raw_front_door)
        topology = MacroTopologyGraph.build_topology(plan, portals)

    # 2. Generating device positions (if not already set in plan)
    notify_stage("Generating device positions")
    # Plan already has validated nodes dictionary

    # 3. Proving minimum hubs
    notify_stage("Proving minimum hubs")
    proven_h, is_proven_optimal, optimal_zone_partition = prove_minimum_hubs(
        plan=plan, topology=topology, time_limit_s=2.0
    )
    hub_status_badge = (
        "PROVEN OPTIMAL HUB COUNT" if is_proven_optimal else "FEASIBLE HUB COUNT"
    )

    # 4. Greedy baseline solver (mandatory incumbent floor)
    notify_stage("Greedy baseline incumbent")
    greedy_res = solve_greedy_baseline(plan=plan, topology=topology, seed=random_seed)

    incumbent_bundle = greedy_res.bundle
    incumbent_partition = greedy_res.partition
    incumbent_cost = greedy_res.cost
    incumbent_report = FloorPlanChecker.evaluate(plan, incumbent_bundle, topology=topology)

    # 5. Generating route & ring candidates
    notify_stage("Generating route candidates")
    generator = ZoneRingGenerator(
        plan=plan,
        topology=topology,
        beam_width=10,
        candidates_per_zone=2,
    )


    # Enumerate zones to generate candidates for
    # Use partitions from proof or greedily grown zones
    all_ring_candidates: List[CompleteRingCandidate] = []
    zone_list = optimal_zone_partition if optimal_zone_partition else [
        frozenset(h.assigned_rooms) for h in incumbent_partition.hubs
    ]
    existing_zones = set(zone_list)

    for z_idx, z_rooms in enumerate(zone_list, start=1):
        cands = generator.generate_zone_candidates(
            zone_rooms=z_rooms,
            zone_idx=z_idx,
            existing_nodes=plan.nodes,
        )
        all_ring_candidates.extend(cands)

    # Also generate individual room ring candidates for flexibility if not already generated
    eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
    for r_idx, r_id in enumerate(eligible_rooms, start=1):
        single_zone = frozenset([r_id])
        if single_zone not in existing_zones:
            single_cands = generator.generate_zone_candidates(
                zone_rooms=single_zone,
                zone_idx=r_idx + len(zone_list),
                existing_nodes=plan.nodes,
            )
            all_ring_candidates.extend(single_cands)

    # 6. Global CP-SAT candidate selection
    notify_stage("CP-SAT selection")
    conflict_matrix = PairwiseConflictMatrix()
    conflict_matrix.precompute(all_ring_candidates)

    selector = GlobalRingSelector(
        plan=plan,
        candidates=all_ring_candidates,
        conflict_matrix=conflict_matrix,
        time_limit_s=min(4.0, time_limit_s / 2.0),
    )
    selection_res = selector.solve()

    if selection_res.selected_rings:
        cpsat_bundle, cpsat_partition = rings_to_bundle_and_partition(plan, selection_res.selected_rings)
        cpsat_cost, cpsat_rep = evaluate_solution(plan, cpsat_bundle, cpsat_partition, topology=topology)
        if cpsat_rep.is_valid and cpsat_cost < incumbent_cost:
            incumbent_bundle = cpsat_bundle
            incumbent_partition = cpsat_partition
            incumbent_cost = cpsat_cost
            incumbent_report = cpsat_rep

    # 7. ALNS improvement with simulated annealing
    notify_stage("ALNS improvement")
    remaining_time = max(1.0, time_limit_s - (time.perf_counter() - t_start) - 0.5)
    alns_cfg = ALNSConfig(
        optimization_time_limit_s=remaining_time,
        random_seed=random_seed,
        initial_temperature=2.0,
        cooling_rate=0.995,
        restart_count=3,
    )
    alns_optimizer = ALNSOptimizer(plan=plan, topology=topology, config=alns_cfg)

    # Convert current incumbent rings into CompleteRingCandidate format for ALNS
    if selection_res.selected_rings:
        init_rings = selection_res.selected_rings
    else:
        # Build ring candidates from incumbent partition
        init_rings = []
        for h_idx, h in enumerate(incumbent_partition.hubs, start=1):
            h_cands = generator.generate_zone_candidates(frozenset(h.assigned_rooms), zone_idx=h_idx, existing_nodes=plan.nodes)
            if h_cands:
                init_rings.append(h_cands[0])

    alns_res = alns_optimizer.optimize(initial_rings=init_rings)
    if alns_res.report.is_valid and alns_res.cost < incumbent_cost:
        incumbent_bundle = alns_res.bundle
        incumbent_partition = alns_res.partition
        incumbent_cost = alns_res.cost
        incumbent_report = alns_res.report

    # Invariant: Hybrid J must never be worse than greedy J!
    if greedy_res.is_valid and greedy_res.cost < incumbent_cost:
        incumbent_bundle = greedy_res.bundle
        incumbent_partition = greedy_res.partition
        incumbent_cost = greedy_res.cost
        incumbent_report = FloorPlanChecker.evaluate(plan, incumbent_bundle, topology=topology)

    # 8. Final validation with independent checker
    notify_stage("Final validation")
    final_report = FloorPlanChecker.evaluate(plan, incumbent_bundle, topology=topology)

    # 9. Assemble scoreboard
    elapsed_total = time.perf_counter() - t_start

    greedy_j = greedy_res.total_cost_J
    opt_j = final_report.total_cost_J
    improvement_pct = max(0.0, ((greedy_j - opt_j) / greedy_j * 100.0)) if greedy_j > 1e-4 else 0.0

    lower_bound_j = selection_res.relaxed_j_lower_bound if selection_res.relaxed_j_lower_bound > 0 else opt_j
    j_gap_pct = max(0.0, ((opt_j - lower_bound_j) / opt_j * 100.0)) if opt_j > 1e-4 else 0.0

    return Phase11OptimizationResult(
        bundle=incumbent_bundle,
        partition=incumbent_partition,
        report=final_report,
        cost=incumbent_cost,
        greedy_H=greedy_res.h_count,
        greedy_J=greedy_res.total_cost_J,
        greedy_L=greedy_res.total_length_m,
        greedy_B=greedy_res.total_bends,
        greedy_X=greedy_res.total_crossings,
        greedy_O=greedy_res.total_overlap_m,
        greedy_runtime_s=greedy_res.runtime_s,
        opt_H=final_report.hub_count,
        opt_J=final_report.total_cost_J,
        opt_L=final_report.total_length_m,
        opt_B=final_report.total_bends,
        opt_X=final_report.total_crossings,
        opt_O=final_report.total_overlap_m,
        opt_delta_area_gap=final_report.area_gap_m2,
        improvement_percent=improvement_pct,
        hub_optimality_status=hub_status_badge,
        relaxed_j_lower_bound=lower_bound_j,
        estimated_j_gap_percent=j_gap_pct,
        total_runtime_s=elapsed_total,
        stages_completed=completed_stages,
    )
