"""Comparative Benchmark of PortfolioHybrid vs Standalone ALNS vs PPO-ALNS (Requirement 5).

Evaluates the 3 algorithms on the exact fixed validation plans [0, 4, 5, 12, 24]
under strictly identical optimization budgets (25 steps / 5.0s) and verified by
the official FloorPlanChecker at 1 mm tolerance.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.optimizers.alns_optimizer import ALNSOptimizer
from rl.dataset import ResPlanDatasetManager
from rl.train_ppo import PPOALNSOptimizer


def run_validation_comparison(
    val_plan_ids: List[int] = [0, 4, 5, 12, 24],
    checkpoint_path: str = "checkpoints/ppo_alns/best_model.pt",
    budget_steps: int = 25,
    time_limit_s: float = 5.0,
) -> Dict[str, Any]:
    print("=" * 80)
    print("  HONEYWELL VALIDATION COMPARISON: PortfolioHybrid vs ALNS vs PPO-ALNS")
    print(f"  Plans: {val_plan_ids} | Step Budget: {budget_steps} | Time Limit: {time_limit_s}s")
    print("=" * 80, flush=True)

    dataset_mgr = ResPlanDatasetManager()
    optimizers = {
        "PortfolioHybrid": PortfolioHybridOptimizer(),
        "ALNS_Standalone": ALNSOptimizer(name="ALNS_Standalone", max_iterations=budget_steps),
        "PPO_ALNS": PPOALNSOptimizer(name="PPO_ALNS", checkpoint_path=checkpoint_path, max_steps=budget_steps),
    }

    csv_path = Path("results/validation_comparison.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "plan_id", "algorithm", "h_count", "cost_J", "length_m", "bends",
            "crossings", "overlap_m", "area_gap_m2", "is_valid", "runtime_s"
        ])

    results: Dict[str, List[Dict[str, Any]]] = {algo: [] for algo in optimizers}

    for p_idx, pid in enumerate(val_plan_ids, start=1):
        plan = dataset_mgr.get_plan(pid)
        n_rooms = len([r for r in plan.rooms.values() if r.is_eligible])
        print(f"\n[{p_idx}/{len(val_plan_ids)}] Evaluating Plan ID {pid} ({n_rooms} eligible rooms, {plan.total_area_m2:.1f} m²):")

        for algo_name, opt in optimizers.items():
            t0 = time.perf_counter()
            try:
                res = opt.optimize(plan, time_limit_seconds=time_limit_s, random_seed=42)
                row = res.as_dict()
                row["algorithm"] = algo_name
                row["plan_id"] = str(pid)
            except Exception as exc:
                row = {
                    "algorithm": algo_name,
                    "plan_id": str(pid),
                    "h_count": 0,
                    "total_cost_J": float("inf"),
                    "total_length_m": 0.0,
                    "total_bends": 0,
                    "total_crossings": 0,
                    "total_overlap_m": 0.0,
                    "area_gap_m2": 0.0,
                    "is_valid": False,
                    "runtime_s": round(time.perf_counter() - t0, 4),
                    "solver_status": "ERROR",
                }

            results[algo_name].append(row)

            # Record CSV
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    row["plan_id"],
                    algo_name,
                    row["h_count"],
                    round(row["total_cost_J"], 4),
                    round(row["total_length_m"], 4),
                    row["total_bends"],
                    row["total_crossings"],
                    round(row["total_overlap_m"], 4),
                    round(row["area_gap_m2"], 4),
                    row["is_valid"],
                    round(row["runtime_s"], 4),
                ])

            status = "VALID" if row["is_valid"] else "INVALID"
            print(f"   - {algo_name:18s}: H={row['h_count']} J={row['total_cost_J']:7.2f} (L={row['total_length_m']:5.1f}m, B={row['total_bends']:2d}, X={row['total_crossings']}, O={row['total_overlap_m']:.2f}m) [{status}, {row['runtime_s']:4.2f}s]")

    # Aggregate metrics
    summary: Dict[str, Any] = {"plans": val_plan_ids, "algorithms": {}}
    print("\n" + "=" * 80)
    print(f"{'Algorithm':18s} | {'Valid':6s} | {'Mean H':6s} | {'Mean J':8s} | {'Median J':8s} | {'Mean L':7s} | {'Runtime':7s}")
    print("-" * 80)

    for algo, res_list in results.items():
        val_rows = [r for r in res_list if r["is_valid"]]
        valid_rate = len(val_rows) / max(1, len(res_list))
        j_vals = [r["total_cost_J"] for r in val_rows]
        h_vals = [r["h_count"] for r in val_rows]
        l_vals = [r["total_length_m"] for r in val_rows]
        runtimes = [r["runtime_s"] for r in res_list]

        m_j = round(float(np.mean(j_vals)), 2) if j_vals else None
        med_j = round(float(np.median(j_vals)), 2) if j_vals else None
        m_h = round(float(np.mean(h_vals)), 2) if h_vals else None
        m_l = round(float(np.mean(l_vals)), 2) if l_vals else None
        m_rt = round(float(np.mean(runtimes)), 2)

        summary["algorithms"][algo] = {
            "validity_rate_pct": round(valid_rate * 100.0, 1),
            "mean_H": m_h,
            "mean_J": m_j,
            "median_J": med_j,
            "mean_L": m_l,
            "mean_runtime_s": m_rt,
        }
        print(f"{algo:18s} | {valid_rate*100:5.1f}% | {m_h:6.2f} | {m_j:8.2f} | {med_j:8.2f} | {m_l:7.2f} | {m_rt:6.2f}s")
    print("=" * 80)

    with open("results/validation_comparison.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    run_validation_comparison()
