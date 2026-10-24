"""Floor plan optimizers package."""

from typing import Any

from resplan_router.optimizers.base import (
    BaseFloorPlanOptimizer,
    OptimizerResult,
    create_optimizer_result,
)
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.optimizers.bitmask_dp import BitmaskDPOptimizer
from resplan_router.optimizers.cpsat_hanan import CPSATHananOptimizer
from resplan_router.optimizers.cpsat_multipath import CPSATMultiPathOptimizer
from resplan_router.optimizers.alns_optimizer import ALNSOptimizer
from resplan_router.optimizers.genetic_optimizer import GeneticOptimizer
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer

__all__ = [
    "BaseFloorPlanOptimizer",
    "OptimizerResult",
    "create_optimizer_result",
    "GreedyBaselineOptimizer",
    "BitmaskDPOptimizer",
    "CPSATHananOptimizer",
    "CPSATMultiPathOptimizer",
    "ALNSOptimizer",
    "GeneticOptimizer",
    "PortfolioHybridOptimizer",
    "ALNSLinUCBOptimizer",
    "LinUCB_ALNS_V2Optimizer",
    "ALNSLinUCBV2Optimizer",
    "ALNSLinUCB_V2Optimizer",
    "FallbackReason",
    "ProductionFallbackMode",
    "ProductionFallbackOptimizer",
    "evaluate_lexicographical_preference",
]


def __getattr__(name: str) -> Any:
    if name in ("LinUCB_ALNS_V2Optimizer", "ALNSLinUCBV2Optimizer", "ALNSLinUCB_V2Optimizer"):
        from resplan_router.optimizers.alns_linucb_v2 import (
            LinUCB_ALNS_V2Optimizer,
            ALNSLinUCBV2Optimizer,
            ALNSLinUCB_V2Optimizer,
        )
        mapping = {
            "LinUCB_ALNS_V2Optimizer": LinUCB_ALNS_V2Optimizer,
            "ALNSLinUCBV2Optimizer": ALNSLinUCBV2Optimizer,
            "ALNSLinUCB_V2Optimizer": ALNSLinUCB_V2Optimizer,
        }
        return mapping[name]
    if name in ("FallbackReason", "ProductionFallbackMode", "ProductionFallbackOptimizer", "evaluate_lexicographical_preference"):
        from resplan_router.optimizers.production_fallback import (
            FallbackReason,
            ProductionFallbackMode,
            ProductionFallbackOptimizer,
            evaluate_lexicographical_preference,
        )
        mapping = {
            "FallbackReason": FallbackReason,
            "ProductionFallbackMode": ProductionFallbackMode,
            "ProductionFallbackOptimizer": ProductionFallbackOptimizer,
            "evaluate_lexicographical_preference": evaluate_lexicographical_preference,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
