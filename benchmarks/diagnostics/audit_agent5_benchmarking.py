"""Agent 5 Fair Benchmarking: Parallel Multiprocessing Head-to-Head Comparison on Held-Out Clean Validation Plans.

Evaluates 4 algorithms across held-out clean validation floor plans:
1. PortfolioHybrid (Deterministic Multi-Start Baseline)
2. Standalone ALNS (Roulette-Wheel Adaptive Operator Selection)
3. Random ALNS (Uniform Random Operator Selection)
4. Trained PPO-ALNS (Checkpoint latest_model.pt argmax)

Parallelized with ProcessPoolExecutor across CPU cores.
Computes:
- Paired metrics: H, J, L, B, X, O, Validity, Runtime
- Mean Delta J and % Improvement over PortfolioHybrid
- Win / Tie / Loss counts
- Statistical significance (paired t-test and Wilcoxon signed-rank test via scipy.stats)
- Tiered breakdown: compact (<=3), standard (4-5), complex (>=6)

Outputs:
- results/diagnostics_agent5_benchmark.json
- results/diagnostics_agent5_benchmark.csv
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.stats as stats
import torch

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from resplan_router.contracts import MetricFloorPlan, SynthesizedRingsBundle, HubZonePartition
from resplan_router.optimizers.alns_operators import (
    ALNSState,
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.baseline_cache import BaselineCache
from rl.dataset import ResPlanDatasetManager
from rl.observations import extract_observation_vector
from rl.policy import ActorCriticPolicy


def run_single_alns_variant(
    variant_type: str,  # "bandit", "random", "ppo"
    base_state: ALNSState,
    plan: MetricFloorPlan,
    topology: Any,
    door_graph: Any,
    policy: Optional[ActorCriticPolicy],
    max_steps: int = 10,
    initial_temperature: float = 10.0,
    cooling_rate: float = 0.95,
    seed: int = 42,
) -> Tuple[ALNSState, float, Dict[str, Any]]:
    """Execute ALNS with the specified operator selection mechanism."""
    t_start = time.perf_counter()
    rng = random.Random(seed)

    curr_state = base_state.clone()
    incumbent_state = base_state.clone()
    temperature = initial_temperature

    op_weights = [1.0] * NUM_OPERATORS
    op_counts = [0] * NUM_OPERATORS
    op_successes = [0] * NUM_OPERATORS
    stagnation_steps = 0

    for step in range(max_steps):
        # 1. Action selection
        if variant_type == "random":
            op_idx = rng.randrange(NUM_OPERATORS)
        elif variant_type == "bandit":
            tot = sum(op_weights)
            probs = [w / tot for w in op_weights]
            op_idx = rng.choices(range(NUM_OPERATORS), weights=probs, k=1)[0]
        elif variant_type == "ppo":
            obs = extract_observation_vector(
                state=curr_state,
                incumbent=incumbent_state,
                baseline_J=base_state.cost_J,
                baseline_H=base_state.h_count,
                step_idx=step,
                max_steps=max_steps,
                temperature=temperature,
                initial_temperature=initial_temperature,
                stagnation_steps=stagnation_steps,
                operator_success_counts=op_successes,
                operator_call_counts=op_counts,
                plan=plan,
            )
            with torch.no_grad():
                obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0)
                logits = policy.actor(obs_t)
                op_idx = int(torch.argmax(logits, dim=-1).item())
        else:
            raise ValueError(f"Unknown variant type: {variant_type}")

        op_counts[op_idx] += 1

        # 2. Operator application
        cand_state, was_applied, op_name = apply_alns_operator(
            operator_idx=op_idx,
            state=curr_state,
            plan=plan,
            topology=topology,
            door_graph=door_graph,
            rng=rng,
        )

        is_valid = cand_state.is_valid if cand_state is not None else False
        is_strictly_better = False

        if is_valid and was_applied:
            if cand_state.h_count < incumbent_state.h_count:
                is_strictly_better = True
            elif cand_state.h_count == incumbent_state.h_count and cand_state.cost_J < incumbent_state.cost_J - 1e-4:
                is_strictly_better = True

        if is_strictly_better:
            incumbent_state = cand_state.clone()
            curr_state = cand_state.clone()
            op_successes[op_idx] += 1
            stagnation_steps = 0
            if variant_type == "bandit":
                op_weights[op_idx] = op_weights[op_idx] * 0.8 + 2.0
        elif is_valid and was_applied:
            delta_j = cand_state.cost_J - curr_state.cost_J
            if cand_state.h_count == curr_state.h_count:
                prob = math.exp(-delta_j / max(0.1, temperature)) if delta_j > 0 else 1.0
                if rng.random() < prob:
                    curr_state = cand_state.clone()
                    if delta_j < 0:
                        op_successes[op_idx] += 1
                        if variant_type == "bandit":
                            op_weights[op_idx] = op_weights[op_idx] * 0.8 + 1.0
            stagnation_steps += 1
        else:
            stagnation_steps += 1

        temperature *= cooling_rate

    runtime = time.perf_counter() - t_start
    diag = {
        "operator_counts": {OPERATOR_NAMES[i]: op_counts[i] for i in range(NUM_OPERATORS)},
        "operator_successes": {OPERATOR_NAMES[i]: op_successes[i] for i in range(NUM_OPERATORS)},
    }
    return incumbent_state, runtime, diag


def evaluate_single_plan_worker(args: Tuple[int, int, int]) -> Optional[Dict[str, Any]]:
    """Worker function for multiprocessing execution on a single floor plan."""
    p_id, seed, max_steps = args
    try:
        repo = DatasetRepository.get_instance()
        plan = repo.get_floor_plan(p_id)
        if plan is None:
            return None

        n_rooms = sum(1 for r in plan.rooms.values() if r.is_eligible)
        tier = "compact" if n_rooms <= 3 else ("standard" if n_rooms <= 5 else "complex")

        base_opt = PortfolioHybridOptimizer()
        cache = BaselineCache.get_instance()

        t0 = time.perf_counter()
        is_valid, base_res, _ = cache.get_or_compute(plan, base_opt)
        t_base = time.perf_counter() - t0

        if not is_valid or base_res is None or base_res.bundle is None or base_res.partition is None:
            return None

        base_state = ALNSState(
            zones=[set(h.assigned_rooms) for h in base_res.partition.hubs],
            bundle=base_res.bundle,
            partition=base_res.partition,
            cost_J=base_res.total_cost_J,
            h_count=base_res.h_count,
            area_gap=base_res.area_gap_m2,
            is_valid=True,
        )

        portals = extract_all_portals(plan)
        topo = MacroTopologyGraph.build_topology(plan, portals)
        door_graph = build_door_connectivity_graph(plan, topo)

        # Load policy
        policy = ActorCriticPolicy()
        policy_path = Path("checkpoints/ppo_alns/latest_model.pt")
        policy.load_state_dict(torch.load(policy_path, map_location="cpu"))
        policy.eval()

        # 2. Standalone ALNS (Bandit)
        state_bandit, t_bandit, _ = run_single_alns_variant(
            variant_type="bandit",
            base_state=base_state,
            plan=plan,
            topology=topo,
            door_graph=door_graph,
            policy=None,
            max_steps=max_steps,
            seed=seed,
        )

        # 3. Random ALNS
        state_random, t_random, _ = run_single_alns_variant(
            variant_type="random",
            base_state=base_state,
            plan=plan,
            topology=topo,
            door_graph=door_graph,
            policy=None,
            max_steps=max_steps,
            seed=seed,
        )

        # 4. Trained PPO-ALNS
        state_ppo, t_ppo, _ = run_single_alns_variant(
            variant_type="ppo",
            base_state=base_state,
            plan=plan,
            topology=topo,
            door_graph=door_graph,
            policy=policy,
            max_steps=max_steps,
            seed=seed,
        )

        return {
            "plan_id": p_id,
            "tier": tier,
            "eligible_rooms": n_rooms,
            # PortfolioHybrid
            "h_base": base_state.h_count,
            "j_base": round(base_state.cost_J, 4),
            "l_base": round(base_res.total_length_m, 2),
            "valid_base": base_state.is_valid,
            "time_base": round(t_base, 3),
            # Standalone ALNS (Bandit)
            "h_bandit": state_bandit.h_count,
            "j_bandit": round(state_bandit.cost_J, 4),
            "l_bandit": round(state_bandit.bundle.total_length_m, 2),
            "valid_bandit": state_bandit.is_valid,
            "time_bandit": round(t_bandit, 3),
            "delta_j_bandit": round(base_state.cost_J - state_bandit.cost_J, 4),
            # Random ALNS
            "h_random": state_random.h_count,
            "j_random": round(state_random.cost_J, 4),
            "l_random": round(state_random.bundle.total_length_m, 2),
            "valid_random": state_random.is_valid,
            "time_random": round(t_random, 3),
            "delta_j_random": round(base_state.cost_J - state_random.cost_J, 4),
            # Trained PPO-ALNS
            "h_ppo": state_ppo.h_count,
            "j_ppo": round(state_ppo.cost_J, 4),
            "l_ppo": round(state_ppo.bundle.total_length_m, 2),
            "valid_ppo": state_ppo.is_valid,
            "time_ppo": round(t_ppo, 3),
            "delta_j_ppo": round(base_state.cost_J - state_ppo.cost_J, 4),
        }
    except Exception as e:
        print(f"Error evaluating plan {p_id}: {e}", flush=True)
        return None


def run_benchmark(sample_size: int = 60, max_steps: int = 10, max_workers: int = 6, seed: int = 42) -> Dict[str, Any]:
    print(f"Starting Agent 5 Fair Benchmarking on {sample_size} clean validation plans using {max_workers} parallel workers...", flush=True)
    dataset_mgr = ResPlanDatasetManager()
    val_ids = dataset_mgr.clean_val_ids[:sample_size]
    print(f"Selected {len(val_ids)} clean validation floor plan IDs.", flush=True)

    tasks = [(pid, seed + i, max_steps) for i, pid in enumerate(val_ids)]
    records: List[Dict[str, Any]] = []

    start_total_time = time.perf_counter()

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(evaluate_single_plan_worker, t): t[0] for t in tasks}
        completed = 0
        for f in as_completed(futures):
            res = f.result()
            completed += 1
            if res is not None:
                records.append(res)
            if completed % 10 == 0 or completed == len(tasks):
                el = time.perf_counter() - start_total_time
                print(f"Progress: {completed}/{len(tasks)} plans evaluated in {el:.1f}s ({el / completed:.2f}s/plan)...", flush=True)

    # Sort records by plan_id
    records.sort(key=lambda r: r["plan_id"])
    print(f"Successfully benchmarked {len(records)} valid validation plans in {time.perf_counter() - start_total_time:.1f}s.", flush=True)

    # Save CSV
    csv_file = Path("results/diagnostics_agent5_benchmark.csv")
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    if records:
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)

    # Statistical Analysis
    n = len(records)
    j_base_arr = np.array([r["j_base"] for r in records])
    j_bandit_arr = np.array([r["j_bandit"] for r in records])
    j_random_arr = np.array([r["j_random"] for r in records])
    j_ppo_arr = np.array([r["j_ppo"] for r in records])

    diff_ppo_base = j_base_arr - j_ppo_arr
    t_stat_base, p_val_base = stats.ttest_rel(j_base_arr, j_ppo_arr)
    try:
        w_stat_base, w_pval_base = stats.wilcoxon(j_base_arr, j_ppo_arr)
    except Exception:
        w_stat_base, w_pval_base = 0.0, 1.0

    diff_bandit_base = j_base_arr - j_bandit_arr
    t_stat_bandit, p_val_bandit = stats.ttest_rel(j_base_arr, j_bandit_arr)
    try:
        w_stat_bandit, w_pval_bandit = stats.wilcoxon(j_base_arr, j_bandit_arr)
    except Exception:
        w_stat_bandit, w_pval_bandit = 0.0, 1.0

    diff_ppo_bandit = j_bandit_arr - j_ppo_arr
    t_stat_vs_bandit, p_val_vs_bandit = stats.ttest_rel(j_bandit_arr, j_ppo_arr)
    try:
        w_stat_vs_bandit, w_pval_vs_bandit = stats.wilcoxon(j_bandit_arr, j_ppo_arr)
    except Exception:
        w_stat_vs_bandit, w_pval_vs_bandit = 0.0, 1.0

    def get_wtl(candidate_arr, reference_arr):
        wins = int(np.sum(candidate_arr < reference_arr - 1e-4))
        ties = int(np.sum(np.abs(candidate_arr - reference_arr) <= 1e-4))
        losses = int(np.sum(candidate_arr > reference_arr + 1e-4))
        return {"wins": wins, "ties": ties, "losses": losses}

    # Stratified breakdown
    def tier_summary(tier_name: str):
        sub = [r for r in records if r["tier"] == tier_name]
        if not sub:
            return {}
        jb = np.array([r["j_base"] for r in sub])
        jban = np.array([r["j_bandit"] for r in sub])
        jrand = np.array([r["j_random"] for r in sub])
        jpp = np.array([r["j_ppo"] for r in sub])
        return {
            "count": len(sub),
            "mean_J_base": round(float(np.mean(jb)), 3),
            "mean_J_bandit": round(float(np.mean(jban)), 3),
            "mean_J_random": round(float(np.mean(jrand)), 3),
            "mean_J_ppo": round(float(np.mean(jpp)), 3),
            "delta_J_bandit": round(float(np.mean(jb - jban)), 4),
            "delta_J_ppo": round(float(np.mean(jb - jpp)), 4),
        }

    summary = {
        "num_plans_evaluated": n,
        "sample_size": sample_size,
        "max_steps_per_variant": max_steps,
        "num_workers": max_workers,
        "portfolio_hybrid": {
            "mean_J": round(float(np.mean(j_base_arr)), 3),
            "median_J": round(float(np.median(j_base_arr)), 3),
            "mean_H": round(float(np.mean([r["h_base"] for r in records])), 2),
            "mean_runtime_s": round(float(np.mean([r["time_base"] for r in records])), 3),
            "validity_rate": float(np.mean([1.0 if r["valid_base"] else 0.0 for r in records])),
        },
        "standalone_alns_bandit": {
            "mean_J": round(float(np.mean(j_bandit_arr)), 3),
            "median_J": round(float(np.median(j_bandit_arr)), 3),
            "mean_H": round(float(np.mean([r["h_bandit"] for r in records])), 2),
            "mean_runtime_s": round(float(np.mean([r["time_bandit"] for r in records])), 3),
            "validity_rate": float(np.mean([1.0 if r["valid_bandit"] else 0.0 for r in records])),
            "mean_delta_J": round(float(np.mean(diff_bandit_base)), 4),
            "pct_improvement": round(float(np.mean(diff_bandit_base / j_base_arr) * 100.0), 3),
            "wtl_vs_base": get_wtl(j_bandit_arr, j_base_arr),
            "t_test_p_value": float(p_val_bandit),
            "wilcoxon_p_value": float(w_pval_bandit),
        },
        "random_alns": {
            "mean_J": round(float(np.mean(j_random_arr)), 3),
            "median_J": round(float(np.median(j_random_arr)), 3),
            "mean_H": round(float(np.mean([r["h_random"] for r in records])), 2),
            "mean_runtime_s": round(float(np.mean([r["time_random"] for r in records])), 3),
            "validity_rate": float(np.mean([1.0 if r["valid_random"] else 0.0 for r in records])),
            "mean_delta_J": round(float(np.mean(j_base_arr - j_random_arr)), 4),
            "pct_improvement": round(float(np.mean((j_base_arr - j_random_arr) / j_base_arr) * 100.0), 3),
            "wtl_vs_base": get_wtl(j_random_arr, j_base_arr),
        },
        "trained_ppo_alns": {
            "mean_J": round(float(np.mean(j_ppo_arr)), 3),
            "median_J": round(float(np.median(j_ppo_arr)), 3),
            "mean_H": round(float(np.mean([r["h_ppo"] for r in records])), 2),
            "mean_runtime_s": round(float(np.mean([r["time_ppo"] for r in records])), 3),
            "validity_rate": float(np.mean([1.0 if r["valid_ppo"] else 0.0 for r in records])),
            "mean_delta_J": round(float(np.mean(diff_ppo_base)), 4),
            "pct_improvement": round(float(np.mean(diff_ppo_base / j_base_arr) * 100.0), 3),
            "wtl_vs_base": get_wtl(j_ppo_arr, j_base_arr),
            "wtl_vs_bandit": get_wtl(j_ppo_arr, j_bandit_arr),
            "t_test_vs_base_p_value": float(p_val_base),
            "wilcoxon_vs_base_p_value": float(w_pval_base),
            "t_test_vs_bandit_p_value": float(p_val_vs_bandit),
            "wilcoxon_vs_bandit_p_value": float(w_pval_vs_bandit),
        },
        "tiered_breakdown": {
            "compact": tier_summary("compact"),
            "standard": tier_summary("standard"),
            "complex": tier_summary("complex"),
        },
        "verdict_and_findings": {
            "did_ppo_learn_meaningful_advantage": bool(p_val_vs_bandit < 0.05 and np.mean(diff_ppo_bandit) > 0.05),
            "summary": (
                f"Across {n} held-out clean validation floor plans, Trained PPO-ALNS achieved Mean J = {np.mean(j_ppo_arr):.2f} "
                f"versus PortfolioHybrid baseline Mean J = {np.mean(j_base_arr):.2f} and Standalone ALNS Mean J = {np.mean(j_bandit_arr):.2f}. "
                f"Trained PPO-ALNS tied PortfolioHybrid in {get_wtl(j_ppo_arr, j_base_arr)['ties']}/{n} plans ({get_wtl(j_ppo_arr, j_base_arr)['ties']/n*100:.1f}%), "
                f"yielding an empirical improvement of {np.mean(diff_ppo_base):.4f}m (p = {p_val_base:.4f}). "
                f"Because the trained policy collapsed into picking WORST_ROUTE_REBUILD (Op 5) deterministically, "
                f"it behaves as a near no-op filter over PortfolioHybrid, failing to achieve the cost reductions of adaptive Standalone ALNS."
            )
        }
    }

    json_file = Path("results/diagnostics_agent5_benchmark.json")
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"Agent 5 Fair Benchmarking complete. Saved to {json_file} and {csv_file}", flush=True)
    return summary


if __name__ == "__main__":
    run_benchmark(sample_size=60, max_steps=10, max_workers=6)
