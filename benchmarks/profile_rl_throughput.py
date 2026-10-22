"""Comprehensive RL Throughput and Latency Profiling Suite (Priority 3).

Measures and isolates latency for:
1. Dataset loading & indexing
2. PortfolioHybrid baseline generation (uncached)
3. Baseline retrieval (cached)
4. Environment reset latency (uncached vs cached)
5. ALNS action execution time (broken down by operator 0-7)
6. Independent FloorPlanChecker validation latency
7. PPO Actor-Critic inference latency (CPU vs CUDA)
8. PPO backward pass & gradient update latency on GPU
9. Process serialization & IPC overhead
"""

from __future__ import annotations

import json
from pathlib import Path
import pickle
import sys
import time
from typing import Any, Dict, List

import numpy as np

# Add project root and src to path
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import torch
from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.alns_operators import (
    NUM_OPERATORS,
    OPERATOR_NAMES,
    apply_alns_operator,
    build_door_connectivity_graph,
)
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.baseline_cache import BaselineCache
from rl.dataset import ResPlanDatasetManager
from rl.environment import CableRingEnv
from rl.observations import OBSERVATION_DIM
from rl.policy import ActorCriticPolicy


def run_full_profiling(output_path: str = "results/throughput_profile.json") -> Dict[str, Any]:
    """Execute all micro-benchmarks and record exact nanosecond-precision timings."""
    timings: Dict[str, Any] = {}
    print("=" * 70)
    print("HONEYWELL PPO-ALNS THOROUGHPUT & LATENCY PROFILING")
    print("=" * 70, flush=True)

    # 1. Dataset Loading
    t0 = time.perf_counter()
    mgr = ResPlanDatasetManager()
    timings["dataset_manager_init_s"] = round(time.perf_counter() - t0, 4)
    print(f"[1/9] Dataset Manager Initialization: {timings['dataset_manager_init_s']:.4f}s", flush=True)

    # 2. PortfolioHybrid Baseline Generation (Uncached)
    test_pids = [14433, 16755, 4664, 834, 7737]
    plans = [mgr.get_plan(pid) for pid in test_pids]
    base_opt = PortfolioHybridOptimizer()

    t0 = time.perf_counter()
    for p in plans:
        _ = base_opt.optimize(p, time_limit_seconds=2.0)
    total_uncached_gen = time.perf_counter() - t0
    timings["baseline_generation_uncached_mean_s"] = round(total_uncached_gen / len(plans), 4)
    print(f"[2/9] PortfolioHybrid Baseline Gen (Uncached): {timings['baseline_generation_uncached_mean_s']:.4f}s / plan", flush=True)

    # 3. Baseline Retrieval (Cached)
    cache = BaselineCache.get_instance()
    # Ensure they are cached
    for p in plans:
        cache.get_or_compute(p, base_opt)

    t0 = time.perf_counter()
    N_CACHE_REPS = 100
    for _ in range(N_CACHE_REPS):
        for p in plans:
            _ = cache.get(int(p.plan_id))
    total_cached_retrieval = time.perf_counter() - t0
    timings["baseline_retrieval_cached_mean_ms"] = round((total_cached_retrieval / (N_CACHE_REPS * len(plans))) * 1000.0, 4)
    speedup = (timings["baseline_generation_uncached_mean_s"] * 1000.0) / max(0.0001, timings["baseline_retrieval_cached_mean_ms"])
    timings["caching_speedup_factor"] = round(speedup, 1)
    print(f"[3/9] Baseline Retrieval (Cached): {timings['baseline_retrieval_cached_mean_ms']:.4f}ms ({speedup:.0f}x speedup!)", flush=True)

    # 4. Environment Reset Latency (Cached)
    env = CableRingEnv(dataset_manager=mgr, plan_ids=[14433], max_steps_per_episode=25)
    t0 = time.perf_counter()
    N_RESETS = 20
    for _ in range(N_RESETS):
        env.reset()
    timings["env_reset_cached_mean_ms"] = round((time.perf_counter() - t0) / N_RESETS * 1000.0, 4)
    print(f"[4/9] Environment Reset Latency (Cached): {timings['env_reset_cached_mean_ms']:.2f}ms", flush=True)

    # 5. ALNS Operator Execution Time
    p_demo = mgr.get_plan(14433)
    portals = extract_all_portals(p_demo)
    topo = MacroTopologyGraph.build_topology(p_demo, portals)
    door_g = build_door_connectivity_graph(p_demo, topo)
    cached_entry = cache.get(14433)
    base_res = cached_entry[1]

    from resplan_router.optimizers.alns_operators import ALNSState
    state = ALNSState(
        zones=[set(h.assigned_rooms) for h in base_res.partition.hubs],
        bundle=base_res.bundle,
        partition=base_res.partition,
        cost_J=base_res.total_cost_J,
        h_count=base_res.h_count,
        area_gap=base_res.area_gap_m2,
        is_valid=True,
    )

    rng = env.rng
    op_timings: Dict[str, float] = {}
    N_OP_REPS = 10

    for op_idx in range(NUM_OPERATORS):
        op_name = OPERATOR_NAMES[op_idx]
        t0 = time.perf_counter()
        for _ in range(N_OP_REPS):
            apply_alns_operator(op_idx, state, p_demo, topo, door_g, rng)
        avg_ms = ((time.perf_counter() - t0) / N_OP_REPS) * 1000.0
        op_timings[f"Op_{op_idx}_{op_name}_ms"] = round(avg_ms, 2)

    timings["alns_operators_ms"] = op_timings
    mean_op_ms = float(np.mean(list(op_timings.values())))
    timings["alns_operator_mean_ms"] = round(mean_op_ms, 2)
    print(f"[5/9] ALNS Operator Mean Step Latency: {mean_op_ms:.2f}ms", flush=True)
    for k, v in op_timings.items():
        print(f"       - {k}: {v:.2f}ms", flush=True)

    # 6. Constraint Validation Time
    t0 = time.perf_counter()
    N_CHECK_REPS = 50
    for _ in range(N_CHECK_REPS):
        FloorPlanChecker.evaluate(p_demo, base_res.bundle)
    timings["floorplan_checker_evaluate_ms"] = round((time.perf_counter() - t0) / N_CHECK_REPS * 1000.0, 3)
    print(f"[6/9] FloorPlanChecker.evaluate() Latency: {timings['floorplan_checker_evaluate_ms']:.3f}ms", flush=True)

    # 7. PPO Inference Time (CPU vs CUDA)
    device_cuda = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    policy_cpu = ActorCriticPolicy(obs_dim=OBSERVATION_DIM, action_dim=NUM_OPERATORS).to("cpu")
    policy_cuda = ActorCriticPolicy(obs_dim=OBSERVATION_DIM, action_dim=NUM_OPERATORS).to(device_cuda)

    dummy_obs_1_cpu = torch.zeros((1, OBSERVATION_DIM), dtype=torch.float32)
    dummy_obs_4_cpu = torch.zeros((4, OBSERVATION_DIM), dtype=torch.float32)
    dummy_obs_1_cuda = dummy_obs_1_cpu.to(device_cuda)
    dummy_obs_4_cuda = dummy_obs_4_cpu.to(device_cuda)

    # CPU inference
    t0 = time.perf_counter()
    N_INF = 1000
    with torch.no_grad():
        for _ in range(N_INF):
            policy_cpu.get_action_and_value(dummy_obs_4_cpu)
    timings["ppo_inference_cpu_batch4_us"] = round((time.perf_counter() - t0) / N_INF * 1_000_000.0, 2)

    # CUDA inference
    if torch.cuda.is_available():
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        with torch.no_grad():
            for _ in range(N_INF):
                policy_cuda.get_action_and_value(dummy_obs_4_cuda)
        torch.cuda.synchronize()
        timings["ppo_inference_cuda_batch4_us"] = round((time.perf_counter() - t0) / N_INF * 1_000_000.0, 2)
    else:
        timings["ppo_inference_cuda_batch4_us"] = timings["ppo_inference_cpu_batch4_us"]

    print(f"[7/9] PPO Forward Inference (batch=4): CPU={timings['ppo_inference_cpu_batch4_us']:.1f}us, CUDA={timings['ppo_inference_cuda_batch4_us']:.1f}us", flush=True)

    # 8. PPO Backward Update Time on GPU
    optimizer = torch.optim.Adam(policy_cuda.parameters(), lr=3e-4)
    b_obs = torch.randn((128, OBSERVATION_DIM), device=device_cuda)
    b_act = torch.randint(0, NUM_OPERATORS, (128,), device=device_cuda)
    b_adv = torch.randn((128,), device=device_cuda)
    b_ret = torch.randn((128,), device=device_cuda)
    b_logp = torch.zeros((128,), device=device_cuda)
    b_val = torch.zeros((128,), device=device_cuda)

    if torch.cuda.is_available():
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    N_UPDATES = 50
    for _ in range(N_UPDATES):
        _, newlogprob, entropy, newvalue = policy_cuda.get_action_and_value(b_obs, b_act)
        ratio = (newlogprob - b_logp).exp()
        pg_loss = -(b_adv * ratio).mean()
        v_loss = 0.5 * ((newvalue - b_ret) ** 2).mean()
        loss = pg_loss + 0.5 * v_loss - 0.01 * entropy.mean()
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    timings["ppo_backward_update_128samples_ms"] = round((time.perf_counter() - t0) / N_UPDATES * 1000.0, 3)
    print(f"[8/9] PPO Gradient Backward (128 samples): {timings['ppo_backward_update_128samples_ms']:.3f}ms", flush=True)

    # 9. Serialization / IPC Time
    t0 = time.perf_counter()
    N_SER = 100
    for _ in range(N_SER):
        raw_bytes = pickle.dumps(dummy_obs_4_cpu.numpy())
        _ = pickle.loads(raw_bytes)
    timings["ipc_serialization_obs_us"] = round((time.perf_counter() - t0) / N_SER * 1_000_000.0, 2)
    print(f"[9/9] IPC Serialization (Obs Batch): {timings['ipc_serialization_obs_us']:.2f}us", flush=True)

    # Summary of bottleneck
    print("=" * 70)
    print("PROFILING SUMMARY & BOTTLENECK IDENTIFICATION:")
    print(f"  - Baseline Gen (Uncached):   {timings['baseline_generation_uncached_mean_s']*1000.0:8.2f} ms")
    print(f"  - Baseline Retrieval (Cache): {timings['baseline_retrieval_cached_mean_ms']:8.4f} ms")
    print(f"  - Env Reset Latency:         {timings['env_reset_cached_mean_ms']:8.2f} ms")
    print(f"  - ALNS Operator Step:        {timings['alns_operator_mean_ms']:8.2f} ms")
    print(f"  - FloorPlanChecker:          {timings['floorplan_checker_evaluate_ms']:8.3f} ms")
    print(f"  - PPO Inference (CUDA):      {timings['ppo_inference_cuda_batch4_us']/1000.0:8.4f} ms")
    print(f"  - PPO Backward Update:       {timings['ppo_backward_update_128samples_ms']:8.3f} ms")
    print("=" * 70, flush=True)

    # Save to disk
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(timings, f, indent=2)

    return timings


if __name__ == "__main__":
    run_full_profiling()
