"""ALNS Cable Ring Optimizer v2 guided by LinUCB Contextual Bandit (Agent 1).

Integrates the frozen LinUCBBandit contextual multi-armed bandit architecture with
the complete set of 14 ALNS operators:
- 8 Core ALNS operators (from alns_operators.py)
- 6 Advanced Post-Champion ALNS operators (from advanced_operators.py)

Mathematical & Architectural Guarantees:
1. Expanded Action Space (K = 14): Disjoint linear ridge regression models per arm,
   updating covariance inverses via Sherman-Morrison rank-1 recursion in O(d^2).
2. Strict Dynamic Action Masking: Eliminates operators that are mathematically
   inapplicable or guaranteed no-ops (e.g. crossing reconstruction when X=0,
   overlap rerouting when O=0, alternative partitions when only 1 optimal partition exists).
3. 24-Feature Context Compatibility: Full compatibility with normalized observation
   vectors (from rl/observations.py) across all 14 bandit arms.
4. Lexicographical Priority:
   Feasibility (100%) > Minimum Hub Count (H*) > Routing Cost (J) > Area Gap (Delta A).
5. Incumbent Monotonicity & Validated Fallback: NEVER returns a worse valid solution
   than the starting PortfolioHybrid baseline; safely falls back if no improving feasible
   move is discovered.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx
import numpy as np

from resplan_router.contracts import MetricFloorPlan, Point2D
from resplan_router.experimental.advanced_operators import (
    ADVANCED_OPERATOR_NAMES,
    NUM_ADVANCED_OPERATORS,
    _enumerate_all_h_star_partitions,
    apply_advanced_operator,
    detect_ring_crossings,
    detect_ring_overlaps,
)
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS as NUM_CORE_OPERATORS,
    OPERATOR_NAMES as CORE_OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.base import (
    BaseFloorPlanOptimizer,
    OptimizerResult,
    create_optimizer_result,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.linucb import LinUCBBandit, compute_linucb_reward
from rl.observations import OBSERVATION_DIM, extract_observation_vector


# =========================================================================
# OPERATOR DEFINITIONS AND REGISTRATION (K = 14)
# =========================================================================

OPERATOR_NAMES_V2: List[str] = list(CORE_OPERATOR_NAMES) + list(ADVANCED_OPERATOR_NAMES)
NUM_OPERATORS_V2: int = len(OPERATOR_NAMES_V2)  # 8 + 6 = 14

# Semantic Action Indices
OP_BOUNDARY_EXCHANGE = 0
OP_MERGE_SPLIT = 1
OP_CROSSING_ELIM = 2
OP_OVERLAP_REDUCE = 3
OP_BEND_SMOOTH = 4
OP_WORST_ROUTE_REBUILD = 5
OP_RANDOM_EXPLORATION = 6
OP_HUB_CENTROID_RELOC = 7
OP_CROSSING_AWARE_RECONSTRUCTION = 8
OP_OVERLAP_AWARE_REROUTING = 9
OP_HUB_POSITION_REFINEMENT = 10
OP_ALTERNATIVE_MIN_HUB_PARTITIONS = 11
OP_RING_ORDER_SEARCH = 12
OP_JOINT_ZONE_REASSIGNMENT = 13


def apply_v2_operator(
    operator_idx: int,
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    rng: random.Random,
) -> Tuple[ALNSState, bool, str]:
    """Execute any of the 14 ALNS operators on the candidate state.

    Args:
        operator_idx: Action index in [0, 13].
        state: Current ALNS solution state.
        plan: Metric floor plan.
        topology: Precomputed macro topology graph.
        door_graph: Door adjacency graph between rooms.
        rng: Random number generator.

    Returns:
        (result_state, was_applied, operator_name)
    """
    if not (0 <= operator_idx < NUM_OPERATORS_V2):
        raise ValueError(
            f"Operator index {operator_idx} out of range [0, {NUM_OPERATORS_V2 - 1}]"
        )

    op_name = OPERATOR_NAMES_V2[operator_idx]

    try:
        if operator_idx < NUM_CORE_OPERATORS:
            return apply_alns_operator(
                operator_idx=operator_idx,
                state=state,
                plan=plan,
                topology=topology,
                door_graph=door_graph,
                rng=rng,
            )
        else:
            adv_idx = operator_idx - NUM_CORE_OPERATORS
            return apply_advanced_operator(
                operator_idx=adv_idx,
                state=state,
                plan=plan,
                topology=topology,
                door_graph=door_graph,
                rng=rng,
            )
    except Exception:
        return state, False, op_name


# =========================================================================
# STRICT DYNAMIC ACTION MASKING
# =========================================================================

def get_available_operators_v2(
    state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: nx.Graph,
    num_optimal_partitions: Optional[int] = None,
) -> List[int]:
    """Determine applicable ALNS v2 operator indices based on current solution state.

    Dynamic action masking strictly eliminates operators that are mathematically
    inapplicable or guaranteed to be no-ops:
    - 0: BOUNDARY_EXCHANGE: requires >= 2 zones and door adjacency between zones.
    - 1: MERGE_SPLIT: requires >= 2 zones (for merge) or at least one zone with > 1 room (for split).
    - 2: CROSSING_ELIM: strictly masked when total crossings X == 0.
    - 3: OVERLAP_REDUCE: strictly masked when total collinear overlap O <= 0.05m.
    - 4: BEND_SMOOTH: requires total bends B > 0 and at least one ring with >= 2 nodes.
    - 5: WORST_ROUTE_REBUILD: requires >= 1 zone.
    - 6: RANDOM_EXPLORATION: requires >= 2 zones and at least one zone with > 1 room.
    - 7: HUB_CENTROID_RELOC: requires >= 1 zone.
    - 8: CROSSING_AWARE_RECONSTRUCTION: strictly masked when total crossings X == 0.
    - 9: OVERLAP_AWARE_REROUTING: strictly masked when total collinear overlap O <= 0.05m.
    - 10: HUB_POSITION_REFINEMENT: requires >= 1 ring.
    - 11: ALTERNATIVE_MIN_HUB_PARTITIONS: strictly masked when only 1 optimal partition exists.
    - 12: RING_ORDER_SEARCH: requires at least one ring with >= 3 assigned nodes.
    - 13: JOINT_ZONE_REASSIGNMENT: requires >= 2 zones and door-adjacent boundary rooms between zones.

    Returns:
        List of applicable operator indices in [0, 13].
    """
    num_zones = len(state.zones)
    inapplicable: Set[int] = set()

    # Metrics
    total_crossings = state.bundle.total_crossings if state.bundle else 0
    total_overlap_m = state.bundle.total_overlap_m if state.bundle else 0.0
    total_bends = state.bundle.total_bends if state.bundle else 0
    rings = state.bundle.hub_rings if state.bundle else []

    # 1. Zone count constraints
    if num_zones < 2:
        inapplicable.add(OP_BOUNDARY_EXCHANGE)
        inapplicable.add(OP_RANDOM_EXPLORATION)
        inapplicable.add(OP_JOINT_ZONE_REASSIGNMENT)
        # If all zones have only 1 room, split is also impossible
        if all(len(z) <= 1 for z in state.zones):
            inapplicable.add(OP_MERGE_SPLIT)
    else:
        # Check if adjacent zones actually share door edges in door_graph
        has_door_adjacent_zones = False
        for i in range(num_zones):
            for j in range(i + 1, num_zones):
                if any(
                    door_graph.has_edge(r1, r2)
                    for r1 in state.zones[i]
                    for r2 in state.zones[j]
                ):
                    has_door_adjacent_zones = True
                    break
            if has_door_adjacent_zones:
                break

        if not has_door_adjacent_zones:
            inapplicable.add(OP_BOUNDARY_EXCHANGE)
            inapplicable.add(OP_JOINT_ZONE_REASSIGNMENT)

        # Check random exploration room relocation
        if all(len(z) <= 1 for z in state.zones):
            inapplicable.add(OP_RANDOM_EXPLORATION)

    # 2. Crossing elimination constraints (X == 0)
    if total_crossings <= 0:
        inapplicable.add(OP_CROSSING_ELIM)
        inapplicable.add(OP_CROSSING_AWARE_RECONSTRUCTION)

    # 3. Overlap reduction constraints (O <= 0.05)
    if total_overlap_m <= 0.05:
        inapplicable.add(OP_OVERLAP_REDUCE)
        inapplicable.add(OP_OVERLAP_AWARE_REROUTING)

    # 4. Bend smoothing constraints
    if total_bends <= 0 or not any(len(r.assigned_nodes) >= 2 for r in rings):
        inapplicable.add(OP_BEND_SMOOTH)

    # 5. Alternative partition enumeration constraint
    if num_optimal_partitions is not None and num_optimal_partitions <= 1:
        inapplicable.add(OP_ALTERNATIVE_MIN_HUB_PARTITIONS)

    # 6. Ring-order 3-opt / Or-opt search constraints (requires ring with >= 3 nodes)
    if not any(len(r.assigned_nodes) >= 3 for r in rings):
        inapplicable.add(OP_RING_ORDER_SEARCH)

    # 7. Hub operators require at least 1 ring
    if len(rings) == 0:
        inapplicable.add(OP_HUB_POSITION_REFINEMENT)
        inapplicable.add(OP_HUB_CENTROID_RELOC)
        inapplicable.add(OP_WORST_ROUTE_REBUILD)

    available = [i for i in range(NUM_OPERATORS_V2) if i not in inapplicable]
    # Safety fallback: never return empty list
    return available if available else [OP_WORST_ROUTE_REBUILD, OP_HUB_POSITION_REFINEMENT]


# =========================================================================
# 24-FEATURE OBSERVATION EXTRACTOR FOR V2
# =========================================================================

def extract_observation_vector_v2(
    state: ALNSState,
    incumbent: ALNSState,
    baseline_J: float,
    baseline_H: int,
    step_idx: int,
    max_steps: int,
    temperature: float,
    initial_temperature: float,
    stagnation_steps: int,
    operator_success_counts: List[int],
    operator_call_counts: List[int],
    plan: MetricFloorPlan,
) -> np.ndarray:
    """Extract and normalize 24-dimensional state feature vector for v2.

    Maintains exact 24-dimensional observation vector compatibility:
    Features 0-15:
      0: Eligible room count normalized (N / 15.0)
      1: Total floor area normalized (min(A, 300.0) / 300.0)
      2: Current hub count normalized (H / 10.0)
      3: Incumbent hub count normalized (H* / 10.0)
      4: Current J cost normalized (min(J, 500.0) / 500.0)
      5: Incumbent J* cost normalized (min(J*, 500.0) / 500.0)
      6: Baseline J0 cost normalized (min(J0, 500.0) / 500.0)
      7: Improvement from baseline ((J0 - J) / max(1.0, J0)), clipped [-1.0, 1.0]
      8: Gap to incumbent ((J - J*) / max(1.0, J*)), clipped [0.0, 1.0]
      9: Current cable length L normalized (min(L, 300.0) / 300.0)
     10: Current total bends B normalized (min(B, 100.0) / 100.0)
     11: Current crossings X normalized (min(X, 20.0) / 20.0)
     12: Current overlap length O normalized (min(O, 50.0) / 50.0)
     13: Area gap Delta A normalized (min(Delta A, 100.0) / 100.0)
     14: Stagnation counter normalized (min(stagnation, 25) / 25.0)
     15: Step progress normalized (step_idx / max(1, max_steps))
    Features 16-23:
      Structured cluster success rates across the 14 operators:
      16: Zone boundary modification cluster (ops 0, 13)
      17: Macro partition / merge-split cluster (ops 1, 11)
      18: Crossing elimination cluster (ops 2, 8)
      19: Overlap reduction cluster (ops 3, 9)
      20: Tour / bend smoothing cluster (ops 4, 12)
      21: Hub placement cluster (ops 7, 10)
      22: Rebuild & exploration cluster (ops 5, 6)
      23: Overall advanced operator cluster success rate (ops 8..13)

    Returns:
        np.ndarray of shape (24,), dtype float32.
    """
    obs = np.zeros(OBSERVATION_DIM, dtype=np.float32)

    # 1. Floor plan context
    eligible_count = sum(1 for r in plan.rooms.values() if r.is_eligible)
    obs[0] = np.clip(eligible_count / 15.0, 0.0, 1.0)
    obs[1] = np.clip(plan.total_area_m2 / 300.0, 0.0, 1.0)

    # 2. Hub metrics
    obs[2] = np.clip(state.h_count / 10.0, 0.0, 1.0)
    obs[3] = np.clip(incumbent.h_count / 10.0, 0.0, 1.0)

    # 3. Cost metrics
    obs[4] = np.clip(state.cost_J / 500.0, 0.0, 1.0)
    obs[5] = np.clip(incumbent.cost_J / 500.0, 0.0, 1.0)
    obs[6] = np.clip(baseline_J / 500.0, 0.0, 1.0)

    # 4. Relative gaps
    j_base = max(1.0, baseline_J)
    obs[7] = np.clip((baseline_J - state.cost_J) / j_base, -1.0, 1.0)
    j_inc = max(1.0, incumbent.cost_J)
    obs[8] = np.clip((state.cost_J - incumbent.cost_J) / j_inc, 0.0, 1.0)

    # 5. Component breakdown
    tot_l = state.bundle.total_length_m if state.bundle else 0.0
    tot_b = state.bundle.total_bends if state.bundle else 0
    tot_x = state.bundle.total_crossings if state.bundle else 0
    tot_o = state.bundle.total_overlap_m if state.bundle else 0.0
    obs[9] = np.clip(tot_l / 300.0, 0.0, 1.0)
    obs[10] = np.clip(tot_b / 100.0, 0.0, 1.0)
    obs[11] = np.clip(tot_x / 20.0, 0.0, 1.0)
    obs[12] = np.clip(tot_o / 50.0, 0.0, 1.0)
    obs[13] = np.clip(state.area_gap / 100.0, 0.0, 1.0)

    # 6. Search dynamic state
    obs[14] = np.clip(stagnation_steps / 25.0, 0.0, 1.0)
    obs[15] = np.clip(step_idx / max(1, max_steps), 0.0, 1.0)

    # 7. Structured operator success rates (16..23)
    def cluster_rate(op_indices: Sequence[int]) -> float:
        tot_calls = sum(
            operator_call_counts[i]
            for i in op_indices
            if i < len(operator_call_counts)
        )
        tot_succ = sum(
            operator_success_counts[i]
            for i in op_indices
            if i < len(operator_success_counts)
        )
        return (tot_succ / tot_calls) if tot_calls > 0 else 0.5

    # 16: Zone boundary modification cluster (0: BOUNDARY_EXCHANGE, 13: JOINT_ZONE_REASSIGNMENT)
    obs[16] = np.clip(cluster_rate([0, 13]), 0.0, 1.0)
    # 17: Macro partition cluster (1: MERGE_SPLIT, 11: ALTERNATIVE_MIN_HUB_PARTITIONS)
    obs[17] = np.clip(cluster_rate([1, 11]), 0.0, 1.0)
    # 18: Crossing elimination cluster (2: CROSSING_ELIM, 8: CROSSING_AWARE_RECONSTRUCTION)
    obs[18] = np.clip(cluster_rate([2, 8]), 0.0, 1.0)
    # 19: Overlap reduction cluster (3: OVERLAP_REDUCE, 9: OVERLAP_AWARE_REROUTING)
    obs[19] = np.clip(cluster_rate([3, 9]), 0.0, 1.0)
    # 20: Tour / bend smoothing cluster (4: BEND_SMOOTH, 12: RING_ORDER_SEARCH)
    obs[20] = np.clip(cluster_rate([4, 12]), 0.0, 1.0)
    # 21: Hub placement cluster (7: HUB_CENTROID_RELOC, 10: HUB_POSITION_REFINEMENT)
    obs[21] = np.clip(cluster_rate([7, 10]), 0.0, 1.0)
    # 22: Rebuild & exploration cluster (5: WORST_ROUTE_REBUILD, 6: RANDOM_EXPLORATION)
    obs[22] = np.clip(cluster_rate([5, 6]), 0.0, 1.0)
    # 23: Overall advanced operator cluster success rate (8..13)
    obs[23] = np.clip(cluster_rate(list(range(8, 14))), 0.0, 1.0)

    return obs


def compute_v2_reward(
    prev_state: ALNSState,
    curr_state: Optional[ALNSState],
    incumbent_state: ALNSState,
    baseline_J: float,
    baseline_H: int,
    was_applied: bool,
    is_valid: bool,
    step_penalty: float = -0.01,
) -> float:
    """Compute lexicographically aligned reward with targeted geometric bonus signals.

    Inherits all core LinUCB reward properties:
    - Feasibility: -1.0 for invalid states, -0.05 for unapplied moves
    - Primary Hub Priority: +5.0 * (prev_H - curr_H), -10.0 for hub increases
    - Symmetric normalized delta J
    - Global incumbent bonuses: +2.0 for H*, +1.0 for J*
    Plus surgical geometric bonuses for reducing crossings and collinear overlaps.
    """
    base_reward = compute_linucb_reward(
        prev_state=prev_state,
        curr_state=curr_state,
        incumbent_state=incumbent_state,
        baseline_J=baseline_J,
        baseline_H=baseline_H,
        was_applied=was_applied,
        is_valid=is_valid,
        step_penalty=step_penalty,
    )

    if not is_valid or curr_state is None or not was_applied:
        return base_reward

    geometric_bonus = 0.0
    # Conflict reduction bonuses (when H does not increase)
    if curr_state.h_count <= prev_state.h_count:
        if curr_state.bundle and prev_state.bundle:
            # Crossing elimination bonus
            dx = prev_state.bundle.total_crossings - curr_state.bundle.total_crossings
            if dx > 0:
                geometric_bonus += 0.5 * float(dx)

            # Overlap reduction bonus
            do = prev_state.bundle.total_overlap_m - curr_state.bundle.total_overlap_m
            if do > 0.1:
                geometric_bonus += 0.25 * float(min(2.0, do))

    return float(np.clip(base_reward + geometric_bonus, -10.0, 10.0))


# =========================================================================
# LINUCB-ALNS V2 OPTIMIZER
# =========================================================================

class LinUCB_ALNS_V2Optimizer(BaseFloorPlanOptimizer):
    """ALNS Cable Ring Optimizer v2: LinUCB Contextual Bandit with 14 Operators.

    Combines the 8 core ALNS operators with the 6 advanced post-champion geometric
    operators under a 14-arm LinUCBBandit architecture with dynamic action masking,
    24-dimensional continuous feature context, Sherman-Morrison rank-1 matrix updates,
    and a validated fallback to v1 / PortfolioHybrid.
    """

    def __init__(
        self,
        name: str = "LinUCB_ALNS_v2",
        max_iterations: int = 25,
        cooling_rate: float = 0.95,
        initial_temperature: float = 10.0,
        alpha: float = 0.5,
        lambda_reg: float = 1.0,
        bandit: Optional[LinUCBBandit] = None,
        online_learning: bool = True,
        mask_inapplicable: bool = True,
        use_cache: bool = True,
        base_opt: Optional[BaseFloorPlanOptimizer] = None,
        reinvert_interval: int = 500,
    ) -> None:
        """Initialize LinUCB_ALNS_V2Optimizer.

        Args:
            name: Optimizer display name.
            max_iterations: Number of ALNS exploration steps (default 25).
            cooling_rate: Simulated annealing geometric cooling factor (default 0.95).
            initial_temperature: Starting temperature for intermediate move acceptance (default 10.0).
            alpha: LinUCB exploration parameter (default 0.5).
            lambda_reg: LinUCB L2 ridge regularization weight (default 1.0).
            bandit: Optional existing or pre-trained LinUCBBandit instance (14 arms).
            online_learning: Whether to update bandit weights after each operator step.
            mask_inapplicable: Whether to dynamically mask inapplicable operators.
            use_cache: Whether to query persistent BaselineCache for instant initial state.
            base_opt: Initial incumbent generator (defaults to PortfolioHybridOptimizer).
            reinvert_interval: Frequency of exact matrix re-inversion in LinUCBBandit.
        """
        super().__init__(name=name)
        self.max_iterations = max_iterations
        self.cooling_rate = cooling_rate
        self.initial_temperature = initial_temperature
        self.alpha = alpha
        self.lambda_reg = lambda_reg
        self.online_learning = online_learning
        self.mask_inapplicable = mask_inapplicable
        self.use_cache = use_cache
        self.reinvert_interval = reinvert_interval

        self.base_opt = base_opt if base_opt is not None else PortfolioHybridOptimizer()
        self.bandit = bandit if bandit is not None else LinUCBBandit(
            feature_dim=OBSERVATION_DIM,
            num_arms=NUM_OPERATORS_V2,
            alpha=alpha,
            lambda_reg=lambda_reg,
            use_sherman_morrison=True,
            reinvert_interval=reinvert_interval,
        )

    @property
    def feature_dim(self) -> int:
        """Return the observation feature dimension (24)."""
        return self.bandit.d

    @property
    def num_arms(self) -> int:
        """Return the total number of operator arms (14)."""
        return self.bandit.k

    @property
    def arm_pull_counts(self) -> List[int]:
        """Return operator arm pull counts."""
        return self.bandit.pull_counts.tolist()

    @property
    def A_matrices(self) -> np.ndarray:
        """Return LinUCB covariance design matrices A."""
        return self.bandit.A

    @property
    def A_inv_matrices(self) -> np.ndarray:
        """Return LinUCB inverse covariance matrices A^{-1}."""
        return self.bandit.A_inv

    @property
    def theta_vectors(self) -> np.ndarray:
        """Return LinUCB ridge regression weight vectors theta."""
        return self.bandit.theta

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Execute LinUCB v2 ALNS optimization on target floor plan.

        Guarantees:
        - NEVER returns a worse valid solution than starting PortfolioHybrid incumbent.
        - Employs independent FloorPlanChecker for objective evaluation and rule auditing.
        - Safe fallback to pristine baseline if v2 cannot produce an improving feasible move.
        """
        t_start = time.perf_counter()
        rng = random.Random(random_seed)

        # 1. Compute baseline incumbent with PortfolioHybrid (or BaselineCache)
        init_res: Optional[OptimizerResult] = None
        if self.use_cache:
            try:
                from rl.baseline_cache import BaselineCache
                cache = BaselineCache.get_instance()
                valid, cached_res, _ = cache.get_or_compute(floor_plan, self.base_opt)
                if (
                    valid
                    and cached_res is not None
                    and cached_res.bundle is not None
                    and cached_res.partition is not None
                ):
                    init_res = cached_res
            except Exception:
                pass

        if init_res is None:
            baseline_time_budget = min(2.0, max(0.5, time_limit_seconds * 0.4))
            init_res = self.base_opt.optimize(
                floor_plan,
                time_limit_seconds=baseline_time_budget,
                random_seed=random_seed,
            )

        # If baseline is invalid or lacks required solution components, return immediately
        if not init_res.is_valid or init_res.bundle is None or init_res.partition is None:
            return init_res

        # 2. Extract topological context
        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)
        door_graph = build_door_connectivity_graph(floor_plan, topology)

        # Precompute optimal partition count for dynamic action masking of OP 11
        num_optimal_partitions: Optional[int] = None
        try:
            _, all_parts = _enumerate_all_h_star_partitions(floor_plan, topology, door_graph)
            num_optimal_partitions = len(all_parts)
        except Exception:
            num_optimal_partitions = None

        # 3. Initialize ALNS State from baseline
        zones = [set(h.assigned_rooms) for h in init_res.partition.hubs]
        curr_state = ALNSState(
            zones=zones,
            bundle=init_res.bundle,
            partition=init_res.partition,
            cost_J=init_res.total_cost_J,
            h_count=init_res.h_count,
            area_gap=init_res.area_gap_m2,
            is_valid=True,
        )
        best_state = curr_state.clone()

        baseline_J = init_res.total_cost_J
        baseline_H = init_res.h_count

        # 4. Search and tracking variables
        temperature = self.initial_temperature
        stagnation_steps = 0
        operator_call_counts: List[int] = [0] * NUM_OPERATORS_V2
        operator_success_counts: List[int] = [0] * NUM_OPERATORS_V2
        incumbent_updates: int = 0
        accepted_moves: int = 0
        iteration: int = 0

        # Time threshold: reserve 0.35s for final verification & assembly
        time_threshold = max(0.5, time_limit_seconds - 0.35)

        # 5. ALNS Main Exploration Loop
        while (
            iteration < self.max_iterations
            and ((time.perf_counter() - t_start) < time_threshold or iteration == 0)
        ):
            iteration += 1

            # A. Extract 24-dimensional continuous feature context
            obs = extract_observation_vector_v2(
                state=curr_state,
                incumbent=best_state,
                baseline_J=baseline_J,
                baseline_H=baseline_H,
                step_idx=iteration - 1,
                max_steps=self.max_iterations,
                temperature=temperature,
                initial_temperature=self.initial_temperature,
                stagnation_steps=stagnation_steps,
                operator_success_counts=operator_success_counts,
                operator_call_counts=operator_call_counts,
                plan=floor_plan,
            )

            # B. Strict dynamic action masking
            available_actions = (
                get_available_operators_v2(
                    state=curr_state,
                    plan=floor_plan,
                    topology=topology,
                    door_graph=door_graph,
                    num_optimal_partitions=num_optimal_partitions,
                )
                if self.mask_inapplicable
                else list(range(NUM_OPERATORS_V2))
            )

            # C. Contextual bandit operator selection (LinUCB)
            chosen_op, _ = self.bandit.select_arm(
                context=obs,
                available_actions=available_actions,
                rng=rng,
            )
            operator_call_counts[chosen_op] += 1

            # D. Apply chosen operator across the unified 14-operator engine
            prev_state = curr_state.clone()
            cand_state, was_applied, _ = apply_v2_operator(
                operator_idx=chosen_op,
                state=curr_state,
                plan=floor_plan,
                topology=topology,
                door_graph=door_graph,
                rng=rng,
            )

            is_valid = cand_state.is_valid if cand_state is not None else False

            # E. Lexicographical Incumbent Comparison
            is_strictly_better = False
            if is_valid and was_applied and cand_state is not None:
                if cand_state.h_count < best_state.h_count:
                    is_strictly_better = True
                elif cand_state.h_count == best_state.h_count:
                    if cand_state.cost_J < best_state.cost_J - 1e-4:
                        is_strictly_better = True
                    elif (
                        abs(cand_state.cost_J - best_state.cost_J) <= 1e-4
                        and cand_state.area_gap < best_state.area_gap - 1e-4
                    ):
                        is_strictly_better = True

            # F. State Transition & SA Acceptance
            if is_strictly_better and cand_state is not None:
                # Discovered strictly better incumbent
                best_state = cand_state.clone()
                curr_state = cand_state.clone()
                operator_success_counts[chosen_op] += 1
                stagnation_steps = 0
                incumbent_updates += 1
                accepted_moves += 1
            elif is_valid and was_applied and cand_state is not None:
                # Simulated Annealing acceptance for intermediate search
                delta_j = cand_state.cost_J - curr_state.cost_J
                if cand_state.h_count == curr_state.h_count:
                    accept_prob = (
                        math.exp(-delta_j / max(0.1, temperature))
                        if delta_j > 0.0
                        else 1.0
                    )
                    if rng.random() < accept_prob:
                        curr_state = cand_state.clone()
                        accepted_moves += 1
                        if delta_j < 0.0:
                            operator_success_counts[chosen_op] += 1
                stagnation_steps += 1
            else:
                stagnation_steps += 1

            # G. Contextual Bandit Feedback & Sherman-Morrison Online Ridge Update
            reward = compute_v2_reward(
                prev_state=prev_state,
                curr_state=cand_state if was_applied else None,
                incumbent_state=best_state,
                baseline_J=baseline_J,
                baseline_H=baseline_H,
                was_applied=was_applied,
                is_valid=is_valid,
            )

            if self.online_learning:
                self.bandit.update(arm=chosen_op, context=obs, reward=reward)

            # H. Cooling
            temperature *= self.cooling_rate

        # 6. Final Result Assembly, Monotonicity Verification & Validated Fallback
        total_runtime = time.perf_counter() - t_start

        diag_info: Dict[str, Any] = {
            "iterations": iteration,
            "incumbent_updates": incumbent_updates,
            "accepted_moves": accepted_moves,
            "stagnation_steps": stagnation_steps,
            "baseline_h_count": baseline_H,
            "baseline_cost_J": round(baseline_J, 4),
            "final_temperature": round(temperature, 4),
            "operator_calls": {
                OPERATOR_NAMES_V2[i]: operator_call_counts[i]
                for i in range(NUM_OPERATORS_V2)
            },
            "operator_successes": {
                OPERATOR_NAMES_V2[i]: operator_success_counts[i]
                for i in range(NUM_OPERATORS_V2)
            },
            "bandit_telemetry": self.bandit.get_telemetry(),
        }

        # If any strictly superior incumbent was found, evaluate and verify it
        if incumbent_updates > 0:
            candidate_res = create_optimizer_result(
                algorithm_name=self.name,
                plan=floor_plan,
                bundle=best_state.bundle,
                partition=best_state.partition,
                solver_status="OPTIMAL",
                runtime_s=total_runtime,
                diagnostic_info=diag_info,
            )

            # Monotonicity guard: never return a worse solution than starting baseline
            is_lex_superior = False
            if candidate_res.is_valid:
                if candidate_res.h_count < init_res.h_count:
                    is_lex_superior = True
                elif (
                    candidate_res.h_count == init_res.h_count
                    and candidate_res.total_cost_J <= init_res.total_cost_J + 1e-4
                ):
                    is_lex_superior = True

            if is_lex_superior:
                diag_info["fallback_to_baseline"] = False
                return candidate_res

        # Fallback to pristine baseline solution if no improvement or if candidate regressed
        diag_info["fallback_to_baseline"] = True
        diag_info["fallback_reason"] = (
            "no_improving_moves_found" if incumbent_updates == 0 else "candidate_failed_monotonicity"
        )
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=init_res.bundle,
            partition=init_res.partition,
            solver_status=init_res.solver_status,
            runtime_s=total_runtime,
            diagnostic_info=diag_info,
        )


# Export aliases for seamless backward and forward compatibility
ALNSLinUCBV2Optimizer = LinUCB_ALNS_V2Optimizer
ALNSLinUCB_V2Optimizer = LinUCB_ALNS_V2Optimizer
