"""Matched-Budget Paired Evaluation Protocol (Agent 5).

Strict Experimental Protocol:
1. Matched Budgets:
   - Identical iteration limits (e.g. max_iterations=25).
   - Identical wall-clock timeouts (default 5.0 s per plan).
   - Deterministic matched random seeds: plan index i uses identical seed across all algorithms.
2. Independent Oracle Authority:
   - FloorPlanChecker at 1 mm tolerance (0.001 m) is the sole arbiter of validity and metrics.
   - Self-reported metrics from optimizers are completely ignored; ground truth is computed from
     raw polyline LineStrings.
3. Lexicographical Priority:
   Feasibility (G=0) > Minimum Hubs (H) > Minimum Routing Cost (J = L + 5X + O + 0.5B) > Delta A_gap.
4. Paired Comparisons:
   Direct head-to-head alignment: Delta H, Delta J, Speedup, Feasibility % for every instance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


class MatchupOutcome(str, Enum):
    """Lexicographical comparison outcome between Candidate and Champion Baseline."""
    WIN = "win"        # Candidate strictly superior under lexicographical hierarchy
    TIE = "tie"        # Identical feasibility, hub count, and routing cost (within 1e-4)
    LOSS = "loss"      # Champion strictly superior under lexicographical hierarchy


@dataclass
class MatchedBudgetProtocol:
    """Protocol settings ensuring fair, reproducible, matched-budget comparisons."""
    max_iterations: int = 25
    time_limit_seconds: float = 5.0
    base_random_seed: int = 42
    geometric_tolerance_m: float = 0.001  # 1 mm
    cost_tie_tolerance: float = 1e-4      # 0.0001 cost units
    max_hub_area_m2: float = 100.0
    max_nodes_per_hub: int = 5

    def get_seed_for_plan(self, plan_id: int) -> int:
        """Derive reproducible, unique seed for plan instance."""
        return (self.base_random_seed + int(plan_id) * 31337) % (2**31 - 1)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanRunMetrics:
    """Independently audited metrics for a single algorithm run on a single plan."""
    plan_id: int
    algorithm_name: str
    is_valid: bool
    h_count: int
    cost_J: float
    wire_length_L: float
    crossings_X: int
    overlaps_O: int
    bends_B: int
    area_gap_m2: float
    runtime_seconds: float
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PairedPlanComparison:
    """Paired head-to-head comparison record for a single plan."""
    plan_id: int
    complexity_tier: str
    champion: PlanRunMetrics
    candidate: PlanRunMetrics
    delta_h: int               # H_cand - H_champ (negative is improvement)
    delta_j: float             # J_champ - J_cand (positive is improvement)
    pct_j_improvement: float   # (J_champ - J_cand) / J_champ * 100
    runtime_speedup: float     # T_champ / T_cand (> 1.0 is speedup)
    outcome: MatchupOutcome
    both_valid: bool
    feasibility_maintained: bool  # True if candidate is valid whenever champion is valid

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value if hasattr(self.outcome, "value") else str(self.outcome)
        return d


def evaluate_head_to_head(
    champ: PlanRunMetrics,
    cand: PlanRunMetrics,
    cost_tie_tolerance: float = 1e-4,
) -> MatchupOutcome:
    """Evaluate candidate vs champion under strict 4-tier lexicographical hierarchy.

    Hierarchy:
    1. Feasibility (Valid > Invalid)
    2. Minimum Hub Count (H_cand < H_champ)
    3. Minimum Routing Cost (J_cand < J_champ - tol)
    4. Minimum Area Gap (Delta A_gap)
    """
    # 1. Feasibility
    if cand.is_valid and not champ.is_valid:
        return MatchupOutcome.WIN
    if champ.is_valid and not cand.is_valid:
        return MatchupOutcome.LOSS
    if not cand.is_valid and not champ.is_valid:
        # Both invalid: tie
        return MatchupOutcome.TIE

    # Both valid:
    # 2. Minimum Hub Count
    if cand.h_count < champ.h_count:
        return MatchupOutcome.WIN
    if cand.h_count > champ.h_count:
        return MatchupOutcome.LOSS

    # Equal Hub Count:
    # 3. Minimum Routing Cost J
    diff = champ.cost_J - cand.cost_J
    if diff > cost_tie_tolerance:
        return MatchupOutcome.WIN
    if diff < -cost_tie_tolerance:
        return MatchupOutcome.LOSS

    # Equal Routing Cost:
    # 4. Minimum Area Gap
    gap_diff = champ.area_gap_m2 - cand.area_gap_m2
    if gap_diff > cost_tie_tolerance:
        return MatchupOutcome.WIN
    if gap_diff < -cost_tie_tolerance:
        return MatchupOutcome.LOSS

    return MatchupOutcome.TIE


def build_paired_comparison(
    plan_id: int,
    complexity_tier: str,
    champ_metrics: PlanRunMetrics,
    cand_metrics: PlanRunMetrics,
    cost_tie_tolerance: float = 1e-4,
) -> PairedPlanComparison:
    """Construct paired comparison record with derived delta metrics."""
    delta_h = cand_metrics.h_count - champ_metrics.h_count
    delta_j = champ_metrics.cost_J - cand_metrics.cost_J

    if champ_metrics.cost_J > 1e-6:
        pct_j = (delta_j / champ_metrics.cost_J) * 100.0
    else:
        pct_j = 0.0

    if cand_metrics.runtime_seconds > 1e-6:
        speedup = champ_metrics.runtime_seconds / cand_metrics.runtime_seconds
    else:
        speedup = 1.0

    outcome = evaluate_head_to_head(champ_metrics, cand_metrics, cost_tie_tolerance)
    both_valid = champ_metrics.is_valid and cand_metrics.is_valid
    feasibility_maintained = cand_metrics.is_valid if champ_metrics.is_valid else True

    return PairedPlanComparison(
        plan_id=plan_id,
        complexity_tier=complexity_tier,
        champion=champ_metrics,
        candidate=cand_metrics,
        delta_h=delta_h,
        delta_j=round(delta_j, 4),
        pct_j_improvement=round(pct_j, 4),
        runtime_speedup=round(speedup, 4),
        outcome=outcome,
        both_valid=both_valid,
        feasibility_maintained=feasibility_maintained,
    )
