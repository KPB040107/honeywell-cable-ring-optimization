r"""LinUCB Contextual Bandit for ALNS Operator Selection (Subagent 2).

Implements the LinUCB (Linear Upper Confidence Bound) algorithm with disjoint
linear models per arm for guided operator selection in Adaptive Large Neighborhood Search.

Mathematical Formulation:
- Context feature vector: x_t in R^d (d = 24, matching OBSERVATION_DIM).
- Arms: a in {0, ..., K-1} (K = 8, matching alns_operators.NUM_OPERATORS).
- Ridge regularized design matrix per arm a:
    A_a = \lambda I_d + \sum_{\tau \in T_a} x_\tau x_\tau^\top
- Bias vector per arm a:
    b_a = \sum_{\tau \in T_a} r_\tau x_\tau
- Ridge regression coefficient:
    \theta_a = A_a^{-1} b_a
- Upper Confidence Bound score:
    p_{t, a} = \theta_a^\top x_t + \alpha \sqrt{x_t^\top A_a^{-1} x_t}
- Sherman-Morrison rank-1 online inverse update (O(d^2)):
    A_{a, new}^{-1} = A_a^{-1} - \frac{(A_a^{-1} x)(x^\top A_a^{-1})}{1 + x^\top A_a^{-1} x}
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from resplan_router.optimizers.alns_operators import ALNSState, NUM_OPERATORS
from rl.observations import OBSERVATION_DIM


def compute_linucb_reward(
    prev_state: ALNSState,
    curr_state: Optional[ALNSState],
    incumbent_state: ALNSState,
    baseline_J: float,
    baseline_H: int,
    was_applied: bool,
    is_valid: bool,
    step_penalty: float = -0.01,
) -> float:
    """Compute lexicographically aligned reward with symmetric delta J and incumbent bonuses.

    Reward Components:
    1. Feasibility:
       - Invalid candidate solution: -1.0
       - Inapplicable operator (was_applied=False): -0.05
    2. Primary Hub Priority (H):
       - Hub reduction: +5.0 * (prev_H - curr_H)
       - Hub increase: -10.0 (strictly forbidden trade-off)
    3. Symmetric Normalized Delta J:
       - delta_j = prev_J - curr_J
       - Normalized by max(1.0, baseline_J), symmetric for improvements vs regressions
       - Clipped to [-2.0, 2.0] for stable linear regression
    4. Global Incumbent Discovery:
       - Discovered strictly better hub count H*: +2.0
       - Discovered strictly better cost J* at same H*: +1.0
    5. Step Penalty:
       - -0.01 to encourage faster convergence

    Returns:
        float reward value.
    """
    if not is_valid or curr_state is None:
        return -1.0

    if not was_applied:
        return -0.05

    reward = step_penalty
    j_scale = max(1.0, baseline_J)

    # 1. Primary Hub count priority
    if curr_state.h_count < prev_state.h_count:
        reward += 5.0 * float(prev_state.h_count - curr_state.h_count)
    elif curr_state.h_count > prev_state.h_count:
        reward -= 10.0

    # 2. Symmetric Delta J (only if H <= prev_H)
    if curr_state.h_count <= prev_state.h_count:
        delta_j = prev_state.cost_J - curr_state.cost_J
        norm_delta_j = np.clip(delta_j / j_scale, -2.0, 2.0)
        reward += float(norm_delta_j)

    # 3. Global incumbent improvement bonus
    if curr_state.h_count < incumbent_state.h_count:
        reward += 2.0
    elif (
        curr_state.h_count == incumbent_state.h_count
        and curr_state.cost_J < incumbent_state.cost_J - 1e-4
    ):
        reward += 1.0

    return float(reward)


class LinUCBBandit:
    """Contextual Multi-Armed Bandit with LinUCB (Disjoint Linear Models).

    Maintains ridge regression matrices (A_a, b_a, A_a^{-1}, \theta_a) for each arm,
    updating the inverse via Sherman-Morrison rank-1 updates for O(d^2) runtime.
    Supports action masking for filtering inapplicable ALNS operators, model persistence,
    and extensive telemetry tracking.
    """

    def __init__(
        self,
        feature_dim: int = OBSERVATION_DIM,
        num_arms: int = NUM_OPERATORS,
        alpha: float = 0.5,
        lambda_reg: float = 1.0,
        use_sherman_morrison: bool = True,
        reinvert_interval: int = 500,
    ) -> None:
        r"""Initialize LinUCB contextual bandit.

        Args:
            feature_dim: Dimension d of observation vector (default 24).
            num_arms: Number of candidate arms K (default 8).
            alpha: UCB exploration confidence parameter \alpha > 0 (default 0.5).
            lambda_reg: Ridge regularization parameter \lambda > 0 (default 1.0).
            use_sherman_morrison: Whether to use rank-1 Sherman-Morrison updates (default True).
            reinvert_interval: Frequency of exact matrix re-inversion to eliminate
                               floating point numerical drift (0 = disabled, default 500).
        """
        if feature_dim <= 0:
            raise ValueError(f"feature_dim must be positive, got {feature_dim}")
        if num_arms <= 0:
            raise ValueError(f"num_arms must be positive, got {num_arms}")
        if alpha < 0.0:
            raise ValueError(f"alpha must be non-negative, got {alpha}")
        if lambda_reg <= 0.0:
            raise ValueError(f"lambda_reg must be strictly positive, got {lambda_reg}")

        self.d = feature_dim
        self.k = num_arms
        self.alpha = float(alpha)
        self.lambda_reg = float(lambda_reg)
        self.use_sherman_morrison = bool(use_sherman_morrison)
        self.reinvert_interval = int(reinvert_interval)

        # Per-arm ridge matrices:
        # A_a = \lambda I_d
        self.A = np.zeros((self.k, self.d, self.d), dtype=np.float64)
        self.A_inv = np.zeros((self.k, self.d, self.d), dtype=np.float64)
        for a in range(self.k):
            self.A[a] = self.lambda_reg * np.eye(self.d, dtype=np.float64)
            self.A_inv[a] = (1.0 / self.lambda_reg) * np.eye(self.d, dtype=np.float64)

        # b_a = 0
        self.b = np.zeros((self.k, self.d), dtype=np.float64)
        # \theta_a = A_a^{-1} b_a = 0
        self.theta = np.zeros((self.k, self.d), dtype=np.float64)

        # Telemetry tracking
        self.pull_counts = np.zeros(self.k, dtype=np.int64)
        self.cumulative_rewards = np.zeros(self.k, dtype=np.float64)
        self.condition_numbers = np.full(self.k, 1.0, dtype=np.float64)
        self.reward_history: List[Dict[str, Any]] = []
        self.step_counter: int = 0

    def get_arm_score(
        self,
        arm: int,
        context: np.ndarray,
    ) -> Tuple[float, float, float]:
        """Compute UCB score, predicted mean, and uncertainty for a specific arm.

        Args:
            arm: Operator arm index in [0, K-1].
            context: Feature vector of shape (d,) or (d, 1).

        Returns:
            Tuple of (ucb_score, predicted_mean, uncertainty_bonus)
        """
        x = np.asarray(context, dtype=np.float64).reshape(-1)
        if x.shape[0] != self.d:
            raise ValueError(f"Expected context dim {self.d}, got {x.shape[0]}")

        theta_a = self.theta[arm]
        A_inv_a = self.A_inv[arm]

        # Predicted expected reward \theta_a^\top x
        pred_mean = float(np.dot(theta_a, x))

        # Quadratic form: x^\top A_a^{-1} x
        quad_form = float(np.dot(x, np.dot(A_inv_a, x)))
        # Guard against minor floating point precision errors < 0
        quad_form = max(0.0, quad_form)
        uncertainty = math.sqrt(quad_form)

        ucb_score = pred_mean + self.alpha * uncertainty
        return ucb_score, pred_mean, uncertainty

    def select_arm(
        self,
        context: np.ndarray,
        available_actions: Optional[Sequence[int]] = None,
        rng: Optional[random.Random] = None,
    ) -> Tuple[int, Dict[int, float]]:
        """Select arm using LinUCB decision rule with optional action masking.

        Args:
            context: Context feature vector of dimension d.
            available_actions: Optional list of available arm indices. If provided,
                               only these actions can be chosen.
            rng: Optional random number generator for tie-breaking.

        Returns:
            Tuple of (chosen_arm, scores_dict_by_arm).
        """
        x = np.asarray(context, dtype=np.float64).reshape(-1)
        if x.shape[0] != self.d:
            raise ValueError(f"Expected context dim {self.d}, got {x.shape[0]}")

        if available_actions is not None:
            valid_set = set(available_actions)
            if not valid_set:
                raise ValueError("available_actions sequence cannot be empty")
            for a in valid_set:
                if not (0 <= a < self.k):
                    raise ValueError(f"Action {a} in available_actions is outside [0, {self.k - 1}]")
        else:
            valid_set = set(range(self.k))

        scores: Dict[int, float] = {}
        for a in range(self.k):
            if a in valid_set:
                ucb_val, _, _ = self.get_arm_score(a, x)
                scores[a] = ucb_val
            else:
                scores[a] = float("-inf")

        # Find best action among available
        best_score = float("-inf")
        best_arms: List[int] = []

        for a in valid_set:
            sc = scores[a]
            if sc > best_score + 1e-9:
                best_score = sc
                best_arms = [a]
            elif abs(sc - best_score) <= 1e-9:
                best_arms.append(a)

        if len(best_arms) == 1:
            chosen = best_arms[0]
        else:
            if rng is not None:
                chosen = rng.choice(best_arms)
            else:
                chosen = random.choice(best_arms)

        return chosen, scores

    def update(
        self,
        arm: int,
        context: np.ndarray,
        reward: float,
    ) -> None:
        """Update LinUCB ridge model parameters for the chosen arm with observed context and reward.

        Applies Sherman-Morrison rank-1 update to A_a^{-1}:
            u = A_a^{-1} x
            A_a^{-1} <- A_a^{-1} - (u u^\top) / (1 + x^\top u)
        And updates \theta_a = A_a^{-1} b_a.

        Args:
            arm: Chosen arm index in [0, K-1].
            context: Context feature vector x_t of shape (d,).
            reward: Scalar observed feedback reward r_t.
        """
        if not (0 <= arm < self.k):
            raise ValueError(f"Arm index {arm} out of range [0, {self.k - 1}]")

        x = np.asarray(context, dtype=np.float64).reshape(-1)
        if x.shape[0] != self.d:
            raise ValueError(f"Expected context dim {self.d}, got {x.shape[0]}")

        r = float(reward)

        # Pre-update prediction for telemetry
        pred_mean_pre = float(np.dot(self.theta[arm], x))
        uncertainty_pre = math.sqrt(max(0.0, float(np.dot(x, np.dot(self.A_inv[arm], x)))))

        # Direct rank-1 update on design matrix A_a: A_a <- A_a + x x^\top
        self.A[arm] += np.outer(x, x)

        # Update b_a: b_a <- b_a + r x
        self.b[arm] += r * x

        # Inverse update
        if self.use_sherman_morrison:
            # u = A_a^{-1} x
            u = np.dot(self.A_inv[arm], x)
            denom = 1.0 + float(np.dot(x, u))
            if denom <= 0.0 or math.isnan(denom):
                # Fallback to direct solve if numerical singularity occurs
                self.reinvert_arm(arm)
            else:
                self.A_inv[arm] -= np.outer(u, u) / denom
                # Symmetrize A_a^{-1} to eliminate numerical drift
                self.A_inv[arm] = 0.5 * (self.A_inv[arm] + self.A_inv[arm].T)
                # Compute \theta_a = A_a^{-1} b_a
                self.theta[arm] = np.dot(self.A_inv[arm], self.b[arm])
        else:
            self.reinvert_arm(arm)

        # Step counter and telemetry
        self.step_counter += 1
        self.pull_counts[arm] += 1
        self.cumulative_rewards[arm] += r

        # Periodic condition number and re-inversion check
        if self.reinvert_interval > 0 and (self.step_counter % self.reinvert_interval == 0):
            self.reinvert_arm(arm)

        # Record condition number
        self.condition_numbers[arm] = self.compute_condition_number(arm)

        # Telemetry record (cap history to last 2000 steps to prevent unbound memory growth)
        if len(self.reward_history) >= 2000:
            self.reward_history.pop(0)

        self.reward_history.append(
            {
                "step": self.step_counter,
                "arm": arm,
                "predicted_mean": round(pred_mean_pre, 6),
                "uncertainty": round(uncertainty_pre, 6),
                "ucb_score": round(pred_mean_pre + self.alpha * uncertainty_pre, 6),
                "actual_reward": round(r, 6),
                "residual": round(r - pred_mean_pre, 6),
            }
        )

    def reinvert_arm(self, arm: int) -> None:
        """Perform exact matrix inversion and solve for arm to restore precision."""
        if not (0 <= arm < self.k):
            raise ValueError(f"Arm index {arm} out of range [0, {self.k - 1}]")
        self.A_inv[arm] = np.linalg.inv(self.A[arm])
        self.A_inv[arm] = 0.5 * (self.A_inv[arm] + self.A_inv[arm].T)
        self.theta[arm] = np.linalg.solve(self.A[arm], self.b[arm])

    def compute_condition_number(self, arm: int) -> float:
        r"""Compute 2-norm condition number \kappa(A_a) = \lambda_max / \lambda_min."""
        if not (0 <= arm < self.k):
            raise ValueError(f"Arm index {arm} out of range [0, {self.k - 1}]")
        return float(np.linalg.cond(self.A[arm]))

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive telemetry metrics for reporting and debugging."""
        mean_rewards = {}
        for a in range(self.k):
            cnt = int(self.pull_counts[a])
            mean_rewards[a] = float(self.cumulative_rewards[a] / cnt) if cnt > 0 else 0.0

        cond_nums = {a: float(self.condition_numbers[a]) for a in range(self.k)}
        theta_norms = {a: float(np.linalg.norm(self.theta[a])) for a in range(self.k)}

        # Recent residuals
        recent = self.reward_history[-50:] if self.reward_history else []
        mae = float(np.mean([abs(rec["residual"]) for rec in recent])) if recent else 0.0

        return {
            "total_pulls": int(self.step_counter),
            "pull_counts": {a: int(self.pull_counts[a]) for a in range(self.k)},
            "cumulative_rewards": {a: round(float(self.cumulative_rewards[a]), 4) for a in range(self.k)},
            "mean_rewards": {a: round(mean_rewards[a], 4) for a in range(self.k)},
            "condition_numbers": {a: round(cond_nums[a], 4) for a in range(self.k)},
            "theta_norms": {a: round(theta_norms[a], 4) for a in range(self.k)},
            "recent_residual_mae": round(mae, 4),
            "history_length": len(self.reward_history),
            "alpha": self.alpha,
            "lambda_reg": self.lambda_reg,
        }

    def reset_telemetry(self) -> None:
        """Clear telemetry history log while retaining learned parameters."""
        self.reward_history.clear()

    def save(self, path: Union[str, Path]) -> None:
        """Save bandit state to disk in .npz or .json format.

        Args:
            path: Target file path ending in .npz or .json. If no extension is given,
                  .npz is used by default.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "feature_dim": self.d,
            "num_arms": self.k,
            "alpha": self.alpha,
            "lambda_reg": self.lambda_reg,
            "use_sherman_morrison": self.use_sherman_morrison,
            "reinvert_interval": self.reinvert_interval,
            "step_counter": self.step_counter,
            "pull_counts": self.pull_counts.tolist(),
            "cumulative_rewards": self.cumulative_rewards.tolist(),
            "condition_numbers": self.condition_numbers.tolist(),
        }

        if p.suffix.lower() == ".json":
            payload = {
                "metadata": meta,
                "A": self.A.tolist(),
                "A_inv": self.A_inv.tolist(),
                "b": self.b.tolist(),
                "theta": self.theta.tolist(),
            }
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        else:
            target_path = p if p.suffix.lower() == ".npz" else p.with_suffix(".npz")
            np.savez_compressed(
                target_path,
                A=self.A,
                A_inv=self.A_inv,
                b=self.b,
                theta=self.theta,
                meta=json.dumps(meta),
            )

    def load(self, path: Union[str, Path]) -> None:
        """Load bandit state from a .npz or .json file.

        Args:
            path: Source file path.
        """
        p = Path(path)
        if not p.exists():
            # Check with default extension
            if p.with_suffix(".npz").exists():
                p = p.with_suffix(".npz")
            elif p.with_suffix(".json").exists():
                p = p.with_suffix(".json")
            else:
                raise FileNotFoundError(f"Bandit file not found: {path}")

        if p.suffix.lower() == ".json":
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
            meta = payload["metadata"]
            self.d = int(meta["feature_dim"])
            self.k = int(meta["num_arms"])
            self.alpha = float(meta["alpha"])
            self.lambda_reg = float(meta["lambda_reg"])
            self.use_sherman_morrison = bool(meta.get("use_sherman_morrison", True))
            self.reinvert_interval = int(meta.get("reinvert_interval", 500))
            self.step_counter = int(meta.get("step_counter", 0))

            self.A = np.asarray(payload["A"], dtype=np.float64)
            self.A_inv = np.asarray(payload["A_inv"], dtype=np.float64)
            self.b = np.asarray(payload["b"], dtype=np.float64)
            self.theta = np.asarray(payload["theta"], dtype=np.float64)

            self.pull_counts = np.asarray(meta.get("pull_counts", [0] * self.k), dtype=np.int64)
            self.cumulative_rewards = np.asarray(meta.get("cumulative_rewards", [0.0] * self.k), dtype=np.float64)
            self.condition_numbers = np.asarray(meta.get("condition_numbers", [1.0] * self.k), dtype=np.float64)
        else:
            with np.load(p, allow_pickle=False) as data:
                meta = json.loads(str(data["meta"]))
                self.d = int(meta["feature_dim"])
                self.k = int(meta["num_arms"])
                self.alpha = float(meta["alpha"])
                self.lambda_reg = float(meta["lambda_reg"])
                self.use_sherman_morrison = bool(meta.get("use_sherman_morrison", True))
                self.reinvert_interval = int(meta.get("reinvert_interval", 500))
                self.step_counter = int(meta.get("step_counter", 0))

                self.A = np.asarray(data["A"], dtype=np.float64)
                self.A_inv = np.asarray(data["A_inv"], dtype=np.float64)
                self.b = np.asarray(data["b"], dtype=np.float64)
                self.theta = np.asarray(data["theta"], dtype=np.float64)

                self.pull_counts = np.asarray(meta.get("pull_counts", [0] * self.k), dtype=np.int64)
                self.cumulative_rewards = np.asarray(meta.get("cumulative_rewards", [0.0] * self.k), dtype=np.float64)
                self.condition_numbers = np.asarray(meta.get("condition_numbers", [1.0] * self.k), dtype=np.float64)

    @classmethod
    def load_from_file(cls, path: Union[str, Path]) -> LinUCBBandit:
        """Create and populate a new LinUCBBandit instance from saved file."""
        inst = cls()
        inst.load(path)
        return inst
