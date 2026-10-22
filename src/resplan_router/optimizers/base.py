"""Unified Base Interface and Result Contract for Floor Plan Cable Optimizers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional

from resplan_router.contracts import (
    EvaluationReport,
    HubZonePartition,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)
from resplan_router.phase6_output.checker import FloorPlanChecker


@dataclass
class OptimizerResult:
    """Standardized result returned by all floor plan optimizers."""

    algorithm_name: str
    plan_id: str
    bundle: Optional[SynthesizedRingsBundle]
    partition: Optional[HubZonePartition]
    h_count: int
    total_length_m: float
    total_bends: int
    total_crossings: int
    total_overlap_m: float
    total_cost_J: float
    area_gap_m2: float
    is_valid: bool
    solver_status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMEOUT", "ERROR"
    runtime_s: float
    diagnostic_info: Dict[str, Any] = field(default_factory=dict)
    rules_passed: Dict[str, bool] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Convert result to serializable dictionary for benchmark logging."""
        return {
            "algorithm_name": self.algorithm_name,
            "plan_id": self.plan_id,
            "h_count": self.h_count,
            "total_length_m": round(self.total_length_m, 4),
            "total_bends": self.total_bends,
            "total_crossings": self.total_crossings,
            "total_overlap_m": round(self.total_overlap_m, 4),
            "total_cost_J": round(self.total_cost_J, 4),
            "area_gap_m2": round(self.area_gap_m2, 4),
            "is_valid": self.is_valid,
            "solver_status": self.solver_status,
            "runtime_s": round(self.runtime_s, 4),
            "rules_passed": self.rules_passed,
            "violations": self.violations,
            "diagnostic_info": self.diagnostic_info,
        }


def create_optimizer_result(
    algorithm_name: str,
    plan: MetricFloorPlan,
    bundle: Optional[SynthesizedRingsBundle],
    partition: Optional[HubZonePartition],
    solver_status: str,
    runtime_s: float,
    diagnostic_info: Optional[Dict[str, Any]] = None,
) -> OptimizerResult:
    """Create a strictly validated OptimizerResult using the independent FloorPlanChecker.

    Guarantees that every optimizer's output is scored using the identical
    official geometric checker rules (1 mm tolerance, J = L + 5X + O + 0.5B).
    """
    diag = diagnostic_info or {}
    if bundle is None:
        return OptimizerResult(
            algorithm_name=algorithm_name,
            plan_id=plan.plan_id,
            bundle=None,
            partition=partition,
            h_count=0,
            total_length_m=0.0,
            total_bends=0,
            total_crossings=0,
            total_overlap_m=0.0,
            total_cost_J=float("inf"),
            area_gap_m2=0.0,
            is_valid=False,
            solver_status=solver_status,
            runtime_s=runtime_s,
            diagnostic_info=diag,
            rules_passed={f"Rule {i}": False for i in range(1, 7)},
            violations=["No bundle generated"],
        )

    # Independent audit
    report: EvaluationReport = FloorPlanChecker.evaluate(plan, bundle)

    violations = [f"Rule {v.rule_number} ({v.rule_name}): {v.message}" for v in report.violations]

    return OptimizerResult(
        algorithm_name=algorithm_name,
        plan_id=plan.plan_id,
        bundle=bundle,
        partition=partition,
        h_count=report.hub_count,
        total_length_m=report.total_length_m,
        total_bends=report.total_bends,
        total_crossings=report.total_crossings,
        total_overlap_m=report.total_overlap_m,
        total_cost_J=report.total_cost_J,
        area_gap_m2=report.area_gap_m2,
        is_valid=report.is_valid,
        solver_status=solver_status,
        runtime_s=runtime_s,
        diagnostic_info=diag,
        rules_passed=report.rules_passed,
        violations=violations,
    )


class BaseFloorPlanOptimizer(ABC):
    """Abstract base class for all floor plan cable optimizers."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Run optimization and return standard validated OptimizerResult."""
        pass
