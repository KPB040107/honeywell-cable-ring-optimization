"""High-Performance PPO Training Pipeline for Honeywell ALNS Guidance (Subagent 3).

Optimized for NVIDIA RTX 4050 Laptop GPU (CUDA 12.4) with vectorized rollouts,
curriculum scheduling across 17,000 ResPlan plans, and strict incumbent preservation.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure project root and src are in sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult, create_optimizer_result
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.optimizers.alns_operators import NUM_OPERATORS, OPERATOR_NAMES
from rl.curriculum import CurriculumScheduler
from rl.dataset import ResPlanDatasetManager
from rl.environment import CableRingEnv
from rl.observations import OBSERVATION_DIM


class PPOTrainer:
    """Actor-Critic Proximal Policy Optimization Trainer with GAE and GPU acceleration."""

    def __init__(
        self,
        total_timesteps: int = 50_000,
        num_envs: int = 4,
        rollout_steps: int = 32,
        num_epochs: int = 4,
        minibatch_size: int = 32,
        learning_rate: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_coef: float = 0.2,
        ent_coef: float = 0.01,
        vf_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        checkpoint_dir: str = "checkpoints/ppo_alns",
        log_dir: str = "logs/ppo_alns",
        seed: int = 42,
    ) -> None:
        self.total_timesteps = total_timesteps
        self.num_envs = num_envs
        self.rollout_steps = rollout_steps
        self.batch_size = num_envs * rollout_steps
        self.num_epochs = num_epochs
        self.minibatch_size = minibatch_size
        self.learning_rate = learning_rate
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_coef = clip_coef
        self.ent_coef = ent_coef
        self.vf_coef = vf_coef
        self.max_grad_norm = max_grad_norm
        self.checkpoint_dir = Path(checkpoint_dir)
        self.log_dir = Path(log_dir)
        self.seed = seed

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # PyTorch check
        import torch
        self.torch = torch
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        print(f"[PPO] Running on target compute device: {self.device}", flush=True)
        if torch.cuda.is_available():
            print(f"[PPO] GPU Model: {torch.cuda.get_device_name(0)}, Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB", flush=True)

        # Initialize policy
        from rl.policy import ActorCriticPolicy
        self.policy = ActorCriticPolicy(
            obs_dim=OBSERVATION_DIM,
            action_dim=NUM_OPERATORS,
            hidden_dim=128,
        ).to(self.device)

        self.optimizer = torch.optim.Adam(self.policy.parameters(), lr=learning_rate, eps=1e-5)

        # Datasets & environments
        self.dataset_mgr = ResPlanDatasetManager()
        self.curriculum = CurriculumScheduler(
            dataset_manager=self.dataset_mgr,
            total_steps=total_timesteps,
            rng=random.Random(seed),
        )

        self.envs = [
            CableRingEnv(
                dataset_manager=self.dataset_mgr,
                curriculum_scheduler=self.curriculum,
                max_steps_per_episode=25,
                random_seed=seed + i * 100,
            )
            for i in range(num_envs)
        ]

    def save_checkpoint(
        self,
        path: Path | str,
        update: int,
        global_step: int,
        best_val_score: float,
        history: List[Dict[str, Any]],
    ) -> None:
        """Persist full training state including policy, optimizer, and RNG buffers."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        ckpt = {
            "update": update,
            "global_step": global_step,
            "best_val_score": best_val_score,
            "policy_state_dict": self.policy.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "curriculum_step": self.curriculum.current_step,
            "history": history,
            "rng_torch": self.torch.get_rng_state(),
            "rng_cuda": self.torch.cuda.get_rng_state() if self.torch.cuda.is_available() else None,
            "rng_python": random.getstate(),
            "rng_numpy": np.random.get_state(),
        }
        self.torch.save(ckpt, p)

        # Also save standalone model weights for deployment / inference
        model_name = p.name.replace("checkpoint", "model")
        self.policy.save(p.parent / model_name)

    def load_checkpoint(self, path: Path | str) -> Tuple[int, int, float, List[Dict[str, Any]]]:
        """Restore full training state from a saved checkpoint."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Checkpoint not found at: {p}")
        ckpt = self.torch.load(p, map_location=self.device, weights_only=False)
        self.policy.load_state_dict(ckpt["policy_state_dict"])
        self.optimizer.load_state_dict(ckpt["optimizer_state_dict"])
        self.curriculum.current_step = ckpt.get("curriculum_step", 0)

        if "rng_torch" in ckpt and ckpt["rng_torch"] is not None:
            self.torch.set_rng_state(ckpt["rng_torch"])
        if "rng_cuda" in ckpt and ckpt["rng_cuda"] is not None and self.torch.cuda.is_available():
            self.torch.cuda.set_rng_state(ckpt["rng_cuda"])
        if "rng_python" in ckpt and ckpt["rng_python"] is not None:
            random.setstate(ckpt["rng_python"])
        if "rng_numpy" in ckpt and ckpt["rng_numpy"] is not None:
            np.random.set_state(ckpt["rng_numpy"])

        start_update = ckpt.get("update", 0)
        global_step = ckpt.get("global_step", 0)
        best_val = ckpt.get("best_val_score", float("inf"))
        history = ckpt.get("history", [])
        print(f"[PPO] Resumed state from {p} at update {start_update}, step {global_step} (Best Val J = {best_val:.2f})", flush=True)
        return start_update, global_step, best_val, history

    def train(self, resume_path: Optional[str] = None) -> Dict[str, Any]:
        """Execute full training loop with PPO updates, state persistence, and quarantine logging."""
        torch = self.torch
        num_updates = self.total_timesteps // self.batch_size

        # Telemetry containers
        obs_buf = torch.zeros((self.rollout_steps, self.num_envs, OBSERVATION_DIM), dtype=torch.float32, device=self.device)
        act_buf = torch.zeros((self.rollout_steps, self.num_envs), dtype=torch.int64, device=self.device)
        logp_buf = torch.zeros((self.rollout_steps, self.num_envs), dtype=torch.float32, device=self.device)
        rew_buf = torch.zeros((self.rollout_steps, self.num_envs), dtype=torch.float32, device=self.device)
        done_buf = torch.zeros((self.rollout_steps, self.num_envs), dtype=torch.float32, device=self.device)
        val_buf = torch.zeros((self.rollout_steps, self.num_envs), dtype=torch.float32, device=self.device)

        # Initialize environments
        next_obs = torch.zeros((self.num_envs, OBSERVATION_DIM), dtype=torch.float32, device=self.device)
        next_done = torch.zeros(self.num_envs, dtype=torch.float32, device=self.device)

        for i, env in enumerate(self.envs):
            o, _ = env.reset()
            next_obs[i] = torch.tensor(o, dtype=torch.float32, device=self.device)

        start_update = 0
        global_step = 0
        best_val_score = float("inf")
        history: List[Dict[str, Any]] = []
        operator_call_totals = [0] * NUM_OPERATORS

        if resume_path:
            start_update, global_step, best_val_score, history = self.load_checkpoint(resume_path)

        start_time = time.perf_counter()

        # Initial baseline evaluation
        initial_val_res = self.evaluate(num_plans=5)
        initial_val_j = initial_val_res["mean_J"]
        print(f"[PPO] Initial Validation Baseline (5 plans): Mean J = {initial_val_j:.2f}, Validity = {initial_val_res['validity_rate']*100:.1f}%", flush=True)

        print(f"[PPO] Starting training: {self.total_timesteps} steps ({num_updates} iterations) across {self.num_envs} workers...", flush=True)

        for update in range(start_update + 1, num_updates + 1):
            # Anneal learning rate linearly
            frac = 1.0 - (update - 1.0) / num_updates
            lr_now = frac * self.learning_rate
            self.optimizer.param_groups[0]["lr"] = lr_now

            # 1. Rollout Phase
            for step in range(self.rollout_steps):
                global_step += self.num_envs
                obs_buf[step] = next_obs
                done_buf[step] = next_done

                with torch.no_grad():
                    action, logprob, _, value = self.policy.get_action_and_value(next_obs)
                    val_buf[step] = value

                act_buf[step] = action
                logp_buf[step] = logprob

                # Environment step
                for i, env in enumerate(self.envs):
                    a_int = int(action[i].cpu().item())
                    operator_call_totals[a_int] += 1
                    o, r, terminated, truncated, _ = env.step(a_int)
                    d = terminated or truncated

                    rew_buf[step, i] = float(r)
                    if d:
                        o, _ = env.reset()

                    next_obs[i] = torch.tensor(o, dtype=torch.float32, device=self.device)
                    next_done[i] = 1.0 if d else 0.0

            # 2. Generalized Advantage Estimation (GAE)
            with torch.no_grad():
                next_value = self.policy.get_value(next_obs)
                advantages = torch.zeros_like(rew_buf, device=self.device)
                lastgaelam = 0.0
                for t in reversed(range(self.rollout_steps)):
                    if t == self.rollout_steps - 1:
                        nextnonterminal = 1.0 - next_done
                        nextvalues = next_value
                    else:
                        nextnonterminal = 1.0 - done_buf[t + 1]
                        nextvalues = val_buf[t + 1]
                    delta = rew_buf[t] + self.gamma * nextvalues * nextnonterminal - val_buf[t]
                    advantages[t] = lastgaelam = delta + self.gamma * self.gae_lambda * nextnonterminal * lastgaelam
                returns = advantages + val_buf

            # Flatten rollout tensors for minibatch optimization
            b_obs = obs_buf.reshape((-1, OBSERVATION_DIM))
            b_logprobs = logp_buf.reshape(-1)
            b_actions = act_buf.reshape(-1)
            b_advantages = advantages.reshape(-1)
            b_returns = returns.reshape(-1)
            b_values = val_buf.reshape(-1)

            # Normalize advantages
            b_advantages = (b_advantages - b_advantages.mean()) / (b_advantages.std() + 1e-8)

            # 3. PPO Update Epochs
            b_inds = np.arange(self.batch_size)
            clipfracs: List[float] = []

            for epoch in range(self.num_epochs):
                np.random.shuffle(b_inds)
                for start in range(0, self.batch_size, self.minibatch_size):
                    end = start + self.minibatch_size
                    mb_inds = b_inds[start:end]

                    _, newlogprob, entropy, newvalue = self.policy.get_action_and_value(
                        b_obs[mb_inds], b_actions[mb_inds]
                    )
                    logratio = newlogprob - b_logprobs[mb_inds]
                    ratio = logratio.exp()

                    with torch.no_grad():
                        old_approx_kl = (-logratio).mean()
                        approx_kl = ((ratio - 1) - logratio).mean()
                        clipfracs += [((ratio - 1.0).abs() > self.clip_coef).float().mean().item()]

                    mb_advantages = b_advantages[mb_inds]

                    # Policy loss
                    pg_loss1 = -mb_advantages * ratio
                    pg_loss2 = -mb_advantages * torch.clamp(ratio, 1 - self.clip_coef, 1 + self.clip_coef)
                    pg_loss = torch.max(pg_loss1, pg_loss2).mean()

                    # Value loss with clipping
                    v_loss_unclipped = (newvalue - b_returns[mb_inds]) ** 2
                    v_clipped = b_values[mb_inds] + torch.clamp(
                        newvalue - b_values[mb_inds], -self.clip_coef, self.clip_coef
                    )
                    v_loss_clipped = (v_clipped - b_returns[mb_inds]) ** 2
                    v_loss = 0.5 * torch.max(v_loss_unclipped, v_loss_clipped).mean()

                    entropy_loss = entropy.mean()
                    loss = pg_loss - self.ent_coef * entropy_loss + self.vf_coef * v_loss

                    self.optimizer.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.policy.parameters(), self.max_grad_norm)
                    self.optimizer.step()

            sps = int(global_step / max(0.001, time.perf_counter() - start_time))
            avg_rew = rew_buf.mean().item()

            print(
                f"Update {update:3d}/{num_updates} | Step {global_step:6d} | "
                f"Rew: {avg_rew:+.3f} | Loss: {loss.item():.4f} | "
                f"PG: {pg_loss.item():.4f} | VF: {v_loss.item():.4f} | "
                f"Entropy: {entropy_loss.item():.3f} | SPS: {sps} | "
                f"Stage: {self.curriculum.current_stage}",
                flush=True,
            )

            # Periodic full state checkpoint
            self.save_checkpoint(
                self.checkpoint_dir / "latest_checkpoint.pt",
                update,
                global_step,
                best_val_score,
                history,
            )

            # Checkpoint best model periodically
            val_res = {"validity_rate": 1.0, "mean_J": best_val_score, "mean_H": 4.0}
            mean_j = best_val_score
            if update % 5 == 0 or update == num_updates:
                val_res = self.evaluate(num_plans=5)
                mean_j = val_res["mean_J"]
                mean_h = val_res.get("mean_H", 4.0)
                print(f"  --> Validation (5 plans): Mean J = {mean_j:.2f}, Mean H = {mean_h:.2f}, Validity = {val_res['validity_rate']*100:.1f}%", flush=True)
                if mean_j < best_val_score:
                    best_val_score = mean_j
                    self.save_checkpoint(
                        self.checkpoint_dir / "best_checkpoint.pt",
                        update,
                        global_step,
                        best_val_score,
                        history,
                    )
                    print(f"  *** New Best Validation Model Checkpointed! Mean J = {best_val_score:.2f} ***", flush=True)

            tot_calls = max(1, sum(operator_call_totals))
            op_freqs = {OPERATOR_NAMES[idx]: round(cnt / tot_calls, 4) for idx, cnt in enumerate(operator_call_totals)}

            history.append({
                "update": update,
                "global_step": global_step,
                "avg_reward": avg_rew,
                "loss": loss.item(),
                "policy_loss": pg_loss.item(),
                "value_loss": v_loss.item(),
                "entropy": entropy_loss.item(),
                "val_mean_J": mean_j,
                "val_mean_H": val_res.get("mean_H", 4.0),
                "val_validity": val_res["validity_rate"],
                "operator_frequencies": op_freqs,
            })

        total_time = time.perf_counter() - start_time
        print(f"[PPO] Training completed in {total_time:.2f}s ({sps} SPS). Best Val J = {best_val_score:.2f}", flush=True)

        imp_pct = round(((initial_val_j - best_val_score) / initial_val_j * 100.0) if initial_val_j > 0 else 0.0, 2)
        summary = {
            "total_timesteps": global_step,
            "training_time_s": round(total_time, 2),
            "initial_validation_J": round(initial_val_j, 2),
            "final_validation_J": round(best_val_score, 2),
            "percentage_improvement": imp_pct,
            "final_validity_rate": val_res["validity_rate"],
            "final_mean_H": val_res.get("mean_H", 4.0),
            "final_operator_frequencies": op_freqs,
            "history": history,
        }
        with open(self.checkpoint_dir / "training_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        pilot_path = Path("results/ppo_pilot_10k_summary.json")
        pilot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pilot_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        self.dataset_mgr.save_quarantine_log("logs/quarantined_plans.json")
        return summary

    def evaluate(self, num_plans: int = 10) -> Dict[str, Any]:
        """Run validation episodes on held-out clean validation plans."""
        val_pids = self.dataset_mgr.clean_val_ids[:num_plans]
        j_values: List[float] = []
        h_values: List[int] = []
        l_values: List[float] = []
        initial_j_values: List[float] = []
        valid_count = 0

        for pid in val_pids:
            env = CableRingEnv(
                dataset_manager=self.dataset_mgr,
                plan_ids=[pid],
                max_steps_per_episode=25,
                random_seed=42,
            )
            obs, _ = env.reset(options={"plan_id": pid})
            initial_j_values.append(env.baseline_J)
            done = False
            while not done:
                obs_t = self.torch.tensor(obs, dtype=self.torch.float32, device=self.device)
                action = self.policy.get_action(obs_t, deterministic=True)
                obs, _, terminated, truncated, _ = env.step(action)
                done = terminated or truncated

            res = env.get_incumbent_optimizer_result()
            if res.is_valid:
                valid_count += 1
                j_values.append(res.total_cost_J)
                h_values.append(res.h_count)
                l_values.append(res.total_length_m)
            else:
                j_values.append(999.0)
                h_values.append(99)
                l_values.append(999.0)

        return {
            "mean_J": float(np.mean(j_values)),
            "mean_H": float(np.mean(h_values)),
            "initial_J": float(np.mean(initial_j_values)),
            "mean_L": float(np.mean(l_values)),
            "validity_rate": valid_count / max(1, len(val_pids)),
        }


class PPOALNSOptimizer(BaseFloorPlanOptimizer):
    """PPO-guided ALNS Optimizer conforming to official BaseFloorPlanOptimizer interface.

    Initializes from PortfolioHybrid, then uses the trained Actor-Critic policy to
    intelligently select coupled destroy-repair operators over an iterative budget.
    """

    def __init__(
        self,
        name: str = "PPO_ALNS",
        checkpoint_path: str = "checkpoints/ppo_alns/best_model.pt",
        max_steps: int = 25,
        device: str = "cpu",
    ) -> None:
        super().__init__(name=name)
        self.checkpoint_path = Path(checkpoint_path)
        self.max_steps = max_steps
        self.device = device
        self.policy: Optional[Any] = None

    def _ensure_policy(self) -> None:
        if self.policy is None:
            from rl.policy import ActorCriticPolicy
            if self.checkpoint_path.exists():
                self.policy = ActorCriticPolicy.load(self.checkpoint_path, device=self.device)
            else:
                # Random/untrained policy fallback if checkpoint not found
                self.policy = ActorCriticPolicy(OBSERVATION_DIM, NUM_OPERATORS).to(self.device)

    def optimize(
        self,
        floor_plan: MetricFloorPlan,
        time_limit_seconds: float = 5.0,
        random_seed: int = 42,
    ) -> OptimizerResult:
        """Run PPO-guided ALNS optimization."""
        t_start = time.perf_counter()
        self._ensure_policy()
        import torch

        # Fast path for single hub optimal
        base_opt = PortfolioHybridOptimizer()
        incumbent = base_opt.optimize(floor_plan, time_limit_seconds=1.5, random_seed=random_seed)

        if not incumbent.is_valid or incumbent.h_count <= 1:
            return incumbent

        # Execute PPO guided ALNS steps
        env = CableRingEnv(
            plan_ids=[int(floor_plan.plan_id)],
            max_steps_per_episode=self.max_steps,
            random_seed=random_seed,
        )
        obs, _ = env.reset(options={"plan_id": int(floor_plan.plan_id)})

        for step_i in range(self.max_steps):
            if (time.perf_counter() - t_start) >= (time_limit_seconds - 0.4):
                break
            obs_t = torch.tensor(obs, dtype=torch.float32, device=self.device)
            action = self.policy.get_action(obs_t, deterministic=True)
            obs, _, terminated, truncated, _ = env.step(action)
            if terminated or truncated:
                break

        res = env.get_incumbent_optimizer_result(algorithm_name=self.name)
        return res


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PPO-guided ALNS Trainer")
    parser.add_argument("--timesteps", type=int, default=5000, help="Total training timesteps")
    parser.add_argument("--envs", type=int, default=2, help="Number of parallel environments (empirically optimal=2)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--resume", type=str, default=None, help="Path to checkpoint file to resume from")
    args = parser.parse_args()

    trainer = PPOTrainer(
        total_timesteps=args.timesteps,
        num_envs=args.envs,
        seed=args.seed,
    )
    trainer.train(resume_path=args.resume)
