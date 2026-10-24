"""ResPlan Cable Ring Router: Autonomous Floor Plan Routing Pipeline.

Public Production API for Honeywell Cable Ring Optimization.
"""

from __future__ import annotations

from resplan_router.api import (
    BatchResult,
    OptimizationConfig,
    OptimizationResult,
    SolverStatus,
    ValidationResult,
    optimize_batch,
    optimize_plan,
    validate_solution,
)
from resplan_router.config import SystemConfig
from resplan_router.contracts import (
    MetricFloorPlan,
    Point2D,
    SynthesizedRingsBundle,
)

__version__ = "2.0.0"

__all__ = [
    "__version__",
    "optimize_plan",
    "optimize_batch",
    "validate_solution",
    "OptimizationConfig",
    "OptimizationResult",
    "BatchResult",
    "ValidationResult",
    "SolverStatus",
    "SystemConfig",
    "MetricFloorPlan",
    "Point2D",
    "SynthesizedRingsBundle",
]
