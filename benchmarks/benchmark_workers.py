"""Worker Concurrency and SPS Throughput Benchmarking (Requirement 4).

Rebenchmarks complete environment-step throughput with 1, 2, and 4 workers
after ring-reuse acceleration, measuring:
- Actual Steps Per Second (SPS)
- CPU utilization percentage (via psutil)
- GPU utilization percentage and VRAM usage (via nvidia-smi)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np
import psutil

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import torch
from resplan_router.optimizers.alns_operators import NUM_OPERATORS
from rl.baseline_cache import BaselineCache
from rl.curriculum import CurriculumScheduler
from rl.dataset import ResPlanDatasetManager
from rl.environment import CableRingEnv
from rl.observations import OBSERVATION_DIM
from rl.policy import ActorCriticPolicy


def query_gpu_metrics() -> Tuple[float, float, float]:
    """Query current GPU utilization (%), memory used (MB), and memory total (MB)."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
        parts = [float(x.strip()) for x in res.stdout.strip().split(",")]
        return parts[0], parts[1], parts[2]
    except Exception:
        return 0.0, 0.0, 0.0


def benchmark_worker_configurations(steps_per_env: int = 16) -> Dict[str, Any]:
    """Benchmark 1, 2, and 4 workers across sequential and parallel stepping."""
    print("=" * 75)
    print("HONEYWELL PPO-ALNS MULTI-WORKER THROUGHPUT BENCHMARK (RING-REUSE ACCELERATED)")
    print("=" * 75, flush=True)

    dataset_mgr = ResPlanDatasetManager()
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    policy = ActorCriticPolicy(OBSERVATION_DIM, NUM_OPERATORS).to(device)
    cache = BaselineCache.get_instance()

    # Pre-cache test floor plans
    p_demo = dataset_mgr.get_plan(14433)
    from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
    cache.get_or_compute(p_demo, PortfolioHybridOptimizer())

    results: Dict[str, Any] = {}

    configs = [
        ("1_worker_sequential", 1, "sequential"),
        ("2_workers_sequential", 2, "sequential"),
        ("2_workers_threadpool", 2, "threadpool"),
        ("4_workers_sequential", 4, "sequential"),
        ("4_workers_threadpool", 4, "threadpool"),
    ]

    for name, num_envs, mode in configs:
        curriculum = CurriculumScheduler(dataset_manager=dataset_mgr, total_steps=1000)
        envs = [
            CableRingEnv(
                dataset_manager=dataset_mgr,
                plan_ids=[14433],
                max_steps_per_episode=25,
                random_seed=42 + i * 100,
            )
            for i in range(num_envs)
        ]

        # Reset all envs
        obs_list = [env.reset()[0] for env in envs]
        total_steps = num_envs * steps_per_env

        # Prime psutil measurement
        psutil.cpu_percent(interval=None)
        gpu_util_start, gpu_mem_used, gpu_mem_tot = query_gpu_metrics()

        t_start = time.perf_counter()

        if mode == "sequential":
            for step_i in range(steps_per_env):
                obs_t = torch.tensor(np.array(obs_list), dtype=torch.float32, device=device)
                with torch.no_grad():
                    actions = policy.get_action_and_value(obs_t)[0].cpu().numpy()

                for env_idx, env in enumerate(envs):
                    o, r, term, trunc, _ = env.step(int(actions[env_idx]))
                    if term or trunc:
                        o, _ = env.reset()
                    obs_list[env_idx] = o

        elif mode == "threadpool":
            with ThreadPoolExecutor(max_workers=num_envs) as pool:
                for step_i in range(steps_per_env):
                    obs_t = torch.tensor(np.array(obs_list), dtype=torch.float32, device=device)
                    with torch.no_grad():
                        actions = policy.get_action_and_value(obs_t)[0].cpu().numpy()

                    def step_worker(idx_and_action):
                        idx, a = idx_and_action
                        e = envs[idx]
                        o, r, term, trunc, _ = e.step(int(a))
                        if term or trunc:
                            o, _ = e.reset()
                        return idx, o

                    tasks = [(i, actions[i]) for i in range(num_envs)]
                    step_results = list(pool.map(step_worker, tasks))
                    for idx, o in step_results:
                        obs_list[idx] = o

        elapsed = time.perf_counter() - t_start
        cpu_pct = psutil.cpu_percent(interval=None)
        gpu_util_end, gpu_mem_used, _ = query_gpu_metrics()
        gpu_util = max(gpu_util_start, gpu_util_end)

        sps = round(total_steps / max(0.001, elapsed), 2)
        results[name] = {
            "num_envs": num_envs,
            "mode": mode,
            "total_steps": total_steps,
            "elapsed_s": round(elapsed, 4),
            "sps": sps,
            "cpu_utilization_pct": round(cpu_pct, 1),
            "gpu_utilization_pct": round(gpu_util, 1),
            "gpu_memory_used_mb": round(gpu_mem_used, 1),
        }
        print(
            f"[{name:22s}] {total_steps:2d} steps in {elapsed:5.2f}s | "
            f"SPS: {sps:5.2f} | CPU: {cpu_pct:4.1f}% | GPU: {gpu_util:4.1f}% | VRAM: {gpu_mem_used:.0f} MB",
            flush=True,
        )

    print("=" * 75)
    best_config = max(results.keys(), key=lambda k: results[k]["sps"])
    print(f"Optimal Worker Configuration: {best_config} ({results[best_config]['sps']} SPS)")
    print("=" * 75, flush=True)

    with open("results/worker_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    benchmark_worker_configurations()
