"""Public API for ResPlan Cable Ring Router (Honeywell Cable Ring Optimization).

Provides a clean, stable, fully-typed production interface:
- optimize_plan(plan, config=None) -> OptimizationResult
- optimize_batch(plans, config=None, max_workers=4, checkpoint_path=None) -> BatchResult
- validate_solution(plan, solution, config=None) -> ValidationResult
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field

from resplan_router.config import SystemConfig
from resplan_router.contracts import (
    EvaluationReport,
    MetricFloorPlan,
    Point2D,
    SynthesizedRingsBundle,
)
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer
from resplan_router.optimizers.alns_linucb_v2 import LinUCB_ALNS_V2Optimizer
from resplan_router.optimizers.base import (
    BaseFloorPlanOptimizer,
    OptimizerResult,
)
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.optimizers.production_fallback import (
    ProductionFallbackMode,
    ProductionFallbackOptimizer,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import export_slide8_json
from resplan_router.phase6_output.pipeline import route_floor_plan

logger = logging.getLogger("resplan_router.api")


# =============================================================================
# ENUMS & DATA CONTRACTS
# =============================================================================


class SolverStatus(str, Enum):
    """Categorical outcome of floor plan optimization and validation."""

    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class OptimizationConfig(BaseModel):
    """Execution configuration for single-plan and batch optimization."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    system_config: SystemConfig = Field(
        default_factory=SystemConfig,
        description="Core capacity limits, clearance thresholds, and metric weights",
    )
    algorithm: str = Field(
        default="production",
        description="Algorithm choice: 'production', 'greedy', 'v1', 'v2', 'pipeline'",
    )
    time_limit_seconds: float = Field(
        default=10.0,
        ge=0.01,
        description="Maximum wall-clock execution time budget per floor plan",
    )
    random_seed: int = Field(
        default=42,
        description="Random seed for reproducible stochastic search moves",
    )
    max_iterations: int = Field(
        default=10,
        ge=1,
        description="Iteration limit for iterative metaheuristics (e.g. ALNS)",
    )
    fallback_mode: str = Field(
        default="on_demand",
        description="Fallback arbitration mode: 'on_demand', 'arbitrated', 'seeded'",
    )
    strict_clearance: bool = Field(
        default=True,
        description="Validate node clearance on parse; raise error if breached",
    )
    export_slide8: bool = Field(
        default=True,
        description="Automatically serialize Slide 8 competition JSON dictionary",
    )
    optimizer: Optional[Any] = Field(
        default=None,
        description="Explicit BaseFloorPlanOptimizer instance override",
    )


class OptimizationResult(BaseModel):
    """Structured result returned by single-plan optimization."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan_id: str = Field(description="Unique floor plan identifier")
    status: SolverStatus = Field(description="Solver outcome status")
    is_valid: bool = Field(description="True if all official competition rules pass")
    hub_count: int = Field(default=0, description="Total number of active hub rings")
    total_length_m: float = Field(default=0.0, description="Total cable length (L) in metres")
    total_bends: int = Field(default=0, description="Total 90/180-degree cable bends (B)")
    total_crossings: int = Field(default=0, description="Total cable crossings (X)")
    total_overlap_m: float = Field(default=0.0, description="Total collinear cable overlap (O)")
    total_cost_J: float = Field(default=0.0, description="Total composite competition cost J")
    area_gap_m2: float = Field(default=0.0, description="Area load gap between largest and smallest zone")
    solution: Optional[SynthesizedRingsBundle] = Field(
        default=None,
        description="Complete floor plan bundle of synthesized closed rings",
    )
    slide8_dict: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Official Slide 8 competition submission JSON representation",
    )
    rules_passed: Dict[str, bool] = Field(
        default_factory=dict,
        description="Rule 1 through Rule 6 pass/fail status",
    )
    violations: List[str] = Field(
        default_factory=list,
        description="Descriptions of any detected rule breaches",
    )
    runtime_s: float = Field(default=0.0, description="Wall-clock execution duration in seconds")
    algorithm: str = Field(default="", description="Name of optimizer algorithm employed")
    message: Optional[str] = Field(default=None, description="Informational message or failure diagnostic")
    diagnostic_info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Auxiliary metadata, arbitration trace, and search diagnostics",
    )

    @property
    def bundle(self) -> Optional[SynthesizedRingsBundle]:
        """Convenience alias for synthesized rings bundle."""
        return self.solution

    @property
    def h_count(self) -> int:
        """Convenience alias for hub count."""
        return self.hub_count

    @property
    def solver_status(self) -> SolverStatus:
        """Convenience alias for status."""
        return self.status

    def to_dict(self) -> Dict[str, Any]:
        """Export serialized dictionary with rounded numerical metrics."""
        return {
            "plan_id": self.plan_id,
            "status": self.status.value,
            "is_valid": self.is_valid,
            "hub_count": self.hub_count,
            "total_length_m": round(self.total_length_m, 4),
            "total_bends": self.total_bends,
            "total_crossings": self.total_crossings,
            "total_overlap_m": round(self.total_overlap_m, 4),
            "total_cost_J": round(self.total_cost_J, 4),
            "area_gap_m2": round(self.area_gap_m2, 4),
            "solution": self.solution.model_dump() if self.solution is not None else None,
            "slide8_dict": self.slide8_dict,
            "rules_passed": self.rules_passed,
            "violations": self.violations,
            "runtime_s": round(self.runtime_s, 4),
            "algorithm": self.algorithm,
            "message": self.message,
            "diagnostic_info": self.diagnostic_info,
        }

    def to_json(self, indent: int = 2) -> str:
        """Export JSON formatted string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save_slide8(self, output_path: Union[str, Path]) -> None:
        """Export Slide 8 JSON format to specified file path."""
        if self.slide8_dict is None:
            raise ValueError(f"No Slide 8 JSON submission available for plan {self.plan_id}")
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.slide8_dict, f, indent=2)


class ValidationResult(BaseModel):
    """Result of independent geometric and rule validation of a floor plan solution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan_id: str = Field(description="Unique floor plan identifier")
    is_valid: bool = Field(description="True if solution satisfies 100% of hard competition rules")
    status: SolverStatus = Field(description="FEASIBLE if valid, INFEASIBLE if violations exist, ERROR if check failed")
    rules_passed: Dict[str, bool] = Field(
        default_factory=dict,
        description="Slide 3 hard rule checklist status (Rule 1 through Rule 6)",
    )
    violations: List[str] = Field(
        default_factory=list,
        description="Exhaustive list of formatted rule violation messages",
    )
    hub_count: int = Field(default=0, description="Audited hub count")
    total_length_m: float = Field(default=0.0, description="Audited cable length L at 1 mm tolerance")
    total_crossings: int = Field(default=0, description="Audited cable crossings X")
    total_overlap_m: float = Field(default=0.0, description="Audited cable overlap O")
    total_bends: int = Field(default=0, description="Audited cable bends B")
    total_cost_J: float = Field(default=0.0, description="Official audited composite cost J")
    area_gap_m2: float = Field(default=0.0, description="Audited area load gap")
    message: Optional[str] = Field(default=None, description="Summary audit descriptor")
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Per-hub evaluated metrics and breakdown",
    )

    @property
    def h_count(self) -> int:
        """Convenience alias for hub count."""
        return self.hub_count

    def to_dict(self) -> Dict[str, Any]:
        """Export serialized dictionary with rounded numerical metrics."""
        return {
            "plan_id": self.plan_id,
            "is_valid": self.is_valid,
            "status": self.status.value,
            "rules_passed": self.rules_passed,
            "violations": self.violations,
            "hub_count": self.hub_count,
            "total_length_m": round(self.total_length_m, 4),
            "total_crossings": self.total_crossings,
            "total_overlap_m": round(self.total_overlap_m, 4),
            "total_bends": self.total_bends,
            "total_cost_J": round(self.total_cost_J, 4),
            "area_gap_m2": round(self.area_gap_m2, 4),
            "message": self.message,
            "details": self.details,
        }

    def to_json(self, indent: int = 2) -> str:
        """Export JSON formatted string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)


class BatchResult(BaseModel):
    """Aggregated results from batch optimization across multiple floor plans."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    total_plans: int = Field(default=0, description="Total number of plans dispatched")
    successful: int = Field(default=0, description="Number of plans successfully solved and validated")
    failed: int = Field(default=0, description="Number of plans that failed or produced invalid routes")
    results: List[OptimizationResult] = Field(
        default_factory=list,
        description="Ordered list of individual OptimizationResult instances",
    )
    total_cost_J: float = Field(default=0.0, description="Cumulative competition cost across valid plans")
    total_runtime_s: float = Field(default=0.0, description="Total wall-clock duration of batch execution")
    average_runtime_s: float = Field(default=0.0, description="Average runtime per plan")
    checkpoint_saved: Optional[str] = Field(
        default=None,
        description="Path to saved checkpoint JSON file if checkpointing was active",
    )
    summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="Statistical summary across plans",
    )

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, item: Union[int, str]) -> OptimizationResult:
        if isinstance(item, int):
            return self.results[item]
        elif isinstance(item, str):
            for res in self.results:
                if res.plan_id == item:
                    return res
            raise KeyError(f"Plan ID '{item}' not found in batch results.")
        raise TypeError(f"Invalid key type: {type(item)}")

    def get(self, plan_id: str, default: Any = None) -> Optional[OptimizationResult]:
        """Look up OptimizationResult by plan_id."""
        for res in self.results:
            if res.plan_id == plan_id:
                return res
        return default

    @property
    def results_by_plan_id(self) -> Dict[str, OptimizationResult]:
        """Mapping from plan_id to OptimizationResult."""
        return {r.plan_id: r for r in self.results}

    def to_dict(self) -> Dict[str, Any]:
        """Export serialized batch dictionary."""
        return {
            "total_plans": self.total_plans,
            "successful": self.successful,
            "failed": self.failed,
            "total_cost_J": round(self.total_cost_J, 4),
            "total_runtime_s": round(self.total_runtime_s, 4),
            "average_runtime_s": round(self.average_runtime_s, 4),
            "checkpoint_saved": self.checkpoint_saved,
            "summary": self.summary,
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        """Export JSON formatted string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)


# =============================================================================
# HELPER NORMALIZATION UTILITIES
# =============================================================================


def _coerce_config(
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]],
) -> OptimizationConfig:
    """Normalize user-provided config argument into an OptimizationConfig instance."""
    if config is None:
        return OptimizationConfig()
    if isinstance(config, OptimizationConfig):
        return config
    if isinstance(config, SystemConfig):
        return OptimizationConfig(system_config=config)
    if isinstance(config, dict):
        # Extract system_config overrides if present
        sys_kwargs: Dict[str, Any] = {}
        opt_kwargs: Dict[str, Any] = {}
        sys_field_names = set(SystemConfig.model_fields.keys())
        opt_field_names = set(OptimizationConfig.model_fields.keys())

        for k, v in config.items():
            if k in opt_field_names and k != "system_config":
                opt_kwargs[k] = v
            elif k in sys_field_names:
                sys_kwargs[k] = v
            elif k == "system_config":
                if isinstance(v, SystemConfig):
                    opt_kwargs["system_config"] = v
                elif isinstance(v, dict):
                    sys_kwargs.update(v)

        if "system_config" not in opt_kwargs and sys_kwargs:
            opt_kwargs["system_config"] = SystemConfig(**sys_kwargs)

        return OptimizationConfig(**opt_kwargs)

    raise TypeError(f"Unsupported config type: {type(config).__name__}")


def _normalize_floor_plan(
    plan: Union[MetricFloorPlan, Dict[str, Any], str, Path, int],
    strict_clearance: bool = True,
) -> MetricFloorPlan:
    """Parse or retrieve a canonical MetricFloorPlan from any supported format."""
    if isinstance(plan, MetricFloorPlan):
        return plan

    if isinstance(plan, int):
        repo = DatasetRepository.get_instance()
        return repo.get_floor_plan(plan)

    if isinstance(plan, (str, Path)):
        # Check if numeric string referring to DatasetRepository index
        str_val = str(plan).strip()
        if str_val.isdigit():
            repo = DatasetRepository.get_instance()
            try:
                return repo.get_floor_plan(int(str_val))
            except Exception:
                pass

        p = Path(plan)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            return parse_floor_plan(raw_data, strict_clearance=strict_clearance)

        # Check if raw JSON string
        if str_val.startswith("{") and str_val.endswith("}"):
            raw_data = json.loads(str_val)
            return parse_floor_plan(raw_data, strict_clearance=strict_clearance)

        raise FileNotFoundError(f"Floor plan file or plan ID not found: {plan}")

    if isinstance(plan, dict):
        # Check if it already matches MetricFloorPlan schema
        if "plan_id" in plan and "rooms" in plan and "free_space_wkt" in plan:
            try:
                return MetricFloorPlan.model_validate(plan)
            except Exception:
                pass
        return parse_floor_plan(plan, strict_clearance=strict_clearance)

    raise TypeError(f"Unsupported floor plan input type: {type(plan).__name__}")


def _resolve_optimizer(config: OptimizationConfig) -> BaseFloorPlanOptimizer:
    """Construct or return the configured optimizer instance."""
    if config.optimizer is not None:
        return config.optimizer

    algo = (config.algorithm or "production").strip().lower()

    if algo in ("greedy", "baseline", "greedy_baseline"):
        return GreedyBaselineOptimizer(name="GreedyBaseline")

    if algo in ("v1", "champion", "alns_v1", "linucb_v1"):
        return ALNSLinUCBOptimizer(
            name="LinUCB_ALNS_v1_champion",
            max_iterations=config.max_iterations,
        )

    if algo in ("v2", "candidate", "alns_v2", "linucb_v2"):
        return LinUCB_ALNS_V2Optimizer(
            name="LinUCB_ALNS_v2",
            max_iterations=config.max_iterations,
        )

    if algo in ("production", "fallback", "default", "guarded"):
        mode_str = config.fallback_mode.strip().lower()
        if mode_str == "arbitrated":
            fb_mode = ProductionFallbackMode.ARBITRATED
        elif mode_str == "seeded":
            fb_mode = ProductionFallbackMode.SEEDED
        else:
            fb_mode = ProductionFallbackMode.ON_DEMAND

        return ProductionFallbackOptimizer(
            name="ProductionGuarded_LinUCB_v2",
            mode=fb_mode,
            v2_iterations=config.max_iterations,
            v1_iterations=config.max_iterations,
            tolerance_m=config.system_config.checker_tolerance_m,
        )

    logger.warning("Unrecognized optimizer algorithm '%s', defaulting to production fallback.", algo)
    return ProductionFallbackOptimizer(
        name="ProductionGuarded_LinUCB_v2",
        mode=ProductionFallbackMode.ON_DEMAND,
        v2_iterations=config.max_iterations,
        v1_iterations=config.max_iterations,
    )


# =============================================================================
# PUBLIC API FUNCTIONS
# =============================================================================


def optimize_plan(
    plan: Union[MetricFloorPlan, Dict[str, Any], str, Path, int],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
) -> OptimizationResult:
    """Optimize cable ring routes for a single architectural floor plan.

    Args:
        plan: Floor plan input. Can be:
            - A pre-parsed MetricFloorPlan instance.
            - A dictionary containing raw floor plan geometry.
            - A path (str or Path) to a floor plan JSON file.
            - An integer or numeric string representing a canonical plan ID in ResPlan.pkl.
        config: Optional configuration settings (OptimizationConfig, SystemConfig, or dict).

    Returns:
        OptimizationResult with complete solution, competition metrics, validation status,
        and Slide 8 export.
    """
    t_start = time.perf_counter()
    opt_config = _coerce_config(config)

    # 1. Ingestion / Normalization
    try:
        metric_plan = _normalize_floor_plan(plan, strict_clearance=opt_config.strict_clearance)
    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        plan_id_str = getattr(plan, "plan_id", str(plan)) if not isinstance(plan, dict) else plan.get("plan_id", "unknown")
        return OptimizationResult(
            plan_id=str(plan_id_str),
            status=SolverStatus.ERROR,
            is_valid=False,
            runtime_s=elapsed,
            algorithm=opt_config.algorithm,
            message=f"Floor plan normalization error: {exc}",
            violations=[f"Plan parsing failed: {exc}"],
        )

    # 2. Pipeline execution path (Phases 1-6 determinism)
    algo_name = (opt_config.algorithm or "").strip().lower()
    if algo_name == "pipeline":
        try:
            raw_input: Any = plan
            if isinstance(plan, MetricFloorPlan):
                try:
                    repo = DatasetRepository.get_instance()
                    raw_input = repo.get_record(metric_plan.plan_id)
                except Exception:
                    raw_input = None

            if raw_input is not None and (
                isinstance(raw_input, (str, Path))
                or (isinstance(raw_input, dict) and "area" in raw_input)
            ):
                slide8_dict, evaluation, bundle = route_floor_plan(
                    raw_input,
                    config=opt_config.system_config,
                    strict_clearance=opt_config.strict_clearance,
                )
                elapsed = time.perf_counter() - t_start
                status = SolverStatus.FEASIBLE if evaluation.is_valid else SolverStatus.INFEASIBLE
                violations = [f"[Rule {v.rule_number}] {v.rule_name}: {v.message}" for v in evaluation.violations]

                return OptimizationResult(
                    plan_id=metric_plan.plan_id,
                    status=status,
                    is_valid=evaluation.is_valid,
                    hub_count=evaluation.hub_count,
                    total_length_m=evaluation.total_length_m,
                    total_bends=evaluation.total_bends,
                    total_crossings=evaluation.total_crossings,
                    total_overlap_m=evaluation.total_overlap_m,
                    total_cost_J=evaluation.total_cost_J,
                    area_gap_m2=evaluation.area_gap_m2,
                    solution=bundle,
                    slide8_dict=slide8_dict,
                    rules_passed=evaluation.rules_passed,
                    violations=violations,
                    runtime_s=elapsed,
                    algorithm="pipeline",
                    message="Pipeline solved successfully" if evaluation.is_valid else "Pipeline solution has violations",
                )
            else:
                base_opt = GreedyBaselineOptimizer(name="PipelineBaseline")
                opt_res = base_opt.optimize(metric_plan)
                elapsed = time.perf_counter() - t_start
                slide8_dict = None
                if opt_res.bundle is not None:
                    try:
                        slide8_dict = export_slide8_json(metric_plan, opt_res.bundle, opt_res.partition)
                    except Exception:
                        pass
                return OptimizationResult(
                    plan_id=opt_res.plan_id,
                    status=SolverStatus.FEASIBLE if opt_res.is_valid else SolverStatus.INFEASIBLE,
                    is_valid=opt_res.is_valid,
                    hub_count=opt_res.h_count,
                    total_length_m=opt_res.total_length_m,
                    total_bends=opt_res.total_bends,
                    total_crossings=opt_res.total_crossings,
                    total_overlap_m=opt_res.total_overlap_m,
                    total_cost_J=opt_res.total_cost_J,
                    area_gap_m2=opt_res.area_gap_m2,
                    solution=opt_res.bundle,
                    slide8_dict=slide8_dict,
                    rules_passed=opt_res.rules_passed,
                    violations=opt_res.violations,
                    runtime_s=elapsed,
                    algorithm="pipeline",
                    message="Pipeline solved successfully" if opt_res.is_valid else "Pipeline solution has violations",
                )
        except Exception as exc:
            elapsed = time.perf_counter() - t_start
            return OptimizationResult(
                plan_id=metric_plan.plan_id,
                status=SolverStatus.ERROR,
                is_valid=False,
                runtime_s=elapsed,
                algorithm="pipeline",
                message=f"Pipeline optimization exception: {exc}",
                violations=[str(exc)],
            )

    # 3. Solver dispatch
    optimizer = _resolve_optimizer(opt_config)
    try:
        opt_res: OptimizerResult = optimizer.optimize(
            metric_plan,
            time_limit_seconds=opt_config.time_limit_seconds,
            random_seed=opt_config.random_seed,
        )
        elapsed = time.perf_counter() - t_start
    except Exception as exc:
        elapsed = time.perf_counter() - t_start
        return OptimizationResult(
            plan_id=metric_plan.plan_id,
            status=SolverStatus.ERROR,
            is_valid=False,
            runtime_s=elapsed,
            algorithm=getattr(optimizer, "name", algo_name),
            message=f"Optimizer exception: {exc}",
            violations=[str(exc)],
        )

    # 4. Map solver status
    raw_status = (opt_res.solver_status or "").upper()
    if raw_status == "OPTIMAL":
        status = SolverStatus.OPTIMAL
    elif raw_status == "FEASIBLE":
        status = SolverStatus.FEASIBLE
    elif raw_status == "TIMEOUT":
        status = SolverStatus.TIMEOUT
    elif raw_status == "INFEASIBLE":
        status = SolverStatus.INFEASIBLE
    elif raw_status == "ERROR":
        status = SolverStatus.ERROR
    else:
        status = SolverStatus.FEASIBLE if opt_res.is_valid else SolverStatus.INFEASIBLE

    # 5. Export Slide 8 JSON representation if enabled
    slide8 = None
    if opt_config.export_slide8 and opt_res.bundle is not None:
        try:
            slide8 = export_slide8_json(metric_plan, opt_res.bundle, opt_res.partition)
        except Exception as exc:
            logger.warning("Could not export Slide 8 JSON for plan %s: %s", metric_plan.plan_id, exc)

    msg = "Optimization succeeded (valid)" if opt_res.is_valid else f"Optimization infeasible: {len(opt_res.violations)} violations"

    return OptimizationResult(
        plan_id=opt_res.plan_id,
        status=status,
        is_valid=opt_res.is_valid,
        hub_count=opt_res.h_count,
        total_length_m=opt_res.total_length_m,
        total_bends=opt_res.total_bends,
        total_crossings=opt_res.total_crossings,
        total_overlap_m=opt_res.total_overlap_m,
        total_cost_J=opt_res.total_cost_J,
        area_gap_m2=opt_res.area_gap_m2,
        solution=opt_res.bundle,
        slide8_dict=slide8,
        rules_passed=opt_res.rules_passed,
        violations=opt_res.violations,
        runtime_s=opt_res.runtime_s if opt_res.runtime_s > 0 else elapsed,
        algorithm=opt_res.algorithm_name,
        message=msg,
        diagnostic_info=opt_res.diagnostic_info,
    )


def validate_solution(
    plan: Union[MetricFloorPlan, Dict[str, Any], str, Path, int],
    solution: Union[SynthesizedRingsBundle, OptimizationResult, Dict[str, Any], str, Path],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
) -> ValidationResult:
    """Validate a candidate routing solution against Honeywell's 6 Hard Rules and competition metrics.

    Audits with independent FloorPlanChecker at 1 mm geometric tolerance.

    Args:
        plan: Reference MetricFloorPlan, raw dictionary, or file path.
        solution: SynthesizedRingsBundle, OptimizationResult, Slide 8 dict, or file path.
        config: Optional configuration specifying tolerances and capacity limits.

    Returns:
        ValidationResult detailing pass/fail status for all 6 Hard Rules and audited metrics.
    """
    opt_config = _coerce_config(config)
    sys_config = opt_config.system_config

    # 1. Normalize reference floor plan
    try:
        metric_plan = _normalize_floor_plan(plan, strict_clearance=opt_config.strict_clearance)
    except Exception as exc:
        plan_id_str = getattr(plan, "plan_id", str(plan)) if not isinstance(plan, dict) else plan.get("plan_id", "unknown")
        return ValidationResult(
            plan_id=str(plan_id_str),
            is_valid=False,
            status=SolverStatus.ERROR,
            message=f"Failed to normalize floor plan: {exc}",
            violations=[f"Reference plan parsing error: {exc}"],
        )

    # 2. Normalize candidate solution
    if solution is None:
        return ValidationResult(
            plan_id=metric_plan.plan_id,
            is_valid=False,
            status=SolverStatus.INFEASIBLE,
            message="Candidate solution is None",
            violations=["No solution provided for validation"],
        )

    submission_obj: Any = solution
    if isinstance(solution, OptimizationResult):
        if solution.solution is not None:
            submission_obj = solution.solution
        elif solution.slide8_dict is not None:
            submission_obj = solution.slide8_dict
        else:
            return ValidationResult(
                plan_id=metric_plan.plan_id,
                is_valid=False,
                status=SolverStatus.INFEASIBLE,
                message="OptimizationResult does not contain a solution bundle or slide8_dict",
                violations=["Empty solution in OptimizationResult"],
            )
    elif isinstance(solution, (str, Path)):
        p = Path(solution)
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                submission_obj = json.load(f)
        else:
            str_val = str(solution).strip()
            if str_val.startswith("{") and str_val.endswith("}"):
                submission_obj = json.loads(str_val)
            else:
                return ValidationResult(
                    plan_id=metric_plan.plan_id,
                    is_valid=False,
                    status=SolverStatus.ERROR,
                    message=f"Solution file not found and input is not valid JSON string: {solution}",
                    violations=[f"Unresolvable solution string or path: {solution}"],
                )

    # 3. Execute authoritative FloorPlanChecker audit
    try:
        report: EvaluationReport = FloorPlanChecker.evaluate(
            plan=metric_plan,
            raw_submission=submission_obj,
            tolerance_m=sys_config.checker_tolerance_m,
            max_area_m2=sys_config.max_area_m2,
            max_nodes_per_hub=sys_config.max_nodes_per_hub,
        )
    except Exception as exc:
        return ValidationResult(
            plan_id=metric_plan.plan_id,
            is_valid=False,
            status=SolverStatus.ERROR,
            message=f"Independent checker evaluation failed: {exc}",
            violations=[f"Checker exception: {exc}"],
        )

    status = SolverStatus.FEASIBLE if report.is_valid else SolverStatus.INFEASIBLE
    violations = [f"[Rule {v.rule_number}] {v.rule_name}: {v.message}" for v in report.violations]
    msg = (
        "Solution valid (100% rules passed)"
        if report.is_valid
        else f"Solution invalid ({len(violations)} rule violations detected)"
    )

    per_hub_details = [m.model_dump() for m in report.hub_metrics]

    return ValidationResult(
        plan_id=metric_plan.plan_id,
        is_valid=report.is_valid,
        status=status,
        rules_passed=report.rules_passed,
        violations=violations,
        hub_count=report.hub_count,
        total_length_m=report.total_length_m,
        total_crossings=report.total_crossings,
        total_overlap_m=report.total_overlap_m,
        total_bends=report.total_bends,
        total_cost_J=report.total_cost_J,
        area_gap_m2=report.area_gap_m2,
        message=msg,
        details={"hub_metrics": per_hub_details},
    )


def _write_checkpoint(
    checkpoint_path: Path,
    total_plans: int,
    results: List[OptimizationResult],
) -> None:
    """Safely write batch progress to an atomic JSON checkpoint file."""
    checkpoint_data = {
        "timestamp": time.time(),
        "total_plans": total_plans,
        "completed": len(results),
        "successful": sum(1 for r in results if r.is_valid),
        "results": [r.to_dict() for r in results],
    }
    tmp_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, indent=2)
    tmp_path.replace(checkpoint_path)


def optimize_batch(
    plans: Sequence[Union[MetricFloorPlan, Dict[str, Any], str, Path, int]],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
    max_workers: int = 4,
    checkpoint_path: Optional[Union[str, Path]] = None,
) -> BatchResult:
    """Optimize a batch of floor plans concurrently with checkpointing and error isolation.

    Args:
        plans: Iterable sequence of floor plan inputs.
        config: Optional optimization configuration.
        max_workers: Maximum concurrent worker threads (defaults to 4). If <= 1, runs sequentially.
        checkpoint_path: Optional file path to persist intermediate results for crash recovery.

    Returns:
        BatchResult containing ordered individual OptimizationResults and aggregated competition metrics.
    """
    t_start = time.perf_counter()
    plans_list = list(plans)
    total_plans = len(plans_list)

    if total_plans == 0:
        return BatchResult(
            total_plans=0,
            successful=0,
            failed=0,
            results=[],
            total_cost_J=0.0,
            total_runtime_s=0.0,
            average_runtime_s=0.0,
            summary={"status": "Empty batch provided"},
        )

    ck_path = Path(checkpoint_path) if checkpoint_path is not None else None
    results: List[OptimizationResult] = [None] * total_plans  # type: ignore

    if max_workers <= 1 or total_plans == 1:
        # Sequential execution
        for idx, p in enumerate(plans_list):
            res = optimize_plan(p, config=config)
            results[idx] = res
            if ck_path is not None:
                _write_checkpoint(ck_path, total_plans, [r for r in results if r is not None])
    else:
        # Concurrent execution preserving input order
        worker_count = max(1, min(max_workers, total_plans))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_idx = {
                executor.submit(optimize_plan, p, config): idx
                for idx, p in enumerate(plans_list)
            }
            completed_so_far: List[OptimizationResult] = []
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res = future.result()
                except Exception as exc:
                    plan_item = plans_list[idx]
                    p_id = getattr(plan_item, "plan_id", str(plan_item)) if not isinstance(plan_item, dict) else plan_item.get("plan_id", "unknown")
                    res = OptimizationResult(
                        plan_id=str(p_id),
                        status=SolverStatus.ERROR,
                        is_valid=False,
                        message=f"Batch worker exception: {exc}",
                        violations=[str(exc)],
                    )
                results[idx] = res
                completed_so_far.append(res)
                if ck_path is not None:
                    _write_checkpoint(ck_path, total_plans, completed_so_far)

    elapsed = time.perf_counter() - t_start
    successful = sum(1 for r in results if r.is_valid)
    failed = total_plans - successful
    total_j = sum(r.total_cost_J for r in results if r.is_valid)
    avg_runtime = elapsed / total_plans if total_plans > 0 else 0.0

    summary_stats = {
        "success_rate": round(successful / total_plans, 4) if total_plans > 0 else 0.0,
        "valid_plans": [r.plan_id for r in results if r.is_valid],
        "invalid_plans": [r.plan_id for r in results if not r.is_valid],
    }

    return BatchResult(
        total_plans=total_plans,
        successful=successful,
        failed=failed,
        results=results,
        total_cost_J=total_j,
        total_runtime_s=elapsed,
        average_runtime_s=avg_runtime,
        checkpoint_saved=str(ck_path) if ck_path is not None else None,
        summary=summary_stats,
    )
