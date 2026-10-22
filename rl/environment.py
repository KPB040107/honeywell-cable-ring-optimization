"""Gymnasium-compatible Reinforcement Learning Environment for PPO-ALNS (Subagent 1).

Wraps Honeywell Cable Ring optimization, managing state observations, ALNS operator
actions, lexicographical rewards, simulated annealing acceptance, and strict incumbent preservation.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:
    # Graceful fallback before gymnasium package install finishes
    class gym:  # type: ignore
        class Env:
            pass

    spaces = None  # type: ignore

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.base import OptimizerResult, create_optimizer_result
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.observations import OBSERVATION_DIM, extract_observation_vector
from rl.rewards import compute_rl_step_reward
from rl.baseline_cache import BaselineCache


class CableRingEnv(gym.Env):
    """Gymnasium environment for learning operator selection policies in ALNS."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        dataset_manager: Optional[Any] = None,
        plan_ids: Optional[List[int]] = None,
        curriculum_scheduler: Optional[Any] = None,
        max_steps_per_episode: int = 25,
        cooling_rate: float = 0.95,
        initial_temperature: float = 10.0,
        random_seed: int = 42,
    ) -> None:
        super().__init__()
        self.dataset = dataset_manager
        self.plan_ids = plan_ids or []
        self.curriculum = curriculum_scheduler
        self.max_steps = max_steps_per_episode
        self.cooling_rate = cooling_rate
        self.initial_temperature = initial_temperature
        self.rng = random.Random(random_seed)

        # Base portfolio optimizer for pristine initial state generation
        self.base_opt = PortfolioHybridOptimizer()
        self.cache = BaselineCache.get_instance()

        # Spaces
        if spaces is not None:
            self.action_space = spaces.Discrete(NUM_OPERATORS)
            self.observation_space = spaces.Box(
                low=-1.0,
                high=1.0,
                shape=(OBSERVATION_DIM,),
                dtype=np.float32,
            )
        else:
            self.action_space = None
            self.observation_space = None

        # Episode state variables
        self.current_plan: Optional[MetricFloorPlan] = None
        self.topology: Optional[Any] = None
        self.door_graph: Optional[Any] = None

        self.current_state: Optional[ALNSState] = None
        self.incumbent_state: Optional[ALNSState] = None
        self.baseline_J: float = 0.0
        self.baseline_H: int = 0
        self.baseline_result: Optional[OptimizerResult] = None

        self.temperature: float = initial_temperature
        self.step_idx: int = 0
        self.stagnation_steps: int = 0
        self.operator_success_counts: List[int] = [0] * NUM_OPERATORS
        self.operator_call_counts: List[int] = [0] * NUM_OPERATORS
        self.t_start_episode: float = 0.0

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset environment to initial state on a sampled floor plan."""
        if seed is not None:
            self.rng = random.Random(seed)

        # Retry up to 5 times if a sampled plan is malformed or unsolvable
        for attempt in range(5):
            # 1. Select floor plan
            target_pid: Optional[int] = None
            if options and "plan_id" in options and attempt == 0:
                target_pid = int(options["plan_id"])
            elif self.curriculum is not None:
                target_pid = self.curriculum.sample_plan_id()
            elif self.plan_ids:
                target_pid = self.rng.choice(self.plan_ids)
            elif self.dataset is not None:
                target_pid = self.dataset.sample_plan_id(rng=self.rng)
            else:
                target_pid = 14433

            try:
                if self.dataset is not None:
                    self.current_plan = self.dataset.get_plan(target_pid)
                else:
                    from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
                    self.current_plan = DatasetRepository.get_instance().get_floor_plan(target_pid)

                # Pre-validation guard: reject plans with any eligible room > 100.0 m^2
                if any(r.is_eligible and r.area_m2 > 100.001 for r in self.current_plan.rooms.values()):
                    if self.dataset is not None and hasattr(self.dataset, "quarantined_plans"):
                        self.dataset.quarantined_plans[target_pid] = "Eligible room exceeds 100m² maximum hub capacity"
                    continue

                # 2. Extract topology and door graph
                portals = extract_all_portals(self.current_plan)
                self.topology = MacroTopologyGraph.build_topology(self.current_plan, portals)
                self.door_graph = build_door_connectivity_graph(self.current_plan, self.topology)

                # 3. Retrieve or compute initial incumbent with Persistent BaselineCache
                is_valid, cached_res, reason = self.cache.get_or_compute(
                    self.current_plan,
                    self.base_opt,
                )

                if is_valid and cached_res is not None and cached_res.bundle is not None and cached_res.partition is not None:
                    self.baseline_result = cached_res
                    break
                else:
                    if self.dataset is not None and hasattr(self.dataset, "quarantined_plans"):
                        self.dataset.quarantined_plans[target_pid] = reason or "Infeasible baseline"
            except Exception:
                pass
        else:
            # Fallback to known good canonical plan 14433
            from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
            self.current_plan = DatasetRepository.get_instance().get_floor_plan(14433)
            portals = extract_all_portals(self.current_plan)
            self.topology = MacroTopologyGraph.build_topology(self.current_plan, portals)
            self.door_graph = build_door_connectivity_graph(self.current_plan, self.topology)
            _, self.baseline_result, _ = self.cache.get_or_compute(self.current_plan, self.base_opt)

        zones = [set(h.assigned_rooms) for h in self.baseline_result.partition.hubs]
        self.current_state = ALNSState(
            zones=zones,
            bundle=self.baseline_result.bundle,
            partition=self.baseline_result.partition,
            cost_J=self.baseline_result.total_cost_J,
            h_count=self.baseline_result.h_count,
            area_gap=self.baseline_result.area_gap_m2,
            is_valid=self.baseline_result.is_valid,
        )
        self.incumbent_state = self.current_state.clone()
        self.baseline_J = self.current_state.cost_J
        self.baseline_H = self.current_state.h_count

        # 4. Reset search variables
        self.temperature = self.initial_temperature
        self.step_idx = 0
        self.stagnation_steps = 0
        self.operator_success_counts = [0] * NUM_OPERATORS
        self.operator_call_counts = [0] * NUM_OPERATORS
        self.t_start_episode = time.perf_counter()

        obs = self._get_obs()
        info = {
            "plan_id": self.current_plan.plan_id,
            "baseline_J": self.baseline_J,
            "baseline_H": self.baseline_H,
            "incumbent_J": self.incumbent_state.cost_J,
            "incumbent_H": self.incumbent_state.h_count,
        }
        return obs, info

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Apply discrete ALNS operator and transition to next state."""
        self.step_idx += 1
        if self.curriculum is not None:
            self.curriculum.step(1)

        op_idx = int(action)
        self.operator_call_counts[op_idx] += 1

        prev_state = self.current_state.clone()

        # Execute operator
        cand_state, was_applied, op_name = apply_alns_operator(
            operator_idx=op_idx,
            state=self.current_state,
            plan=self.current_plan,
            topology=self.topology,
            door_graph=self.door_graph,
            rng=self.rng,
        )

        is_valid = cand_state.is_valid if cand_state is not None else False

        # Lexicographical check vs incumbent
        is_strictly_better = False
        if is_valid and was_applied:
            if cand_state.h_count < self.incumbent_state.h_count:
                is_strictly_better = True
            elif cand_state.h_count == self.incumbent_state.h_count and cand_state.cost_J < self.incumbent_state.cost_J - 1e-4:
                is_strictly_better = True

        if is_strictly_better:
            self.incumbent_state = cand_state.clone()
            self.current_state = cand_state.clone()
            self.operator_success_counts[op_idx] += 1
            self.stagnation_steps = 0
        elif is_valid and was_applied:
            # Simulated Annealing acceptance for intermediate search
            delta_j = cand_state.cost_J - self.current_state.cost_J
            if cand_state.h_count == self.current_state.h_count:
                accept_prob = math.exp(-delta_j / max(0.1, self.temperature)) if delta_j > 0 else 1.0
                if self.rng.random() < accept_prob:
                    self.current_state = cand_state.clone()
                    if delta_j < 0:
                        self.operator_success_counts[op_idx] += 1
            self.stagnation_steps += 1
        else:
            self.stagnation_steps += 1

        # Reward computation
        reward, _ = compute_rl_step_reward(
            prev_state=prev_state,
            curr_state=cand_state if is_valid else prev_state,
            incumbent_state=self.incumbent_state,
            baseline_J=self.baseline_J,
            baseline_H=self.baseline_H,
            was_applied=was_applied,
            is_valid=is_valid,
        )

        self.temperature *= self.cooling_rate

        terminated = (self.step_idx >= self.max_steps)
        truncated = False

        obs = self._get_obs()
        info = {
            "step": self.step_idx,
            "operator": op_name,
            "was_applied": was_applied,
            "is_valid": is_valid,
            "is_better": is_strictly_better,
            "current_J": self.current_state.cost_J,
            "incumbent_J": self.incumbent_state.cost_J,
            "incumbent_H": self.incumbent_state.h_count,
            "stagnation": self.stagnation_steps,
        }

        return obs, reward, terminated, truncated, info

    def _get_obs(self) -> np.ndarray:
        return extract_observation_vector(
            state=self.current_state,
            incumbent=self.incumbent_state,
            baseline_J=self.baseline_J,
            baseline_H=self.baseline_H,
            step_idx=self.step_idx,
            max_steps=self.max_steps,
            temperature=self.temperature,
            initial_temperature=self.initial_temperature,
            stagnation_steps=self.stagnation_steps,
            operator_success_counts=self.operator_success_counts,
            operator_call_counts=self.operator_call_counts,
            plan=self.current_plan,
        )

    def get_incumbent_optimizer_result(self, algorithm_name: str = "PPO_ALNS") -> OptimizerResult:
        """Return the strictly validated OptimizerResult corresponding to incumbent."""
        runtime = time.perf_counter() - self.t_start_episode
        return create_optimizer_result(
            algorithm_name=algorithm_name,
            plan=self.current_plan,
            bundle=self.incumbent_state.bundle,
            partition=self.incumbent_state.partition,
            solver_status="OPTIMAL",
            runtime_s=runtime,
            diagnostic_info={
                "steps": self.step_idx,
                "initial_J": round(self.baseline_J, 4),
                "final_J": round(self.incumbent_state.cost_J, 4),
                "delta_J": round(self.baseline_J - self.incumbent_state.cost_J, 4),
            },
        )
