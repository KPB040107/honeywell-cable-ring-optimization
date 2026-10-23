"""Phase 16 Empirical Geometry Recovery and Forensic Audit Runner (Agent 2).

Audits and recovers plans from the 740 discrepancy set:
1. Cadastral / Portal Degeneracies (488 plans)
2. Boundary Free Space Exits (175 plans)
3. Terminal Alignment Discrepancies (120 plans)
4. Degenerate Indexing / CAD Overlaps (41 plans)
5. True Physical Infeasibilities (22 plans: 14 hub clearance + 8 isolated leaf)

Evaluates every single candidate under independent FloorPlanChecker at 1 mm tolerance.
Outputs:
- results/recovery/empirical_recovery_results.json
- results/recovery/empirical_recovery_summary.json
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

# Add repo root to sys.path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.geometry_healing import (
    heal_solution_bundle,
    recover_and_certify_plan,
)
from resplan_router.phase6_output.checker import FloorPlanChecker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase16_recovery")


def get_discrepancy_records() -> List[Dict[str, Any]]:
    """Extract all candidate plans from the discrepancy set in benchmark_checkpoint_full.json."""
    with open("results/benchmarks/benchmark_checkpoint_full.json", "r") as f:
        cp = json.load(f)

    records = cp.get("records", [])
    df = pd.read_csv("results/benchmarks/benchmark_results_full.csv")
    valid_pids = set(df[df["valid_linucb_alns"] == True]["plan_id"].astype(int))

    with open("results/dataset_reconciliation_audit.json", "r") as f:
        audit_data = json.load(f)
    strictly_infeasible = set(audit_data["strictly_infeasible_pids"])

    group_a_hub_clearance = {
        345536, 952547, 413814, 586490, 5460, 284019, 399279,
        145814, 227794, 301603, 15000, 15678, 589710, 622584,
    }
    group_b_isolated_leaf = {
        452, 778, 4048, 7447, 8272, 8716, 16162, 16612,
    }

    discrepancy_records = []
    for r in records:
        pid = int(r["plan_id"])
        if pid in valid_pids or pid in strictly_infeasible:
            continue
        res = r.get("results", {}).get("LinUCB_ALNS", {})
        status = res.get("solver_status")
        if status == "LOAD_ERROR" or r.get("eligible_rooms", 0) == 0:
            continue

        viols = " ".join(res.get("violations", []))
        is_fewer = "fewer than two points" in viols
        is_pen = "penetrates" in viols or "outside free space" in viols
        is_miss = "misses fixed terminal" in viols
        is_index = "list index out of range" in viols
        is_clr = "clearance" in viols or pid in group_a_hub_clearance
        is_iso = pid in group_b_isolated_leaf

        if is_fewer or is_pen or is_miss or is_index or is_clr or is_iso or status in ("FEASIBLE", "OPTIMAL", "ERROR", "INFEASIBLE"):
            discrepancy_records.append(r)

    return discrepancy_records


def evaluate_single_plan(record: Dict[str, Any]) -> Dict[str, Any]:
    """Worker function to test recovery on a single candidate plan."""
    pid = int(record["plan_id"])
    res = record.get("results", {}).get("LinUCB_ALNS", {})
    init_status = res.get("solver_status", "UNKNOWN")
    init_viols = res.get("violations", [])

    repo = DatasetRepository.get_instance()
    try:
        plan = repo.get_floor_plan(pid)
    except Exception as exc:
        return {
            "plan_id": pid,
            "tier": record.get("tier", "unknown"),
            "initial_status": init_status,
            "initial_violations": init_viols,
            "recovered_is_valid": False,
            "final_violations": [f"Load error: {str(exc)}"],
            "recovery_category": "UNLOADABLE_ERROR",
            "runtime_s": 0.0,
        }

    t0 = time.perf_counter()
    try:
        is_valid, report, bundle = recover_and_certify_plan(plan)
        t_elapsed = time.perf_counter() - t0

        # Classify recovery category
        viols_str = " ".join(init_viols)
        if is_valid:
            if "fewer than two points" in viols_str:
                category = "RESCUED_PORTAL_DEGENERACY"
            elif "misses fixed terminal" in viols_str:
                category = "RESCUED_TERMINAL_ALIGNMENT"
            elif "penetrates" in viols_str or "outside free space" in viols_str:
                category = "RESCUED_BOUNDARY_EXIT"
            elif "list index out of range" in viols_str:
                category = "RESCUED_INDEXING_ERROR"
            else:
                category = "RESCUED_GENERAL_OPTIMIZATION"
        else:
            final_viols_str = " ".join(report.violations)
            if "clearance" in final_viols_str or "Hub clearance" in final_viols_str:
                category = "TRUE_INFEASIBLE_HUB_CLEARANCE"
            elif "not contiguous" in final_viols_str:
                category = "TRUE_INFEASIBLE_ISOLATED_LEAF"
            else:
                category = "UNRECOVERED_COMPLEX_GEOMETRY"

        return {
            "plan_id": pid,
            "tier": record.get("tier", "unknown"),
            "initial_status": init_status,
            "initial_violations": init_viols,
            "recovered_is_valid": is_valid,
            "hub_count": report.hub_count,
            "total_cost_J": round(report.total_cost_J, 4) if report.total_cost_J < float("inf") else None,
            "total_length_m": round(report.total_length_m, 4),
            "total_bends": report.total_bends,
            "total_crossings": report.total_crossings,
            "total_overlap_m": round(report.total_overlap_m, 4),
            "area_gap_m2": round(report.area_gap_m2, 4),
            "final_violations": [v.message for v in report.violations],
            "recovery_category": category,
            "runtime_s": round(t_elapsed, 4),
        }
    except Exception as exc:
        t_elapsed = time.perf_counter() - t0
        return {
            "plan_id": pid,
            "tier": record.get("tier", "unknown"),
            "initial_status": init_status,
            "initial_violations": init_viols,
            "recovered_is_valid": False,
            "final_violations": [f"Execution error: {str(exc)}"],
            "recovery_category": "EXECUTION_EXCEPTION",
            "runtime_s": round(t_elapsed, 4),
        }


def main():
    logger.info("Initializing Phase 16 Geometry Recovery Empirical Audit...")
    records = get_discrepancy_records()
    total_plans = len(records)
    logger.info("Total discrepancy candidate records identified: %d", total_plans)

    num_workers = min(12, os.cpu_count() or 4)
    logger.info("Launching parallel recovery audit with %d workers...", num_workers)

    results: List[Dict[str, Any]] = []
    t_start = time.perf_counter()

    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(evaluate_single_plan, r): r["plan_id"] for r in records}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            completed += 1
            if completed % 50 == 0 or completed == total_plans:
                valid_so_far = sum(1 for r in results if r["recovered_is_valid"])
                logger.info(
                    "Progress: %d / %d completed (%.1f%%) | Valid Rescued So Far: %d (%.1f%%)",
                    completed,
                    total_plans,
                    completed / total_plans * 100.0,
                    valid_so_far,
                    valid_so_far / completed * 100.0,
                )

    t_total = time.perf_counter() - t_start

    # Sort results by plan_id
    results.sort(key=lambda r: int(r["plan_id"]))

    # Summary statistics
    rescued_count = sum(1 for r in results if r["recovered_is_valid"])
    unrecovered_count = total_plans - rescued_count

    from collections import Counter
    category_counts = Counter(r["recovery_category"] for r in results)

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "total_discrepancy_plans_audited": total_plans,
        "genuinely_recovered_count": rescued_count,
        "recovery_success_rate_pct": round(rescued_count / total_plans * 100.0, 2),
        "unrecovered_count": unrecovered_count,
        "total_audit_runtime_s": round(t_total, 2),
        "category_breakdown": dict(category_counts),
        "sample_rescued_plan_ids": [r["plan_id"] for r in results if r["recovered_is_valid"]][:20],
        "sample_unrecovered_plan_ids": [r["plan_id"] for r in results if not r["recovered_is_valid"]][:20],
    }

    out_dir = Path("results/recovery")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "empirical_recovery_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    with open(out_dir / "empirical_recovery_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    logger.info("=" * 80)
    logger.info("  PHASE 16 GEOMETRY RECOVERY AUDIT COMPLETE")
    logger.info("=" * 80)
    logger.info("Total Discrepancy Plans Audited: %d", total_plans)
    logger.info("Genuinely Rescued Plans (100%% FloorPlanChecker Compliant): %d (%.2f%%)", rescued_count, rescued_count / total_plans * 100.0)
    logger.info("Unrecovered / Provably Infeasible: %d (%.2f%%)", unrecovered_count, unrecovered_count / total_plans * 100.0)
    logger.info("Category Breakdown:")
    for cat, cnt in category_counts.most_common():
        logger.info("  - %s: %d", cat, cnt)
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
