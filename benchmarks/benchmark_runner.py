"""Multi-Process Benchmark Runner for Honeywell Cable Ring Optimization.

Executes systematic, reproducible benchmarks across ResPlan floor plans using
the official BaseFloorPlanOptimizer interface and independent FloorPlanChecker.
Supports Stage 1 (Smoke - 5 plans), Stage 2 (Stratified - 100 plans), and full custom runs.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Resplan Router imports
from resplan_router.contracts import MetricFloorPlan
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.optimizers.bitmask_dp import BitmaskDPOptimizer
from resplan_router.optimizers.cpsat_hanan import CPSATHananOptimizer
from resplan_router.optimizers.cpsat_multipath import CPSATMultiPathOptimizer
from resplan_router.optimizers.alns_optimizer import ALNSOptimizer
from resplan_router.optimizers.genetic_optimizer import GeneticOptimizer
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record

INVENTORY_PATH = Path("results/dataset_inventory.json")
RESULTS_DIR = Path("results")
REPORTS_DIR = Path("reports")
CHECKPOINT_PATH = RESULTS_DIR / "benchmark_checkpoint.json"
CSV_PATH = RESULTS_DIR / "benchmark_results.csv"

AVAILABLE_OPTIMIZERS: Dict[str, Any] = {
    "GreedyBaseline": GreedyBaselineOptimizer,
    "BitmaskDP": BitmaskDPOptimizer,
    "CPSAT_Hanan": CPSATHananOptimizer,
    "CPSAT_MultiPath": CPSATMultiPathOptimizer,
    "ALNS": ALNSOptimizer,
    "GeneticAlgorithm": GeneticOptimizer,
    "PortfolioHybrid": PortfolioHybridOptimizer,
}


def load_plan(plan_id: int) -> MetricFloorPlan:
    """Load and adapt a floor plan from ResPlan.pkl."""
    repo = DatasetRepository.get_instance()
    rec = repo.get_record(plan_id)
    return adapt_resplan_record(rec)


def _worker_run_optimizer(
    algo_name: str,
    plan_id: int,
    time_limit_s: float,
    seed: int,
) -> Dict[str, Any]:
    """Worker function for running one optimizer on one floor plan."""
    t0 = time.perf_counter()
    try:
        plan = load_plan(plan_id)
        cls = AVAILABLE_OPTIMIZERS[algo_name]
        opt: BaseFloorPlanOptimizer = cls()
        res: OptimizerResult = opt.optimize(floor_plan=plan, time_limit_seconds=time_limit_s, random_seed=seed)
        d = res.as_dict()
        d["eligible_rooms"] = len([r for r in plan.rooms.values() if r.is_eligible])
        d["total_area_m2"] = round(plan.total_area_m2, 2)
        return d
    except Exception as e:
        return {
            "algorithm_name": algo_name,
            "plan_id": str(plan_id),
            "h_count": 0,
            "total_length_m": 0.0,
            "total_bends": 0,
            "total_crossings": 0,
            "total_overlap_m": 0.0,
            "total_cost_J": float("inf"),
            "area_gap_m2": 0.0,
            "is_valid": False,
            "solver_status": "ERROR",
            "runtime_s": round(time.perf_counter() - t0, 4),
            "violations": [str(e)],
            "rules_passed": {},
            "diagnostic_info": {"exception": str(e)},
            "eligible_rooms": 0,
            "total_area_m2": 0.0,
        }


class BenchmarkHarness:
    """Manages parallel benchmark execution, checkpointing, and reporting."""

    def __init__(
        self,
        stage_name: str,
        plan_ids: List[int],
        algorithms: List[str],
        concurrency: int = 4,
        time_limit_s: float = 10.0,
        random_seed: int = 42,
    ) -> None:
        self.stage_name = stage_name
        self.plan_ids = plan_ids
        self.algorithms = algorithms
        self.concurrency = min(concurrency, 4)
        self.time_limit_s = time_limit_s
        self.random_seed = random_seed
        self.checkpoint_data: Dict[str, Any] = self._load_checkpoint()

    def _load_checkpoint(self) -> Dict[str, Any]:
        if CHECKPOINT_PATH.exists():
            try:
                with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_checkpoint(self) -> None:
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(self.checkpoint_data, f, indent=2)

    def _init_csv(self) -> None:
        if not CSV_PATH.exists():
            CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "stage", "plan_id", "algorithm", "h_count", "length_m", "bends",
                    "crossings", "overlap_m", "cost_J", "area_gap_m2", "is_valid",
                    "status", "runtime_s", "eligible_rooms", "total_area_m2"
                ])

    def _append_csv(self, row: Dict[str, Any]) -> None:
        with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                self.stage_name,
                row["plan_id"],
                row["algorithm_name"],
                row["h_count"],
                row["total_length_m"],
                row["total_bends"],
                row["total_crossings"],
                row["total_overlap_m"],
                row["total_cost_J"],
                row["area_gap_m2"],
                row["is_valid"],
                row["solver_status"],
                row["runtime_s"],
                row.get("eligible_rooms", 0),
                row.get("total_area_m2", 0.0),
            ])

    def run(self) -> List[Dict[str, Any]]:
        """Run all requested benchmarks."""
        self._init_csv()
        tasks = []
        for pid in self.plan_ids:
            for algo in self.algorithms:
                key = f"{self.stage_name}_{pid}_{algo}"
                if key not in self.checkpoint_data:
                    tasks.append((algo, pid, self.time_limit_s, self.random_seed))

        print(f"[{self.stage_name.upper()}] Starting benchmark: {len(self.plan_ids)} plans x {len(self.algorithms)} algorithms = {len(self.plan_ids)*len(self.algorithms)} evaluations ({len(tasks)} remaining to run). Concurrency: {self.concurrency}", flush=True)

        results: List[Dict[str, Any]] = []

        # Load already completed results from checkpoint
        for pid in self.plan_ids:
            for algo in self.algorithms:
                key = f"{self.stage_name}_{pid}_{algo}"
                if key in self.checkpoint_data:
                    results.append(self.checkpoint_data[key])

        if not tasks:
            print("[INFO] All requested benchmarks already cached in checkpoint.", flush=True)
            return results

        completed_count = 0
        total_tasks = len(tasks)

        # Execute using ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_task = {
                executor.submit(_worker_run_optimizer, algo, pid, t_lim, seed): (algo, pid)
                for algo, pid, t_lim, seed in tasks
            }

            for future in as_completed(future_to_task):
                algo, pid = future_to_task[future]
                completed_count += 1
                try:
                    res_dict = future.result()
                except Exception as exc:
                    res_dict = {
                        "algorithm_name": algo,
                        "plan_id": str(pid),
                        "h_count": 0,
                        "total_length_m": 0.0,
                        "total_bends": 0,
                        "total_crossings": 0,
                        "total_overlap_m": 0.0,
                        "total_cost_J": float("inf"),
                        "area_gap_m2": 0.0,
                        "is_valid": False,
                        "solver_status": "CRASH",
                        "runtime_s": 0.0,
                        "violations": [str(exc)],
                        "rules_passed": {},
                        "diagnostic_info": {"exception": str(exc)},
                        "eligible_rooms": 0,
                        "total_area_m2": 0.0,
                    }

                key = f"{self.stage_name}_{pid}_{algo}"
                self.checkpoint_data[key] = res_dict
                self._save_checkpoint()
                self._append_csv(res_dict)
                results.append(res_dict)

                v_str = "VALID" if res_dict.get("is_valid") else "INVALID"
                print(
                    f"[{completed_count}/{total_tasks}] Plan {pid:5d} | {algo:15s} -> "
                    f"H={res_dict['h_count']}, J={res_dict['total_cost_J']:6.2f}, "
                    f"{v_str} ({res_dict['runtime_s']:5.2f}s)",
                    flush=True,
                )

        return results

    def generate_report(self, results: List[Dict[str, Any]], report_filename: str) -> Path:
        """Generate comprehensive markdown comparative benchmark report."""
        report_path = REPORTS_DIR / report_filename
        report_path.parent.mkdir(parents=True, exist_ok=True)

        # Aggregate stats per algorithm
        algo_stats: Dict[str, Dict[str, Any]] = {}
        for algo in self.algorithms:
            matching = [r for r in results if r["algorithm_name"] == algo and str(r["plan_id"]) in [str(p) for p in self.plan_ids]]
            n = len(matching)
            if n == 0:
                continue
            valid_count = sum(1 for r in matching if r.get("is_valid"))
            valid_pct = (valid_count / n) * 100.0

            valid_runs = [r for r in matching if r.get("is_valid")]
            if valid_runs:
                avg_h = sum(r["h_count"] for r in valid_runs) / len(valid_runs)
                avg_l = sum(r["total_length_m"] for r in valid_runs) / len(valid_runs)
                avg_b = sum(r["total_bends"] for r in valid_runs) / len(valid_runs)
                avg_x = sum(r["total_crossings"] for r in valid_runs) / len(valid_runs)
                avg_o = sum(r["total_overlap_m"] for r in valid_runs) / len(valid_runs)
                avg_j = sum(r["total_cost_J"] for r in valid_runs) / len(valid_runs)
                avg_t = sum(r["runtime_s"] for r in valid_runs) / len(valid_runs)
            else:
                avg_h = avg_l = avg_b = avg_x = avg_o = avg_j = avg_t = 0.0

            algo_stats[algo] = {
                "total_plans": n,
                "valid_count": valid_count,
                "valid_pct": round(valid_pct, 1),
                "avg_h": round(avg_h, 2),
                "avg_l": round(avg_l, 2),
                "avg_b": round(avg_b, 1),
                "avg_x": round(avg_x, 2),
                "avg_o": round(avg_o, 2),
                "avg_j": round(avg_j, 2),
                "avg_runtime_s": round(avg_t, 3),
            }

        md = []
        md.append(f"# ResPlan Optimization Benchmark Report: {self.stage_name.upper()}\n")
        md.append(f"**Execution Timestamp:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        md.append(f"**Stage:** `{self.stage_name}` | **Plans Evaluated:** {len(self.plan_ids)} | **Algorithms:** {', '.join(self.algorithms)}\n")
        md.append(f"**Hardware Environment:** Intel Core i5-13450HX (10 cores, 16 threads), Concurrency: {self.concurrency}\n")
        md.append("\n---\n")

        md.append("## 1. Executive Performance Summary\n")
        md.append("| Algorithm | Total Plans | Valid Solutions | Validity Rate | Mean Hubs (H) | Mean Length L (m) | Mean Bends (B) | Mean Crossings (X) | Mean Overlap O (m) | Mean Score (J) | Mean Runtime (s) |\n")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for algo, s in algo_stats.items():
            md.append(
                f"| **`{algo}`** | {s['total_plans']} | {s['valid_count']} | **{s['valid_pct']}%** | "
                f"**{s['avg_h']}** | {s['avg_l']} | {s['avg_b']} | {s['avg_x']} | {s['avg_o']} | "
                f"**{s['avg_j']}** | {s['avg_runtime_s']}s |\n"
            )

        md.append("\n---\n")
        md.append("## 2. Head-to-Head Lexicographical Comparison\n")
        md.append("Under the official rules:\n")
        md.append("1. **Primary criterion:** Fewer hubs $H$ strictly wins.\n")
        md.append("2. **Secondary criterion:** For equal $H$, lower objective cost $J = L + 5X + O + 0.5B$ wins.\n")
        md.append("3. **Tertiary criterion:** For equal $H$ and $J$, smaller assigned area gap $\\Delta A_{\\text{gap}}$ wins.\n\n")

        # Compute pairwise dominance across all evaluated plans
        plan_results: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for r in results:
            pid = str(r["plan_id"])
            if pid not in plan_results:
                plan_results[pid] = {}
            plan_results[pid][r["algorithm_name"]] = r

        if len(self.algorithms) >= 2:
            md.append("| Metric Comparison | " + " | ".join(f"`{a}` Wins" for a in self.algorithms) + " | Ties |\n")
            md.append("|---|" + "|".join("---" for _ in self.algorithms) + "|---|\n")

            # Compare pairwise
            a1 = self.algorithms[0]
            a2 = self.algorithms[1]
            a1_wins = 0
            a2_wins = 0
            ties = 0

            for pid in [str(p) for p in self.plan_ids]:
                res_map = plan_results.get(pid, {})
                r1 = res_map.get(a1)
                r2 = res_map.get(a2)
                if not r1 or not r2:
                    continue

                v1 = r1.get("is_valid", False)
                v2 = r2.get("is_valid", False)
                if v1 and not v2:
                    a1_wins += 1
                elif v2 and not v1:
                    a2_wins += 1
                elif not v1 and not v2:
                    ties += 1
                else:
                    # Both valid: compare H
                    if r1["h_count"] < r2["h_count"]:
                        a1_wins += 1
                    elif r2["h_count"] < r1["h_count"]:
                        a2_wins += 1
                    else:
                        # Equal H: compare J
                        if r1["total_cost_J"] < r2["total_cost_J"] - 1e-4:
                            a1_wins += 1
                        elif r2["total_cost_J"] < r1["total_cost_J"] - 1e-4:
                            a2_wins += 1
                        else:
                            # Equal J: compare area gap
                            if r1["area_gap_m2"] < r2["area_gap_m2"] - 1e-4:
                                a1_wins += 1
                            elif r2["area_gap_m2"] < r1["area_gap_m2"] - 1e-4:
                                a2_wins += 1
                            else:
                                ties += 1

            md.append(f"| **`{a1}` vs `{a2}`** | **{a1_wins}** | **{a2_wins}** | {ties} |\n")

        md.append("\n---\n")
        md.append("## 3. Plan-by-Plan Detailed Scorecard\n")
        md.append("| Plan ID | Eligible Rooms | Area (m²) | Algorithm | Hubs (H) | Length L (m) | Crossings (X) | Overlap O (m) | Bends (B) | Score (J) | Area Gap (m²) | Valid? | Runtime (s) |\n")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")

        for pid in [str(p) for p in self.plan_ids]:
            res_map = plan_results.get(pid, {})
            for algo in self.algorithms:
                r = res_map.get(algo)
                if not r:
                    continue
                v_str = "PASS" if r.get("is_valid") else "FAIL"
                md.append(
                    f"| `{pid}` | {r.get('eligible_rooms', '-')} | {r.get('total_area_m2', '-')} | `{algo}` | "
                    f"**{r['h_count']}** | {r['total_length_m']} | {r['total_crossings']} | {r['total_overlap_m']} | "
                    f"{r['total_bends']} | **{r['total_cost_J']}** | {r['area_gap_m2']} | **{v_str}** | {r['runtime_s']}s |\n"
                )

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("".join(md))

        print(f"\n[OK] Comparative benchmark report saved to {report_path}", flush=True)
        return report_path


def main():
    parser = argparse.ArgumentParser(description="Multi-Process ResPlan Cable Optimizer Benchmark Runner")
    parser.add_argument("--stage", choices=["smoke", "stratified", "custom"], default="smoke")
    parser.add_argument("--plans", type=str, default="", help="Comma-separated plan IDs for custom stage")
    parser.add_argument("--algorithms", type=str, default="GreedyBaseline,BitmaskDP,CPSAT_Hanan", help="Comma-separated algorithms")
    parser.add_argument("--concurrency", type=int, default=4, help="Max parallel worker processes (max 4)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-plan time limit in seconds")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    # Determine plan IDs
    if args.stage == "smoke":
        plan_ids = [6361, 3854, 112, 3083, 5211]
    elif args.stage == "stratified":
        if not INVENTORY_PATH.exists():
            print(f"[ERROR] Inventory path {INVENTORY_PATH} not found!")
            sys.exit(1)
        with open(INVENTORY_PATH, "r", encoding="utf-8") as f:
            inv = json.load(f)
        plan_ids = inv["benchmarks"]["stage_2_stratified_100"]["plan_ids"]
    elif args.stage == "custom":
        if not args.plans:
            print("[ERROR] Must specify --plans with comma-separated IDs for custom stage")
            sys.exit(1)
        plan_ids = [int(p.strip()) for p in args.plans.split(",") if p.strip()]
    else:
        plan_ids = []

    algorithms = [a.strip() for a in args.algorithms.split(",") if a.strip()]

    harness = BenchmarkHarness(
        stage_name=args.stage,
        plan_ids=plan_ids,
        algorithms=algorithms,
        concurrency=args.concurrency,
        time_limit_s=args.timeout,
        random_seed=args.seed,
    )

    results = harness.run()
    report_name = f"benchmark_stage1_report.md" if args.stage == "smoke" else (
        f"benchmark_stage2_report.md" if args.stage == "stratified" else f"benchmark_custom_report.md"
    )
    harness.generate_report(results, report_name)


if __name__ == "__main__":
    main()
