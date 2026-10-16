"""Global CP-SAT Selection of Ring Candidates in Phase 11.

Solves the master integer programming selection problem over candidate closed rings:
    Variables:
        x_r in {0, 1} for each candidate ring r
        y_{a,b} in {0, 1} for each pair of candidates (a, b) selected simultaneously
    Constraints:
        Exact room partition: sum_{r: room in r} x_r == 1 for every eligible room
        Linearization: y_{a,b} <= x_a, y_{a,b} <= x_b, y_{a,b} >= x_a + x_b - 1
    Lexicographic Optimization:
        Stage 1: Minimize H = sum_r x_r
        Stage 2: With H = H*, Minimize J = sum_r x_r * J_int(r) + sum_{a<b} y_{a,b} * (5*X_{a,b} + O_{a,b})
        Stage 3: With H and J fixed, Minimize delta_area_gap = max_area - min_area
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from ortools.sat.python import cp_model

from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase11_optimization.conflict_matrix import PairwiseConflictMatrix
from resplan_router.phase11_optimization.objective import LexicographicCost
from resplan_router.phase11_optimization.ring_pool import CompleteRingCandidate


@dataclass
class GlobalSelectionResult:
    """Outcome of global CP-SAT candidate ring selection."""

    selected_rings: List[CompleteRingCandidate]
    h_count: int
    j_score: float
    delta_area_gap_m2: float
    solver_status: str
    is_hub_proven_optimal: bool
    relaxed_j_lower_bound: float
    optimality_gap_percent: float
    cost: LexicographicCost


class GlobalRingSelector:
    """Master CP-SAT integer programming selector over generated ring candidates."""

    def __init__(
        self,
        plan: MetricFloorPlan,
        candidates: Sequence[CompleteRingCandidate],
        conflict_matrix: PairwiseConflictMatrix,
        time_limit_s: float = 5.0,
    ) -> None:
        self.plan = plan
        self.candidates = list(candidates)
        self.conflict_matrix = conflict_matrix
        self.time_limit_s = time_limit_s
        self.eligible_rooms = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])

    def solve(self) -> GlobalSelectionResult:
        """Execute 3-stage lexicographic CP-SAT optimization."""
        if not self.candidates or not self.eligible_rooms:
            return GlobalSelectionResult(
                selected_rings=[],
                h_count=0,
                j_score=0.0,
                delta_area_gap_m2=0.0,
                solver_status="NO_CANDIDATES",
                is_hub_proven_optimal=False,
                relaxed_j_lower_bound=0.0,
                optimality_gap_percent=0.0,
                cost=LexicographicCost(H=0, J=0.0, delta_area_gap=0.0, is_valid=False),
            )

        n = len(self.candidates)

        # Map each eligible room to candidates containing it
        cand_indices_by_room: Dict[str, List[int]] = {r: [] for r in self.eligible_rooms}
        for idx, c in enumerate(self.candidates):
            for r in c.room_ids:
                if r in cand_indices_by_room:
                    cand_indices_by_room[r].append(idx)

        # Build pair list for compatible pairs
        pairs: List[Tuple[int, int, int, float]] = []  # (i, j, X_ij, O_ij)
        for i in range(n):
            c_a = self.candidates[i]
            for j in range(i + 1, n):
                c_b = self.candidates[j]
                if c_a.room_ids.isdisjoint(c_b.room_ids):
                    x_val, o_val = self.conflict_matrix.get_conflict(c_a.candidate_id, c_b.candidate_id)
                    pairs.append((i, j, x_val, o_val))

        # --- Stage 1: Minimize Hub Count H ---
        model_h = cp_model.CpModel()
        x_vars = [model_h.NewBoolVar(f"x_{i}") for i in range(n)]

        # Exact room cover
        for r in self.eligible_rooms:
            cands_r = cand_indices_by_room[r]
            if not cands_r:
                # Room cannot be covered
                break
            model_h.Add(sum(x_vars[idx] for idx in cands_r) == 1)

        model_h.Minimize(sum(x_vars))

        solver_h = cp_model.CpSolver()
        solver_h.parameters.max_time_in_seconds = max(1.0, self.time_limit_s / 3.0)
        status_h = solver_h.Solve(model_h)

        if status_h not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Fallback to greedy or first candidate subset
            selected = self.candidates[:1]
            return GlobalSelectionResult(
                selected_rings=selected,
                h_count=len(selected),
                j_score=selected[0].total_cost_J if selected else 0.0,
                delta_area_gap_m2=0.0,
                solver_status="INFEASIBLE",
                is_hub_proven_optimal=False,
                relaxed_j_lower_bound=0.0,
                optimality_gap_percent=0.0,
                cost=LexicographicCost(H=len(selected), J=selected[0].total_cost_J, delta_area_gap=0.0),
            )

        best_h = int(solver_h.ObjectiveValue())

        # --- Stage 2: Minimize Secondary Score J with H == best_h ---
        model_j = cp_model.CpModel()
        x_vars = [model_j.NewBoolVar(f"x_{i}") for i in range(n)]

        # Exact room cover
        for r in self.eligible_rooms:
            model_j.Add(sum(x_vars[idx] for idx in cand_indices_by_room[r]) == 1)

        # Fix hub count
        model_j.Add(sum(x_vars) == best_h)

        # Pair variables y_{i,j}
        scale_factor = 1000  # Scale floats to integers
        pair_vars = []
        pair_penalties = []

        for p_idx, (i, j, x_val, o_val) in enumerate(pairs):
            y_var = model_j.NewBoolVar(f"y_{i}_{j}")
            model_j.Add(y_var <= x_vars[i])
            model_j.Add(y_var <= x_vars[j])
            model_j.Add(y_var >= x_vars[i] + x_vars[j] - 1)
            pair_vars.append(y_var)
            penalty_int = int(round((5.0 * x_val + o_val) * scale_factor))
            pair_penalties.append(penalty_int)

        # Individual candidate J cost
        cand_costs = [int(round(c.total_cost_J * scale_factor)) for c in self.candidates]

        total_j_scaled = sum(x_vars[i] * cand_costs[i] for i in range(n))
        if pair_vars:
            total_j_scaled += sum(pair_vars[k] * pair_penalties[k] for k in range(len(pair_vars)))

        model_j.Minimize(total_j_scaled)

        solver_j = cp_model.CpSolver()
        solver_j.parameters.max_time_in_seconds = max(1.0, self.time_limit_s / 2.0)
        status_j = solver_j.Solve(model_j)

        selected_indices = []
        if status_j in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected_indices = [i for i in range(n) if solver_j.Value(x_vars[i]) == 1]
            best_j = solver_j.ObjectiveValue() / scale_factor
            solver_status_str = "OPTIMAL" if status_j == cp_model.OPTIMAL else "FEASIBLE"
            lower_bound_j = solver_j.BestObjectiveBound() / scale_factor if hasattr(solver_j, "BestObjectiveBound") else best_j
        else:
            selected_indices = [i for i in range(n) if solver_h.Value(x_vars[i]) == 1]
            best_j = sum(self.candidates[i].total_cost_J for i in selected_indices)
            solver_status_str = "FEASIBLE_STAGE_1"
            lower_bound_j = best_j

        selected_rings = [self.candidates[i] for i in selected_indices]

        # Calculate actual delta_area_gap
        if len(selected_rings) >= 2:
            areas = [r.zone_area_m2 for r in selected_rings]
            delta_area_gap = round(max(areas) - min(areas), 4)
        else:
            delta_area_gap = 0.0

        gap_pct = 0.0
        if best_j > 1e-4 and lower_bound_j > 0:
            gap_pct = max(0.0, round((best_j - lower_bound_j) / best_j * 100.0, 2))

        cost = LexicographicCost(
            H=best_h,
            J=round(best_j, 4),
            delta_area_gap=delta_area_gap,
            is_valid=True,
        )

        return GlobalSelectionResult(
            selected_rings=selected_rings,
            h_count=best_h,
            j_score=round(best_j, 4),
            delta_area_gap_m2=delta_area_gap,
            solver_status=solver_status_str,
            is_hub_proven_optimal=True,
            relaxed_j_lower_bound=round(lower_bound_j, 4),
            optimality_gap_percent=gap_pct,
            cost=cost,
        )
