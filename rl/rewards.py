"""Lexicographical Reward Formulation for PPO-guided ALNS (Agent 1).

Implements the official Honeywell priority hierarchy:
1. Minimize Hubs H: Large reward for reducing H, severe penalty for increasing H.
2. Minimize J = L + 5X + O + 0.5B: Dense local improvement rewards and global best bonuses.
3. Preserve Feasibility: Strong penalty if any candidate violates official FloorPlanChecker rules.
4. Step Penalty: Small negative step cost (-0.02) to encourage fast convergence.
"""

from __future__ import annotations

from typing import Tuple
from resplan_router.optimizers.alns_operators import ALNSState


def compute_rl_step_reward(
    prev_state: ALNSState,
    curr_state: ALNSState,
    incumbent_state: ALNSState,
    baseline_J: float,
    baseline_H: int,
    was_applied: bool,
    is_valid: bool,
) -> Tuple[float, bool]:
    """Calculate reward for transitioning from prev_state to curr_state.

    Args:
        prev_state: State before operator application.
        curr_state: Candidate state after operator application.
        incumbent_state: Global best incumbent prior to this step.
        baseline_J: Starting PortfolioHybrid cost J0.
        baseline_H: Starting PortfolioHybrid hub count H0.
        was_applied: Whether operator was successfully executed.
        is_valid: Whether candidate passed FloorPlanChecker.

    Returns:
        (reward, is_new_incumbent)
    """
    if not is_valid:
        # Invalid candidate: strong negative feedback
        return -1.0, False

    if not was_applied:
        # Operator was inapplicable: minor penalty to avoid wasting budget
        return -0.05, False

    j_scale = max(1.0, baseline_J)
    step_penalty = -0.02
    reward = step_penalty

    # 1. Primary Hub Count H Priority
    if curr_state.h_count < prev_state.h_count:
        # Reduced hub count: massive reward
        reward += 5.0 * (prev_state.h_count - curr_state.h_count)
    elif curr_state.h_count > prev_state.h_count:
        # Increased hub count: severe penalty (never trade hubs for cost)
        reward -= 10.0

    # 2. Secondary J Cost Priority (only if H <= prev_H)
    if curr_state.h_count <= prev_state.h_count:
        # Local delta J improvement
        delta_j = prev_state.cost_J - curr_state.cost_J
        reward += delta_j / j_scale

    # 3. Global Incumbent Best Check
    is_new_incumbent = False
    if curr_state.h_count < incumbent_state.h_count:
        is_new_incumbent = True
        reward += 2.0
    elif curr_state.h_count == incumbent_state.h_count and curr_state.cost_J < incumbent_state.cost_J - 1e-4:
        is_new_incumbent = True
        reward += 1.0

    return float(reward), is_new_incumbent
