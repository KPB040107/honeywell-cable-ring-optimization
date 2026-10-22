"""Observation Extraction and Normalization for PPO-guided ALNS (Agent 1).

Constructs a fixed 24-dimensional continuous feature vector normalized into [0.0, 1.0]
or [-1.0, 1.0], representing floor plan geometry, current candidate solution metrics,
incumbent tracking, optimization progress, and operator historical success.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import ALNSState, NUM_OPERATORS


OBSERVATION_DIM = 24


def extract_observation_vector(
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
    """Extract and normalize 24-dimensional state feature vector.

    Features:
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
    16-23: Operator success rates for operators 0..7 (successes / max(1, calls))

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
    obs[9] = np.clip(state.bundle.total_length_m / 300.0, 0.0, 1.0)
    obs[10] = np.clip(state.bundle.total_bends / 100.0, 0.0, 1.0)
    obs[11] = np.clip(state.bundle.total_crossings / 20.0, 0.0, 1.0)
    obs[12] = np.clip(state.bundle.total_overlap_m / 50.0, 0.0, 1.0)
    obs[13] = np.clip(state.area_gap / 100.0, 0.0, 1.0)

    # 6. Search dynamic state
    obs[14] = np.clip(stagnation_steps / 25.0, 0.0, 1.0)
    obs[15] = np.clip(step_idx / max(1, max_steps), 0.0, 1.0)

    # 7. Operator success rates (0..7)
    for op_i in range(NUM_OPERATORS):
        calls = operator_call_counts[op_i] if op_i < len(operator_call_counts) else 0
        succ = operator_success_counts[op_i] if op_i < len(operator_success_counts) else 0
        rate = (succ / calls) if calls > 0 else 0.5  # Prior neutral 0.5
        obs[16 + op_i] = np.clip(rate, 0.0, 1.0)

    return obs
