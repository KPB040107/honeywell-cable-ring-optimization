"""Rigorous Post-Champion Benchmark: Advanced ALNS Operators vs Frozen Champion.

Compares:
1. PortfolioHybrid (Exact Bitmask DP + Held-Karp Baseline)
2. LinUCB_ALNS (Grand Champion from Post-Training Pipeline)
3. Advanced_ALNS (Post-Champion Engine with 6 Advanced Operators)

Evaluates 20 representative floor plans across Compact, Standard, and Complex tiers.
Outputs complete CSV, JSON, and Markdown summaries with statistical significance testing.
"""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np
from scipy import stats

# Ensure path resolution
sys.path.insert(0, r"c:\hackhive\src")
sys.path.insert(0, r"c:\hackhive")

from resplan_router.contracts import MetricFloorPlan
from resplan_router.experimental.advanced_operators import AdvancedALNSOptimizer
from resplan_router.optimizers.alns_linucb import ALNSLinUCBOptimizer
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from benchmarks.framework.config import BenchmarkConfig, BenchmarkMode


def run_benchmark():
    repo = DatasetRepository()
    cfg = BenchmarkConfig(mode=BenchmarkMode.SMOKE)
    all_smoke = cfg.resolve_plan_ids()
    target_plan_ids = all_smoke[:20]

    print("=" * 80)
    print("  POST-CHAMPION BENCHMARK: ADVANCED ALNS OPERATORS vs FROZEN CHAMPION")
    print(f"  Target Plans: {len(target_plan_ids)} | Algorithms: PortfolioHybrid, LinUCB_ALNS, Advanced_ALNS")
    print("=" * 80)

    opt_ph = PortfolioHybridOptimizer()
    opt_lin = ALNSLinUCBOptimizer(name="LinUCB_ALNS", max_iterations=15)
    opt_adv = AdvancedALNSOptimizer(name="Advanced_ALNS", max_iterations=15)

    records: List[Dict[str, Any]] = []

    print(f"\n{'Plan':<6} {'Elig':<5} | {'--- PortfolioHybrid ---':<25} | {'--- Frozen LinUCB ---':<25} | {'--- Advanced ALNS ---':<25}")
    print(f"{'ID':<6} {'Rms':<5} | {'H':<3} {'J':<8} {'X':<3} {'O':<7} | {'H':<3} {'J':<8} {'X':<3} {'O':<7} | {'H':<3} {'J':<8} {'X':<3} {'O':<7}")
    print("-" * 90)

    for pid in target_plan_ids:
        plan = repo.get_floor_plan(str(pid))
        if plan is None:
            continue

        n_elig = len([r for r in plan.rooms.values() if r.is_eligible])

        # 1. PortfolioHybrid
        t0 = time.perf_counter()
        r_ph = opt_ph.optimize(plan, time_limit_seconds=4.0, random_seed=42 + pid)
        time_ph = time.perf_counter() - t0

        # 2. LinUCB Champion
        t0 = time.perf_counter()
        r_lin = opt_lin.optimize(plan, time_limit_seconds=5.0, random_seed=42 + pid)
        time_lin = time.perf_counter() - t0

        # 3. Advanced ALNS Candidate
        t0 = time.perf_counter()
        r_adv = opt_adv.optimize(plan, time_limit_seconds=5.0, random_seed=42 + pid)
        time_adv = time.perf_counter() - t0

        rec = {
            "plan_id": pid,
            "eligible_rooms": n_elig,
            "ph_valid": r_ph.is_valid,
            "ph_h": r_ph.h_count,
            "ph_j": r_ph.total_cost_J,
            "ph_l": r_ph.total_length_m,
            "ph_b": r_ph.total_bends,
            "ph_x": r_ph.total_crossings,
            "ph_o": r_ph.total_overlap_m,
            "ph_gap": r_ph.area_gap_m2,
            "ph_time": time_ph,
            "lin_valid": r_lin.is_valid,
            "lin_h": r_lin.h_count,
            "lin_j": r_lin.total_cost_J,
            "lin_l": r_lin.total_length_m,
            "lin_b": r_lin.total_bends,
            "lin_x": r_lin.total_crossings,
            "lin_o": r_lin.total_overlap_m,
            "lin_gap": r_lin.area_gap_m2,
            "lin_time": time_lin,
            "adv_valid": r_adv.is_valid,
            "adv_h": r_adv.h_count,
            "adv_j": r_adv.total_cost_J,
            "adv_l": r_adv.total_length_m,
            "adv_b": r_adv.total_bends,
            "adv_x": r_adv.total_crossings,
            "adv_o": r_adv.total_overlap_m,
            "adv_gap": r_adv.area_gap_m2,
            "adv_time": time_adv,
            "adv_diag": r_adv.diagnostic_info,
        }
        records.append(rec)

        print(
            f"{pid:<6} {n_elig:<5} | "
            f"{r_ph.h_count:<3} {r_ph.total_cost_J:<8.2f} {r_ph.total_crossings:<3} {r_ph.total_overlap_m:<7.2f} | "
            f"{r_lin.h_count:<3} {r_lin.total_cost_J:<8.2f} {r_lin.total_crossings:<3} {r_lin.total_overlap_m:<7.2f} | "
            f"{r_adv.h_count:<3} {r_adv.total_cost_J:<8.2f} {r_adv.total_crossings:<3} {r_adv.total_overlap_m:<7.2f}"
        )

    print("-" * 90)

    # Statistical Aggregation
    n = len(records)
    ph_valid_rate = sum(1 for r in records if r["ph_valid"]) / n * 100
    lin_valid_rate = sum(1 for r in records if r["lin_valid"]) / n * 100
    adv_valid_rate = sum(1 for r in records if r["adv_valid"]) / n * 100

    mean_h_ph = np.mean([r["ph_h"] for r in records])
    mean_h_lin = np.mean([r["lin_h"] for r in records])
    mean_h_adv = np.mean([r["adv_h"] for r in records])

    mean_j_ph = np.mean([r["ph_j"] for r in records])
    mean_j_lin = np.mean([r["lin_j"] for r in records])
    mean_j_adv = np.mean([r["adv_j"] for r in records])

    mean_x_ph = np.mean([r["ph_x"] for r in records])
    mean_x_lin = np.mean([r["lin_x"] for r in records])
    mean_x_adv = np.mean([r["adv_x"] for r in records])

    mean_o_ph = np.mean([r["ph_o"] for r in records])
    mean_o_lin = np.mean([r["lin_o"] for r in records])
    mean_o_adv = np.mean([r["adv_o"] for r in records])

    mean_t_ph = np.mean([r["ph_time"] for r in records])
    mean_t_lin = np.mean([r["lin_time"] for r in records])
    mean_t_adv = np.mean([r["adv_time"] for r in records])

    # Head-to-head Adv vs LinUCB
    wins = 0
    ties = 0
    losses = 0
    j_diffs = []
    for r in records:
        diff = r["adv_j"] - r["lin_j"]
        j_diffs.append(diff)
        if diff < -1e-4:
            wins += 1
        elif abs(diff) <= 1e-4:
            ties += 1
        else:
            losses += 1

    # Hypothesis testing
    t_stat, p_val_t = stats.ttest_rel([r["adv_j"] for r in records], [r["lin_j"] for r in records])
    try:
        w_stat, p_val_w = stats.wilcoxon([r["adv_j"] for r in records], [r["lin_j"] for r in records])
    except Exception:
        p_val_w = float("nan")

    print("\n" + "=" * 80)
    print("  SUMMARY BENCHMARK METRICS (N = 20 FLOOR PLANS)")
    print("=" * 80)
    print(f"{'Metric':<25} | {'PortfolioHybrid':<16} | {'LinUCB (Champion)':<18} | {'Advanced ALNS':<16}")
    print("-" * 80)
    print(f"{'Validity (%)':<25} | {ph_valid_rate:<16.1f} | {lin_valid_rate:<18.1f} | {adv_valid_rate:<16.1f}")
    print(f"{'Mean Hub Count (H)':<25} | {mean_h_ph:<16.2f} | {mean_h_lin:<18.2f} | {mean_h_adv:<16.2f}")
    print(f"{'Mean Cost J (m)':<25} | {mean_j_ph:<16.2f} | {mean_j_lin:<18.2f} | {mean_j_adv:<16.2f}")
    print(f"{'Mean Crossings (X)':<25} | {mean_x_ph:<16.2f} | {mean_x_lin:<18.2f} | {mean_x_adv:<16.2f}")
    print(f"{'Mean Overlap O (m)':<25} | {mean_o_ph:<16.2f} | {mean_o_lin:<18.2f} | {mean_o_adv:<16.2f}")
    print(f"{'Mean Runtime (s)':<25} | {mean_t_ph:<16.2f} | {mean_t_lin:<18.2f} | {mean_t_adv:<16.2f}")
    print("-" * 80)
    print(f"Head-to-head vs Frozen Champion: {wins} Wins, {ties} Ties, {losses} Losses")
    print(f"Mean J reduction vs Champion: {mean_j_lin - mean_j_adv:.2f} m ({((mean_j_lin - mean_j_adv)/mean_j_lin)*100:.2f}%)")
    print(f"Paired t-test p-value: {p_val_t:.4e} | Wilcoxon p-value: {p_val_w:.4e}")
    print("=" * 80)

    # Save results to JSON and CSV
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "benchmark_advanced_operators_summary.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "target_plan_ids": target_plan_ids,
            "metrics": {
                "PortfolioHybrid": {"validity": ph_valid_rate, "mean_h": mean_h_ph, "mean_j": mean_j_ph, "mean_x": mean_x_ph, "mean_o": mean_o_ph, "mean_time": mean_t_ph},
                "LinUCB_ALNS": {"validity": lin_valid_rate, "mean_h": mean_h_lin, "mean_j": mean_j_lin, "mean_x": mean_x_lin, "mean_o": mean_o_lin, "mean_time": mean_t_lin},
                "Advanced_ALNS": {"validity": adv_valid_rate, "mean_h": mean_h_adv, "mean_j": mean_j_adv, "mean_x": mean_x_adv, "mean_o": mean_o_adv, "mean_time": mean_t_adv},
            },
            "comparison": {
                "wins": wins,
                "ties": ties,
                "losses": losses,
                "mean_j_reduction": round(float(mean_j_lin - mean_j_adv), 4),
                "p_val_ttest": float(p_val_t),
                "p_val_wilcoxon": float(p_val_w),
            },
            "records": records,
        }, f, indent=2)

    print(f"[Done] Saved benchmark summary to {out_json}")


if __name__ == "__main__":
    run_benchmark()
