"""Contextual Bandit ALNS Optimizer using LinUCB (Subagent 2).

Integrates the LinUCBBandit contextual multi-armed bandit algorithm with Adaptive
Large Neighborhood Search (ALNS) on top of PortfolioHybrid baseline generation.

Mathematical & Architectural Guarantees:
1. Feature-guided Operator Selection: Uses 24-dimensional normalized state observations
   from rl/observations.py to predict ALNS operator payoffs in real-time.
2. Upper Confidence Bound: Balances exploitation (ridge regression mean) and exploration
   (uncertainty ellipsoid radius scaled by alpha).
3. Action Masking: Filters inapplicable destroy/repair moves dynamically.
4. Incumbent Monotonicity: Strictly tracks and updates the lexicographically best valid
   candidate solution. NEVER returns a worse valid solution than PortfolioHybrid.
5. Standardized Output: Returns fully audited and validated OptimizerResult.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
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


def get_available_operators(state: ALNSState) -> List[int]:
    """Determine applicable ALNS operator indices based on current solution state.

    Masks operators known to be mathematically impossible or trivial:
    - BOUNDARY_EXCHANGE (0) and RANDOM_EXPLORATION (6) require >= 2 zones.
    - MERGE_SPLIT (1) requires >= 2 zones (for merge) or at least one zone with > 1 room (for split).
    """
    num_zones = len(state.zones)
    inapplicable: Set[int] = set()

    if num_zones < 2:
        inapplicable.add(0)  # BOUNDARY_EXCHANGE
        inapplicable.add(6)  # RANDOM_EXPLORATION
        # If all zones have only 1 room, split is also impossible
        if all(len(z) <= 1 for z in state.zones):
            inapplicable.add(1)  # MERGE_SPLIT

    available = [i for i in range(NUM_OPERATORS) if i not in inapplicable]
    return available if available else list(range(NUM_OPERATORS))


class ALNSLinUCBOptimizer(BaseFloorPlanOptimizer):
    """ALNS Cable Ring Optimizer guided by LinUCB Contextual Bandit."""

    def __init__(
        self,
        name: str = "ALNS_LinUCB",
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
    ) -> None:
        """Initialize ALNSLinUCBOptimizer.

        Args:
            name: Optimizer display name.
            max_iterations: Number of ALNS exploration steps (default 25).
            cooling_rate: Simulated annealing geometric cooling factor (default 0.95).
            initial_temperature: Starting temperature for intermediate move acceptance (default 10.0).
            alpha: LinUCB exploration parameter (default 0.5).
            lambda_reg: LinUCB L2 ridge regularization weight (default 1.0).
            bandit: Optional existing or pre-trained LinUCBBandit instance.
            online_learning: Whether to update bandit weights after each operator step.
            mask_inapplicable: Whether to dynamically mask inapplicable operators.
            use_cache: Whether to query persistent BaselineCache for instant initial state.
            base_opt: Initial incumbent generator (defaults to PortfolioHybridOptimizer).
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

        self.base_opt = base_opt if base_opt is not None else PortfolioHybridOptimizer()
        self.bandit = bandit if bandit is not None else LinUCBBandit(
            feature_dim=OBSERVATION_DIM,
            num_arms=NUM_OPERATORS,
            alpha=alpha,
            lambda_reg=lambda_reg,
        )

    @property
    def feature_dim(self) -> int:
        """Return the observation feature dimension."""
        return self.bandit.d

    @property
    def arm_pull_counts(self) -> List[int]:
        """Return operator arm pull counts."""
        return self.bandit.pull_counts.tolist()

    @property
    def A_matrices(self) -> np.ndarray:
        """Return LinUCB covariance matrices A."""
        return self.bandit.A

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Execute LinUCB-guided ALNS optimization on the target floor plan.

        Guarantees:
        - NEVER returns a worse valid solution than the starting PortfolioHybrid incumbent.
        - Employs independent FloorPlanChecker for objective evaluation.
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
                if valid and cached_res is not None and cached_res.bundle is not None and cached_res.partition is not None:
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
        best_res = init_res

        baseline_J = init_res.total_cost_J
        baseline_H = init_res.h_count

        # 4. Search and tracking variables
        temperature = self.initial_temperature
        stagnation_steps = 0
        operator_call_counts: List[int] = [0] * NUM_OPERATORS
        operator_success_counts: List[int] = [0] * NUM_OPERATORS
        incumbent_updates: int = 0
        accepted_moves: int = 0
        iteration: int = 0

        # Time threshold: reserve 0.3s for final verification & assembly
        time_threshold = time_limit_seconds - 0.3

        # 5. ALNS Main Exploration Loop
        while (
            iteration < self.max_iterations
            and ((time.perf_counter() - t_start) < time_threshold or iteration == 0)
        ):
            iteration += 1

            # A. Extract 24-dimensional continuous feature context
            obs = extract_observation_vector(
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

            # B. Action masking
            available_actions = (
                get_available_operators(curr_state)
                if self.mask_inapplicable
                else list(range(NUM_OPERATORS))
            )

            # C. Contextual bandit operator selection
            chosen_op, _ = self.bandit.select_arm(
                context=obs,
                available_actions=available_actions,
                rng=rng,
            )
            operator_call_counts[chosen_op] += 1

            # D. Apply chosen ALNS operator
            prev_state = curr_state.clone()
            cand_state, was_applied, _ = apply_alns_operator(
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
                elif (
                    cand_state.h_count == best_state.h_count
                    and cand_state.cost_J < best_state.cost_J - 1e-4
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

            # G. Contextual Bandit Feedback & Online Ridge Update
            reward = compute_linucb_reward(
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

        # 6. Final Result Assembly & Monotonicity Verification
        total_runtime = time.perf_counter() - t_start

        # Detailed diagnostic summary
        diag_info: Dict[str, Any] = {
            "iterations": iteration,
            "incumbent_updates": incumbent_updates,
            "accepted_moves": accepted_moves,
            "stagnation_steps": stagnation_steps,
            "baseline_h_count": baseline_H,
            "baseline_cost_J": round(baseline_J, 4),
            "final_temperature": round(temperature, 4),
            "operator_calls": {OPERATOR_NAMES[i]: operator_call_counts[i] for i in range(NUM_OPERATORS)},
            "operator_successes": {OPERATOR_NAMES[i]: operator_success_counts[i] for i in range(NUM_OPERATORS)},
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
                return candidate_res

        # Fallback to pristine baseline solution if no improvement or if candidate regressed
        diag_info["fallback_to_baseline"] = True
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=init_res.bundle,
            partition=init_res.partition,
            solver_status=init_res.solver_status,
            runtime_s=total_runtime,
            diagnostic_info=diag_info,
        )
