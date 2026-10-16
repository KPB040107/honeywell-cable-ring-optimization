"""Adaptive Large Neighborhood Search (ALNS) with Simulated Annealing for Phase 11.

Iteratively refines the CP-SAT and greedy baseline solutions via:
- Multi-operator destroy & repair neighborhoods.
- Simulated-annealing acceptance for equal-hub candidate configurations:
    P(accept) = exp(-(J_new - J_current) / T)
- Guaranteed retention of best valid incumbent: the solver always returns its best
  valid solution even upon timeout.
- Parameterized cooling schedule with multi-restart capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from resplan_router.contracts import (
    CompletedHubRing,
    EvaluationReport,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    RouteSegment,
    SynthesizedRingsBundle,
)
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase11_optimization.neighborhoods import (
    ALNSSolutionState,
    destroy_boundary_room,
    destroy_highest_cost_ring,
    destroy_hub_relocation,
    repair_reassign_and_solve,
)
from resplan_router.phase11_optimization.objective import (
    LexicographicCost,
    compute_J,
    evaluate_solution,
)
from resplan_router.phase11_optimization.ring_pool import CompleteRingCandidate


@dataclass
class ALNSConfig:
    """Hyperparameter configuration for ALNS with simulated annealing."""

    optimization_time_limit_s: float = 10.0
    random_seed: int = 42
    initial_temperature: float = 2.0
    cooling_rate: float = 0.995
    restart_count: int = 3
    min_temperature: float = 0.01


@dataclass
class ALNSResult:
    """Output from ALNS optimization run."""

    best_rings: List[CompleteRingCandidate]
    bundle: SynthesizedRingsBundle
    partition: HubZonePartition
    cost: LexicographicCost
    report: EvaluationReport
    iterations: int
    improvements: int
    elapsed_time_s: float


def rings_to_bundle_and_partition(
    plan: MetricFloorPlan,
    rings: Sequence[CompleteRingCandidate],
) -> Tuple[SynthesizedRingsBundle, HubZonePartition]:
    """Convert a list of CompleteRingCandidate objects into official bundle and partition contracts."""
    hubs: List[HubAssignment] = []
    hub_rings: List[CompletedHubRing] = []

    for r_idx, r in enumerate(rings, start=1):
        old_hub_id = r.hub_id
        new_hub_id = f"H{r_idx}"
        new_seq = [new_hub_id if s == old_hub_id else s for s in r.closed_sequence]
        new_segs = [
            RouteSegment(
                from_node=new_hub_id if seg.from_node == old_hub_id else seg.from_node,
                to_node=new_hub_id if seg.to_node == old_hub_id else seg.to_node,
                length_m=seg.length_m,
                polyline=seg.polyline,
            )
            for seg in r.route_segments
        ]

        h_assign = HubAssignment(
            hub_id=new_hub_id,
            room_id=r.hub_room_id,
            location=r.hub_location,
            assigned_rooms=sorted(r.room_ids),
            assigned_nodes=sorted(r.device_nodes.keys()),
            zone_area_m2=r.zone_area_m2,
        )
        hubs.append(h_assign)

        hub_ring = CompletedHubRing(
            hub_id=new_hub_id,
            room_id=r.hub_room_id,
            location=r.hub_location,
            assigned_rooms=sorted(r.room_ids),
            assigned_nodes=sorted(r.device_nodes.keys()),
            closed_sequence=new_seq,
            route_segments=new_segs,
            ring_length_m=round(r.total_length_m, 4),
            ring_bends=r.total_bends,
            ring_crossings=r.internal_crossings,
            ring_overlap_m=round(r.internal_overlap_m, 4),
            cost_J=round(r.total_cost_J, 4),
        )
        hub_rings.append(hub_ring)

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
        total_length_m=round(sum(hr.ring_length_m for hr in hub_rings), 4),
        total_bends=sum(hr.ring_bends for hr in hub_rings),
        total_crossings=0,
        total_overlap_m=0.0,
        total_cost_J=0.0,
    )

    return bundle, partition


class ALNSOptimizer:
    """Adaptive Large Neighborhood Search manager with simulated annealing."""

    def __init__(
        self,
        plan: MetricFloorPlan,
        topology: Any,
        config: Optional[ALNSConfig] = None,
    ) -> None:
        self.plan = plan
        self.topology = topology
        self.config = config or ALNSConfig()
        self.rng = random.Random(self.config.random_seed)

    def optimize(
        self,
        initial_rings: Sequence[CompleteRingCandidate],
    ) -> ALNSResult:
        """Run ALNS improvement starting from initial ring candidates."""
        t_start = time.perf_counter()
        deadline = t_start + self.config.optimization_time_limit_s

        current_rings = list(initial_rings)
        b0, p0 = rings_to_bundle_and_partition(self.plan, current_rings)
        c0, rep0 = evaluate_solution(self.plan, b0, p0, self.topology)

        best_rings = list(current_rings)
        best_bundle = b0
        best_partition = p0
        best_cost = c0
        best_report = rep0

        current_cost = c0

        destroy_ops = [
            destroy_highest_cost_ring,
            destroy_boundary_room,
            destroy_hub_relocation,
        ]

        iterations = 0
        improvements = 0
        restarts_done = 0

        temp = self.config.initial_temperature

        while time.perf_counter() < deadline:
            iterations += 1

            # Check temperature and restart if cooled down
            if temp < self.config.min_temperature:
                if restarts_done < self.config.restart_count:
                    restarts_done += 1
                    temp = self.config.initial_temperature / (1.5 ** restarts_done)
                    current_rings = list(best_rings)
                    current_cost = best_cost
                else:
                    break

            # 1. Select and apply destroy operator
            op_destroy = self.rng.choice(destroy_ops)
            sol_state = ALNSSolutionState(rings=list(current_rings), unassigned_rooms=set())

            if op_destroy == destroy_boundary_room:
                partial_state, _ = destroy_boundary_room(sol_state, self.plan, self.topology, self.rng)
            elif op_destroy == destroy_hub_relocation:
                partial_state, _ = destroy_hub_relocation(sol_state, self.rng)
            else:
                partial_state, _ = destroy_highest_cost_ring(sol_state)

            # 2. Repair
            if time.perf_counter() >= deadline:
                break
            repaired_state = repair_reassign_and_solve(partial_state, self.plan, self.topology, self.rng)

            if not repaired_state.rings or repaired_state.unassigned_rooms:
                continue

            # 3. Evaluate candidate
            cand_bundle, cand_partition = rings_to_bundle_and_partition(self.plan, repaired_state.rings)
            cand_cost, cand_report = evaluate_solution(self.plan, cand_bundle, cand_partition, self.topology)

            # 4. Check validity
            if not cand_report.is_valid:
                continue

            # 5. Acceptance criteria
            accepted = False
            # If strictly better lexicographically (or strictly lower J with same or fewer hubs)
            if cand_cost < best_cost:
                best_cost = cand_cost
                best_rings = list(repaired_state.rings)
                best_bundle = cand_bundle
                best_partition = cand_partition
                best_report = cand_report
                improvements += 1
                accepted = True
            elif cand_cost < current_cost:
                accepted = True
            elif cand_cost.H == current_cost.H:
                # Simulated annealing acceptance for equal hub counts
                delta_j = cand_cost.J - current_cost.J
                prob = math.exp(-delta_j / max(1e-4, temp))
                if self.rng.random() < prob:
                    accepted = True

            if accepted:
                current_rings = list(repaired_state.rings)
                current_cost = cand_cost

            # Cool temperature
            temp *= self.config.cooling_rate

        elapsed = time.perf_counter() - t_start

        return ALNSResult(
            best_rings=best_rings,
            bundle=best_bundle,
            partition=best_partition,
            cost=best_cost,
            report=best_report,
            iterations=iterations,
            improvements=improvements,
            elapsed_time_s=elapsed,
        )
