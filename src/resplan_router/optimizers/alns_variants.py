"""Comparative ALNS Baseline Variants for Honeywell Benchmarking (Subagent 5).

Includes:
1. ALNSRandomOptimizer: Uniform random destroy/repair operator selection.
2. ALNSBanditOptimizer: Adaptive roulette-wheel Multi-Armed Bandit operator selection.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult, create_optimizer_result
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker


class ALNSRandomOptimizer(BaseFloorPlanOptimizer):
    """ALNS with uniform random operator selection across iterations."""

    def __init__(
        self,
        name: str = "ALNS_Random",
        max_iterations: int = 25,
        cooling_rate: float = 0.95,
        initial_temperature: float = 10.0,
    ) -> None:
        super().__init__(name=name)
        self.max_iterations = max_iterations
        self.cooling_rate = cooling_rate
        self.initial_temperature = initial_temperature
        self.base_opt = PortfolioHybridOptimizer()

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        t_start = time.perf_counter()
        rng = random.Random(random_seed)

        # 1. Base Portfolio Hybrid incumbent
        init_res = self.base_opt.optimize(floor_plan, time_limit_seconds=1.5, random_seed=random_seed)
        if not init_res.is_valid or init_res.h_count <= 1:
            return init_res

        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)
        door_graph = build_door_connectivity_graph(floor_plan, topology)

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
        temperature = self.initial_temperature

        iteration = 0
        while iteration < self.max_iterations and (time.perf_counter() - t_start) < (time_limit_seconds - 0.4):
            iteration += 1
            op_idx = rng.randrange(NUM_OPERATORS)

            cand_state, was_applied, _ = apply_alns_operator(
                operator_idx=op_idx,
                state=curr_state,
                plan=floor_plan,
                topology=topology,
                door_graph=door_graph,
                rng=rng,
            )

            if cand_state is not None and cand_state.is_valid and was_applied:
                # Incumbent check
                is_better = False
                if cand_state.h_count < best_state.h_count:
                    is_better = True
                elif cand_state.h_count == best_state.h_count and cand_state.cost_J < best_state.cost_J - 1e-4:
                    is_better = True

                if is_better:
                    best_state = cand_state.clone()
                    curr_state = cand_state.clone()
                else:
                    # Simulated Annealing acceptance
                    delta_j = cand_state.cost_J - curr_state.cost_J
                    if cand_state.h_count == curr_state.h_count:
                        prob = math.exp(-delta_j / max(0.1, temperature)) if delta_j > 0 else 1.0
                        if rng.random() < prob:
                            curr_state = cand_state.clone()

            temperature *= self.cooling_rate

        runtime = time.perf_counter() - t_start
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=best_state.bundle,
            partition=best_state.partition,
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={"iterations": iteration, "final_temperature": round(temperature, 4)},
        )


class ALNSBanditOptimizer(BaseFloorPlanOptimizer):
    """ALNS with Adaptive Roulette Wheel / Multi-Armed Bandit operator selection."""

    def __init__(
        self,
        name: str = "ALNS_Bandit",
        max_iterations: int = 25,
        cooling_rate: float = 0.95,
        initial_temperature: float = 10.0,
        decay: float = 0.8,
    ) -> None:
        super().__init__(name=name)
        self.max_iterations = max_iterations
        self.cooling_rate = cooling_rate
        self.initial_temperature = initial_temperature
        self.decay = decay
        self.base_opt = PortfolioHybridOptimizer()

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        t_start = time.perf_counter()
        rng = random.Random(random_seed)

        init_res = self.base_opt.optimize(floor_plan, time_limit_seconds=1.5, random_seed=random_seed)
        if not init_res.is_valid or init_res.h_count <= 1:
            return init_res

        portals = extract_all_portals(floor_plan)
        topology = MacroTopologyGraph.build_topology(floor_plan, portals)
        door_graph = build_door_connectivity_graph(floor_plan, topology)

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
        temperature = self.initial_temperature

        # Bandit operator weights
        weights = [1.0] * NUM_OPERATORS

        iteration = 0
        while iteration < self.max_iterations and (time.perf_counter() - t_start) < (time_limit_seconds - 0.4):
            iteration += 1

            # Roulette wheel selection
            total_w = sum(weights)
            probs = [w / total_w for w in weights]
            r_val = rng.random()
            cum = 0.0
            chosen_op = 0
            for i, p in enumerate(probs):
                cum += p
                if r_val <= cum:
                    chosen_op = i
                    break

            cand_state, was_applied, _ = apply_alns_operator(
                operator_idx=chosen_op,
                state=curr_state,
                plan=floor_plan,
                topology=topology,
                door_graph=door_graph,
                rng=rng,
            )

            score = 0.0
            if cand_state is not None and cand_state.is_valid and was_applied:
                is_better = False
                if cand_state.h_count < best_state.h_count:
                    is_better = True
                    score = 5.0  # Massive score for hub reduction
                elif cand_state.h_count == best_state.h_count and cand_state.cost_J < best_state.cost_J - 1e-4:
                    is_better = True
                    score = 3.0  # High score for new incumbent

                if is_better:
                    best_state = cand_state.clone()
                    curr_state = cand_state.clone()
                else:
                    delta_j = cand_state.cost_J - curr_state.cost_J
                    if cand_state.h_count == curr_state.h_count:
                        prob = math.exp(-delta_j / max(0.1, temperature)) if delta_j > 0 else 1.0
                        if rng.random() < prob:
                            curr_state = cand_state.clone()
                            if delta_j < 0:
                                score = 1.0

            # Update weight with exponential decay
            weights[chosen_op] = self.decay * weights[chosen_op] + (1.0 - self.decay) * (1.0 + score)
            temperature *= self.cooling_rate

        runtime = time.perf_counter() - t_start
        return create_optimizer_result(
            algorithm_name=self.name,
            plan=floor_plan,
            bundle=best_state.bundle,
            partition=best_state.partition,
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={"iterations": iteration, "weights": [round(w, 3) for w in weights]},
        )
