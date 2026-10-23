"""Independent Oracle Evaluator for Post-Champion Benchmark (Agent 5).

Guarantees:
1. Authority: FloorPlanChecker at 1 mm tolerance is the sole judge of feasibility and metrics.
2. Matched Execution: Champion and candidate receive identical random seed and time budget.
3. Metric Extraction: Authoritatively measures L, X, O, B, J, H, Area Gap, and Runtime.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase6_output.checker import FloorPlanChecker

from benchmarks.experimental.protocol import (
    MatchedBudgetProtocol,
    PairedPlanComparison,
    PlanRunMetrics,
    build_paired_comparison,
)

logger = logging.getLogger(__name__)


def evaluate_single_optimizer(
    optimizer: BaseFloorPlanOptimizer,
    floor_plan: MetricFloorPlan,
    plan_id: int,
    protocol: MatchedBudgetProtocol,
) -> PlanRunMetrics:
    """Execute optimizer on plan under protocol and evaluate with FloorPlanChecker."""
    seed = protocol.get_seed_for_plan(plan_id)
    t0 = time.perf_counter()

    try:
        opt_res: OptimizerResult = optimizer.optimize(
            floor_plan,
            time_limit_seconds=protocol.time_limit_seconds,
            random_seed=seed,
        )
        runtime = time.perf_counter() - t0
    except Exception as exc:
        runtime = time.perf_counter() - t0
        logger.warning("Optimizer %s failed on plan %d: %s", optimizer.name, plan_id, exc)
        return PlanRunMetrics(
            plan_id=plan_id,
            algorithm_name=optimizer.name,
            is_valid=False,
            h_count=999,
            cost_J=999999.0,
            wire_length_L=999999.0,
            crossings_X=999,
            overlaps_O=999,
            bends_B=999,
            area_gap_m2=999999.0,
            runtime_seconds=round(runtime, 4),
            violations=[f"Unhandled exception: {str(exc)}"],
        )

    # If result lacks a bundle, it is invalid
    if not opt_res.is_valid or opt_res.bundle is None:
        violations = [v.rule_name if hasattr(v, "rule_name") else str(v) for v in opt_res.violations] if opt_res.violations else ["Invalid submission bundle"]
        return PlanRunMetrics(
            plan_id=plan_id,
            algorithm_name=optimizer.name,
            is_valid=False,
            h_count=opt_res.h_count if opt_res.h_count > 0 else 999,
            cost_J=round(opt_res.total_cost_J, 4) if opt_res.total_cost_J > 0 else 999999.0,
            wire_length_L=round(opt_res.total_length_m, 4) if opt_res.total_length_m > 0 else 999999.0,
            crossings_X=opt_res.total_crossings,
            overlaps_O=int(round(opt_res.total_overlap_m)),
            bends_B=opt_res.total_bends,
            area_gap_m2=round(opt_res.area_gap_m2, 4),
            runtime_seconds=round(runtime, 4),
            violations=violations,
        )

    # Independent Oracle Evaluation with FloorPlanChecker at 1 mm tolerance
    try:
        report = FloorPlanChecker.evaluate(
            floor_plan,
            opt_res.bundle,
            tolerance_m=protocol.geometric_tolerance_m,
            max_area_m2=protocol.max_hub_area_m2,
            max_nodes_per_hub=protocol.max_nodes_per_hub,
        )
        violations = [f"{v.rule_name}: {v.message}" if hasattr(v, "rule_name") else str(v) for v in report.violations]
        return PlanRunMetrics(
            plan_id=plan_id,
            algorithm_name=optimizer.name,
            is_valid=report.is_valid,
            h_count=report.hub_count,
            cost_J=round(report.total_cost_J, 4),
            wire_length_L=round(report.total_length_m, 4),
            crossings_X=report.total_crossings,
            overlaps_O=int(round(report.total_overlap_m)),
            bends_B=report.total_bends,
            area_gap_m2=round(report.area_gap_m2, 4),
            runtime_seconds=round(runtime, 4),
            violations=violations,
        )
    except Exception as exc:
        logger.error("FloorPlanChecker exception on plan %d: %s", plan_id, exc)
        violations = [v.rule_name if hasattr(v, "rule_name") else str(v) for v in opt_res.violations] if opt_res.violations else [f"Checker crashed: {str(exc)}"]
        return PlanRunMetrics(
            plan_id=plan_id,
            algorithm_name=optimizer.name,
            is_valid=False,
            h_count=opt_res.h_count,
            cost_J=round(opt_res.total_cost_J, 4),
            wire_length_L=round(opt_res.total_length_m, 4),
            crossings_X=opt_res.total_crossings,
            overlaps_O=int(round(opt_res.total_overlap_m)),
            bends_B=opt_res.total_bends,
            area_gap_m2=round(opt_res.area_gap_m2, 4),
            runtime_seconds=round(runtime, 4),
            violations=violations,
        )


def evaluate_paired_plan(
    plan_id: int,
    complexity_tier: str,
    champ_opt: BaseFloorPlanOptimizer,
    cand_opt: BaseFloorPlanOptimizer,
    protocol: Optional[MatchedBudgetProtocol] = None,
) -> PairedPlanComparison:
    """Execute matched-budget paired comparison for a single plan."""
    if protocol is None:
        protocol = MatchedBudgetProtocol()

    repo = DatasetRepository.get_instance()
    plan = repo.get_floor_plan(plan_id)

    champ_metrics = evaluate_single_optimizer(champ_opt, plan, plan_id, protocol)
    cand_metrics = evaluate_single_optimizer(cand_opt, plan, plan_id, protocol)

    return build_paired_comparison(
        plan_id=plan_id,
        complexity_tier=complexity_tier,
        champ_metrics=champ_metrics,
        cand_metrics=cand_metrics,
        cost_tie_tolerance=protocol.cost_tie_tolerance,
    )
