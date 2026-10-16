"""Phase 11: Optimized Cable-Routing, Global CP-SAT Selection, and ALNS."""

from resplan_router.phase11_optimization.alns import ALNSConfig, ALNSOptimizer, ALNSResult
from resplan_router.phase11_optimization.conflict_matrix import PairwiseConflictMatrix, compute_ring_pair_conflict
from resplan_router.phase11_optimization.global_selector import GlobalRingSelector, GlobalSelectionResult
from resplan_router.phase11_optimization.greedy_baseline import GreedyBaselineResult, solve_greedy_baseline
from resplan_router.phase11_optimization.lower_bounds import (
    build_door_connectivity_graph,
    compute_component_lower_bound,
    enumerate_valid_connected_zones,
    prove_minimum_hubs,
)
from resplan_router.phase11_optimization.objective import (
    LexicographicCost,
    compute_J,
    evaluate_solution,
)
from resplan_router.phase11_optimization.phase11_solver import Phase11OptimizationResult, optimize_floor_plan
from resplan_router.phase11_optimization.ring_pool import (
    CompleteRingCandidate,
    ZoneRingGenerator,
    compute_ring_internal_conflicts,
)
from resplan_router.phase11_optimization.route_cache import RouteCache
from resplan_router.phase11_optimization.route_pool import (
    YenRoutePool,
    compute_polyline_metrics,
    validate_geometric_route,
)

__all__ = [
    "ALNSConfig",
    "ALNSOptimizer",
    "ALNSResult",
    "CompleteRingCandidate",
    "GlobalRingSelector",
    "GlobalSelectionResult",
    "GreedyBaselineResult",
    "LexicographicCost",
    "PairwiseConflictMatrix",
    "Phase11OptimizationResult",
    "RouteCache",
    "YenRoutePool",
    "ZoneRingGenerator",
    "build_door_connectivity_graph",
    "compute_J",
    "compute_component_lower_bound",
    "compute_polyline_metrics",
    "compute_ring_internal_conflicts",
    "compute_ring_pair_conflict",
    "enumerate_valid_connected_zones",
    "evaluate_solution",
    "optimize_floor_plan",
    "prove_minimum_hubs",
    "solve_greedy_baseline",
    "validate_geometric_route",
]
