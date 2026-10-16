"""Lexicographic Objective Function and Solution Scoring for Phase 11.

Slide 8 & Challenge Rule Objective:
    1. First minimize: H = number of hubs
    2. Then, with H fixed, minimize: J = L + 5*X + O + 0.5*B
    3. Then, with H and J fixed, minimize: delta_area_gap = max(zone_area) - min(zone_area)

Hard constraints (capacity <= 5 devices & <= 100 m^2, clearance >= 0.50m, 1 ring per hub,
closed ring continuity, free-space containment) are never traded against the score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from resplan_router.contracts import (
    CompletedHubRing,
    EvaluationReport,
    HubZonePartition,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)
from resplan_router.phase6_output.checker import FloorPlanChecker


def compute_J(L: float, B: int, X: int, O: float) -> float:
    """Compute official challenge secondary score J = L + 5*X + O + 0.5*B."""
    return round(float(L) + 5.0 * int(X) + float(O) + 0.5 * int(B), 4)


@dataclass(frozen=True)
class LexicographicCost:
    """Immutable 3-tier lexicographic cost structure (H -> J -> delta_area_gap)."""

    H: int
    J: float
    delta_area_gap: float
    is_valid: bool = True

    def _cmp_tuple(self) -> Tuple[int, int, float, float]:
        # Invalid solutions are ranked strictly worse than any valid solution
        validity_rank = 0 if self.is_valid else 1
        return (validity_rank, self.H, round(self.J, 4), round(self.delta_area_gap, 4))

    def __lt__(self, other: LexicographicCost) -> bool:
        return self._cmp_tuple() < other._cmp_tuple()

    def __le__(self, other: LexicographicCost) -> bool:
        return self._cmp_tuple() <= other._cmp_tuple()

    def __gt__(self, other: LexicographicCost) -> bool:
        return self._cmp_tuple() > other._cmp_tuple()

    def __ge__(self, other: LexicographicCost) -> bool:
        return self._cmp_tuple() >= other._cmp_tuple()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, LexicographicCost):
            return False
        return self._cmp_tuple() == other._cmp_tuple()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "H": self.H,
            "J": self.J,
            "delta_area_gap_m2": self.delta_area_gap,
            "is_valid": self.is_valid,
        }


def evaluate_solution(
    plan: MetricFloorPlan,
    bundle: SynthesizedRingsBundle,
    partition: Optional[HubZonePartition] = None,
    topology: Optional[Any] = None,
) -> Tuple[LexicographicCost, EvaluationReport]:
    """Score a complete solution against all Slide 8 rules and compute lexicographic cost."""
    report = FloorPlanChecker.evaluate(plan=plan, raw_submission=bundle, topology=topology)

    h_count = report.hub_count
    j_score = report.total_cost_J
    area_gap = report.area_gap_m2

    cost = LexicographicCost(
        H=h_count,
        J=j_score,
        delta_area_gap=area_gap,
        is_valid=report.is_valid,
    )
    return cost, report
