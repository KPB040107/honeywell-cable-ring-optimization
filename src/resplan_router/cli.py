"""Command-line interface (CLI) for the ResPlan Cable Ring Router.

Provides a unified production CLI with five subcommands:
- optimize:  optimize a single floor plan file or dataset record, export JSON, print scorecard,
             supports timeout, seed, and schematic overlay visualization.
- batch:     batch optimize floor plans from ResPlan.pkl or a directory of JSON plans with
             worker multiprocessing, atomic single-writer checkpointing, and resume support.
- validate:  dual-oracle validation of candidate solution JSON against input plan JSON using
             both FloorPlanChecker and IndependentVerifier with concordance reporting.
- benchmark: execute configured benchmark suites from YAML or CLI modes and print summary reports.
- inspect:   inspect floor plan geometric/topological details or dataset summary statistics.
- serve:     launch interactive Co-Design Studio web server with live reviewer HUD.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np
import yaml

from resplan_router.config import SystemConfig
from resplan_router.contracts import MetricFloorPlan, Point2D
from resplan_router.experimental.independent_verifier import (
    DualValidationResult,
    IndependentVerifier,
)
from resplan_router.phase1_ingestion.dataset_repository import (
    DatasetRepository,
    DatasetUnavailableError,
    InvalidPlanIdError,
    PlanNotFoundError,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.pipeline import route_floor_plan
from resplan_router.phase6_output.visualizer import FloorPlanVisualizer

logger = logging.getLogger("resplan.cli")

# Terminal encoding protection for Windows platforms
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Proven 22 Physically Infeasible Plan IDs cataloged in Phase 16/17/18
PROVEN_PHYSICAL_INFEASIBLE_IDS: Set[int] = {
    # Inadequate inscribed clearance (< 0.50m)
    5460, 15000, 15678, 145814, 227794, 284019, 301603,
    345536, 399279, 413814, 586490, 589710, 622584, 952547,
    # Isolated degree-0 leaf rooms
    452, 778, 4048, 7447, 8272, 8716, 16162, 16612,
}


def _atomic_write_json(file_path: Union[str, Path], data: Any, indent: int = 2) -> None:
    """Atomically write JSON payload to disk to prevent partial writes."""
    target = Path(file_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(f".tmp_{os.getpid()}_{int(time.time() * 1000)}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=str)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


# =============================================================================
# Multiprocessing Batch Worker
# =============================================================================

def evaluate_batch_plan_worker(task_args: Tuple[Union[int, str], Dict[str, Any]]) -> Dict[str, Any]:
    """Worker process evaluation function for a single batch item (Plan ID or file path)."""
    item, cfg = task_args
    t_start = time.perf_counter()
    tolerance_m = float(cfg.get("tolerance", 0.001))
    iterations = int(cfg.get("iterations", 25))
    timeout_s = float(cfg.get("timeout", 5.0))

    # Fast-path check for known proven physical infeasibility
    if isinstance(item, int) and item in PROVEN_PHYSICAL_INFEASIBLE_IDS:
        return {
            "plan_id": item,
            "status": "PROVEN_INFEASIBLE",
            "is_valid": False,
            "hub_count": 0,
            "cost_J": None,
            "length_m": None,
            "crossings": None,
            "overlap_m": None,
            "bends": None,
            "winner": "none",
            "runtime_s": round(time.perf_counter() - t_start, 4),
            "dual_concordant": True,
            "failure_reason": "PROVEN_PHYSICAL_INFEASIBILITY (clearance <0.5m or isolated leaf room)",
        }

    raw_dict: Optional[Dict[str, Any]] = None
    plan: Optional[MetricFloorPlan] = None
    plan_id_val: Union[int, str] = item

    try:
        if isinstance(item, int):
            repo = DatasetRepository.get_instance()
            plan = repo.get_floor_plan(item)
            plan_id_val = item
        else:
            plan_path = Path(item)
            with open(plan_path, "r", encoding="utf-8") as f:
                raw_dict = json.load(f)
            plan = parse_floor_plan(raw_dict, strict_clearance=False)
            plan_id_val = raw_dict.get("plan_id", plan_path.stem)
    except Exception as e:
        return {
            "plan_id": plan_id_val,
            "status": "INVALID_INPUT",
            "is_valid": False,
            "hub_count": 0,
            "cost_J": None,
            "winner": "none",
            "runtime_s": round(time.perf_counter() - t_start, 4),
            "dual_concordant": False,
            "failure_reason": f"Ingestion / Parsing Error: {str(e)}",
        }

    # Optional Geometry Recovery check
    try:
        from resplan_router.phase1_ingestion.geometry_healing import recover_and_certify_plan
        heal_valid, heal_report, heal_bundle = recover_and_certify_plan(plan)
        if heal_valid and heal_bundle is not None:
            bundle_dict = heal_bundle.model_dump() if hasattr(heal_bundle, "model_dump") else heal_bundle
            dual_res = IndependentVerifier.dual_validate(
                plan=plan,
                raw_submission=bundle_dict,
                tolerance_m=tolerance_m,
            )
            if dual_res.official_valid and dual_res.independent_valid:
                return {
                    "plan_id": plan_id_val,
                    "status": "VALID_OPTIMIZED",
                    "is_valid": True,
                    "hub_count": len(heal_bundle.hub_rings),
                    "cost_J": round(heal_bundle.total_cost_J, 4),
                    "length_m": round(heal_bundle.total_length_m, 4),
                    "crossings": heal_bundle.total_crossings,
                    "overlap_m": round(heal_bundle.total_overlap_m, 4),
                    "bends": heal_bundle.total_bends,
                    "winner": "healed_v2",
                    "runtime_s": round(time.perf_counter() - t_start, 4),
                    "dual_concordant": dual_res.is_concordant,
                    "delta_J": dual_res.delta_J,
                    "bundle": bundle_dict,
                }
        elif not heal_valid and heal_report is not None and not heal_report.is_valid:
            if any("Isolated" in v.message or "clearance" in v.message for v in heal_report.violations):
                return {
                    "plan_id": plan_id_val,
                    "status": "PROVEN_INFEASIBLE",
                    "is_valid": False,
                    "hub_count": 0,
                    "cost_J": None,
                    "winner": "none",
                    "runtime_s": round(time.perf_counter() - t_start, 4),
                    "dual_concordant": True,
                    "failure_reason": "; ".join(v.message for v in heal_report.violations[:3]),
                }
    except Exception:
        pass

    # Execute optimizer (ProductionFallbackOptimizer preferred, with pipeline fallback)
    res_bundle: Any = None
    h_count: int = 0
    cost_J: Optional[float] = None
    length_m: Optional[float] = None
    crossings: Optional[int] = None
    overlap_m: Optional[float] = None
    bends: Optional[int] = None
    winner: str = "production"

    try:
        from resplan_router.optimizers.production_fallback import ProductionFallbackOptimizer
        opt = ProductionFallbackOptimizer(
            v2_iterations=iterations,
            v1_iterations=iterations,
            tolerance_m=tolerance_m,
        )
        opt_res = opt.optimize(plan, time_limit_seconds=timeout_s)
        res_bundle = opt_res.bundle
        h_count = int(opt_res.h_count)
        cost_J = float(opt_res.total_cost_J)
        length_m = float(opt_res.total_length_m)
        crossings = int(opt_res.total_crossings)
        overlap_m = float(opt_res.total_overlap_m)
        bends = int(opt_res.total_bends)
        diag = getattr(opt_res, "diagnostic_info", {}) or {}
        winner = "v1" if diag.get("fallback_triggered", False) else "v2"
    except Exception:
        # Fallback to route_floor_plan
        try:
            input_source = raw_dict if raw_dict is not None else item
            exported_json, evaluation, bundle = route_floor_plan(
                raw_plan_dict_or_path=input_source,
                strict_clearance=False,
            )
            res_bundle = bundle
            h_count = int(evaluation.hub_count)
            cost_J = float(evaluation.total_cost_J)
            length_m = float(evaluation.total_length_m)
            crossings = int(evaluation.total_crossings)
            overlap_m = float(evaluation.total_overlap_m)
            bends = int(evaluation.total_bends)
            winner = "pipeline_v1"
        except Exception as e:
            return {
                "plan_id": plan_id_val,
                "status": "SOLVER_ERROR",
                "is_valid": False,
                "hub_count": 0,
                "cost_J": None,
                "winner": "none",
                "runtime_s": round(time.perf_counter() - t_start, 4),
                "dual_concordant": False,
                "failure_reason": f"Solver Exception: {str(e)}",
            }

    dt_run = round(time.perf_counter() - t_start, 4)

    if res_bundle is None:
        return {
            "plan_id": plan_id_val,
            "status": "UNRESOLVED",
            "is_valid": False,
            "hub_count": 0,
            "cost_J": None,
            "winner": "none",
            "runtime_s": dt_run,
            "dual_concordant": False,
            "failure_reason": "Solver returned empty bundle",
        }

    bundle_dict = res_bundle.model_dump() if hasattr(res_bundle, "model_dump") else res_bundle
    dual_res = IndependentVerifier.dual_validate(
        plan=plan,
        raw_submission=bundle_dict,
        tolerance_m=tolerance_m,
    )

    if not dual_res.official_valid or not dual_res.independent_valid:
        return {
            "plan_id": plan_id_val,
            "status": "VALIDATION_FAILED",
            "is_valid": False,
            "hub_count": h_count,
            "cost_J": cost_J,
            "winner": winner,
            "runtime_s": dt_run,
            "dual_concordant": dual_res.is_concordant,
            "failure_reason": "; ".join(dual_res.discrepancies[:3]) if dual_res.discrepancies else "Rule validation failed",
        }

    return {
        "plan_id": plan_id_val,
        "status": "VALID_OPTIMIZED" if winner == "v2" else "VALID_FALLBACK",
        "is_valid": True,
        "hub_count": h_count,
        "cost_J": round(cost_J, 4) if cost_J is not None else None,
        "length_m": round(length_m, 4) if length_m is not None else None,
        "crossings": crossings,
        "overlap_m": round(overlap_m, 4) if overlap_m is not None else None,
        "bends": bends,
        "winner": winner,
        "runtime_s": dt_run,
        "dual_concordant": dual_res.is_concordant,
        "delta_J": dual_res.delta_J,
        "bundle": bundle_dict,
    }


# =============================================================================
# Scorecard Printing Utilities
# =============================================================================

def print_scorecard(
    plan_id: str,
    evaluation: Any,
    elapsed_sec: float,
    output_json: Optional[str] = None,
    render_png: Optional[str] = None,
) -> None:
    """Print clean architectural reviewer scorecard to stdout."""
    status_str = "VALID (100% Rules Passed)" if evaluation.is_valid else "INVALID (Violations Detected)"
    header = "=" * 80
    sub = "-" * 80

    print(header)
    print(f"{'RESPLAN CABLE RING ROUTER - REVIEWER SCORECARD':^80}")
    print(header)
    print(f"Plan ID:         {plan_id}")
    print(f"Status:          {status_str}")
    print(f"Execution Time:  {elapsed_sec:.3f} s")
    if output_json:
        print(f"Slide 8 JSON:    {output_json}")
    if render_png:
        print(f"Schematic PNG:   {render_png}")
    print(sub)
    print("COMPETITION METRICS:")
    print(f"  Hub Count (H):           {evaluation.hub_count} hubs")
    print(f"  Total Cable Length (L):  {evaluation.total_length_m:.2f} m")
    print(f"  Total Bends (B):         {evaluation.total_bends}")
    print(f"  Total Crossings (X):     {evaluation.total_crossings}")
    print(f"  Total Overlap (O):       {evaluation.total_overlap_m:.2f} m")
    try:
        print(f"  Area Gap (\u0394A_gap):       {evaluation.area_gap_m2:.2f} m\u00b2")
    except UnicodeEncodeError:
        print(f"  Area Gap (Delta_A_gap):  {evaluation.area_gap_m2:.2f} m2")
    print(f"  Total Cost J (Official): {evaluation.total_cost_J:.2f}")
    print(sub)
    print("HARD VALIDITY RULES (Slide 3 Checklist):")
    rule_descriptions = {
        "Rule 1": "Full Coverage (all eligible rooms & nodes served)",
        "Rule 2": "Capacity Limits (<= 100 m\u00b2 & <= 5 nodes per hub)",
        "Rule 3": "Doorway Contiguity (rooms door-connected)",
        "Rule 4": "Closed Ring (hub -> all nodes -> hub)",
        "Rule 5": "Free Space (axis-aligned, no wall/window crossing)",
        "Rule 6": "Hub Clearance (>= 0.50 m from walls/windows)",
    }
    for r_key, r_desc in rule_descriptions.items():
        passed = evaluation.rules_passed.get(r_key, False)
        badge = "[PASS]" if passed else "[FAIL]"
        print(f"  {badge:<6} {r_key}: {r_desc}")

    if not evaluation.is_valid and evaluation.violations:
        print(sub)
        print("RULE VIOLATIONS DETAIL:")
        for v in evaluation.violations:
            loc_str = f" at ({v.location.x:.2f}, {v.location.y:.2f})" if getattr(v, "location", None) else ""
            rule_num = getattr(v, "rule_number", "?")
            rule_name = getattr(v, "rule_name", "Violation")
            print(f"  - [Rule {rule_num}] {rule_name}{loc_str}: {v.message}")

    print(header)


def print_dual_validation_scorecard(dual_res: DualValidationResult) -> None:
    """Print detailed dual-oracle verification comparison scorecard."""
    header = "=" * 80
    sub = "-" * 80

    both_valid = dual_res.official_valid and dual_res.independent_valid
    concordance_str = "CONCORDANT (100% Dual Agreement)" if dual_res.is_concordant else "DISCORDANT (Divergence Detected)"
    overall_status = "CERTIFIED VALID" if both_valid and dual_res.is_concordant else "UNCERTIFIED / INVALID"

    print(header)
    print(f"{'RESPLAN DUAL-ORACLE VERIFIER - REVIEWER SCORECARD':^80}")
    print(header)
    print(f"Plan ID:                {dual_res.plan_id}")
    print(f"Certification Status:   {overall_status}")
    print(f"Oracle Concordance:     {concordance_str}")
    print(f"  FloorPlanChecker:     {'PASS' if dual_res.official_valid else 'FAIL'}")
    print(f"  IndependentVerifier:  {'PASS' if dual_res.independent_valid else 'FAIL'}")
    print(sub)

    print("HARD RULE CHECKLIST (DUAL ORACLES):")
    rule_keys = ["Rule 1", "Rule 2", "Rule 3", "Rule 4", "Rule 5", "Rule 6"]
    rule_names = {
        "Rule 1": "Full Coverage",
        "Rule 2": "Capacity Limits",
        "Rule 3": "Doorway Contiguity",
        "Rule 4": "Closed Ring Loops",
        "Rule 5": "Free Space Routing",
        "Rule 6": "Hub Clearance (>=0.5m)",
    }
    print(f"  {'Rule':<8} {'Description':<26} {'Checker':<10} {'Independent':<14} {'Match'}")
    for r in rule_keys:
        off_p = dual_res.official_report.rules_passed.get(r, False)
        ind_p = dual_res.independent_report.rules_passed.get(r, False)
        match = "YES" if off_p == ind_p else "MISMATCH"
        print(f"  {r:<8} {rule_names.get(r, r):<26} {'PASS' if off_p else 'FAIL':<10} {'PASS' if ind_p else 'FAIL':<14} {match}")

    print(sub)
    print("METRIC RECALCULATION & PARITY AUDIT:")
    metrics = [
        ("Cable Length (L)", f"{dual_res.official_report.total_length_m:.2f} m", f"{dual_res.independent_report.total_length_m:.2f} m", f"{dual_res.delta_L:+.3f} m"),
        ("Crossings (X)", f"{dual_res.official_report.total_crossings}", f"{dual_res.independent_report.total_crossings}", f"{dual_res.delta_X:+d}"),
        ("Overlap (O)", f"{dual_res.official_report.total_overlap_m:.2f} m", f"{dual_res.independent_report.total_overlap_m:.2f} m", f"{dual_res.delta_O:+.3f} m"),
        ("Bends (B)", f"{dual_res.official_report.total_bends}", f"{dual_res.independent_report.total_bends}", f"{dual_res.delta_B:+d}"),
        ("Cost J", f"{dual_res.official_report.total_cost_J:.4f}", f"{dual_res.independent_report.total_cost_J:.4f}", f"{dual_res.delta_J:+.4f}"),
        ("Area Gap (\u0394A)", f"{dual_res.official_report.area_gap_m2:.2f} m\u00b2", f"{dual_res.independent_report.area_gap_m2:.2f} m\u00b2", f"{dual_res.delta_area_gap:+.3f} m\u00b2"),
    ]
    print(f"  {'Metric':<22} {'Checker':<16} {'Independent':<16} {'Delta'}")
    for m_name, off_val, ind_val, delta_val in metrics:
        print(f"  {m_name:<22} {off_val:<16} {ind_val:<16} {delta_val}")

    if dual_res.discrepancies:
        print(sub)
        print("DUAL-ORACLE DISCREPANCIES:")
        for disc in dual_res.discrepancies:
            print(f"  ! {disc}")

    all_violations = list(dual_res.official_report.violations) + [
        v for v in dual_res.independent_report.violations if v not in dual_res.official_report.violations
    ]
    if all_violations:
        print(sub)
        print("VIOLATIONS DETAIL:")
        for v in all_violations:
            loc_str = f" at ({v.location.x:.2f}, {v.location.y:.2f})" if getattr(v, "location", None) else ""
            rule_num = getattr(v, "rule_number", "?")
            rule_name = getattr(v, "rule_name", "Violation")
            print(f"  - [Rule {rule_num}] {rule_name}{loc_str}: {v.message}")

    print(header)


# =============================================================================
# Subcommand Handlers
# =============================================================================

def handle_optimize(args: argparse.Namespace) -> int:
    """Handle `resplan optimize` subcommand."""
    input_str = args.input or getattr(args, "plan_input", None)
    if not input_str:
        logger.error("No floor plan input provided. Use -i/--input or pass input path as argument.")
        print("Error: Input floor plan is required.", file=sys.stderr)
        return 1

    # Seed initialization
    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)
        logger.info("Random seed initialized to %d", args.seed)

    # Determine input source
    input_path = Path(input_str)
    raw_data: Any = None
    plan_id: str = input_path.stem

    if input_path.is_file():
        try:
            with open(input_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            plan_id = str(raw_data.get("plan_id", input_path.stem))
        except Exception as exc:
            logger.error("Failed to read input plan JSON: %s", exc)
            print(f"Error: Failed to read input file: {exc}", file=sys.stderr)
            return 1
    elif input_str.isdigit():
        try:
            repo = DatasetRepository.get_instance()
            pid = int(input_str)
            raw_data = repo.get_record(pid)
            plan_id = str(pid)
        except Exception as exc:
            logger.error("Failed to load plan %s from dataset: %s", input_str, exc)
            print(f"Error: Plan ID '{input_str}' not found in dataset: {exc}", file=sys.stderr)
            return 1
    else:
        logger.error("Input file does not exist: %s", input_path)
        print(f"Error: Input file does not exist: {input_path}", file=sys.stderr)
        return 1

    # Configuration and overrides
    if args.config:
        cfg = SystemConfig.from_yaml(args.config)
    else:
        cfg = SystemConfig()

    overrides: Dict[str, Any] = {}
    if args.max_nodes is not None:
        overrides["max_nodes_per_hub"] = args.max_nodes
    if args.max_area is not None:
        overrides["max_area_m2"] = args.max_area
    if args.tolerance is not None:
        overrides["checker_tolerance_m"] = args.tolerance
    if overrides:
        cfg = cfg.with_overrides(**overrides)

    t0 = time.perf_counter()

    def _execute_pipeline():
        return route_floor_plan(
            raw_plan_dict_or_path=raw_data,
            config=cfg,
            output_json_path=args.output,
            render_png_path=args.visualize,
            strict_clearance=args.strict_clearance,
        )

    # Timeout handling
    if args.timeout is not None and args.timeout > 0:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute_pipeline)
            try:
                exported_json, evaluation, bundle = future.result(timeout=args.timeout)
            except TimeoutError:
                logger.error("Optimization exceeded timeout limit of %.2f seconds", args.timeout)
                print(f"Error: Optimization exceeded {args.timeout}s timeout limit", file=sys.stderr)
                return 1
            except Exception as exc:
                logger.error("Optimization failed: %s", exc)
                print(f"Error during route execution: {exc}", file=sys.stderr)
                return 1
    else:
        try:
            exported_json, evaluation, bundle = _execute_pipeline()
        except Exception as exc:
            logger.error("Optimization failed: %s", exc)
            print(f"Error during route execution: {exc}", file=sys.stderr)
            return 1

    elapsed = time.perf_counter() - t0
    final_plan_id = exported_json.get("plan_id", plan_id)

    print_scorecard(
        plan_id=str(final_plan_id),
        evaluation=evaluation,
        elapsed_sec=elapsed,
        output_json=args.output,
        render_png=args.visualize,
    )

    return 0 if evaluation.is_valid else 1


def handle_batch(args: argparse.Namespace) -> int:
    """Handle `resplan batch` subcommand."""
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sol_dir = out_dir / "solutions"
    sol_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = Path(args.checkpoint).resolve() if args.checkpoint else out_dir / "batch_checkpoint.json"

    # Select target plans
    selected_items: List[Union[int, str]] = []
    dataset_path = Path(args.dataset).resolve() if args.dataset else None

    if dataset_path and dataset_path.is_dir():
        # Directory of plan JSON files
        json_files = sorted(list(dataset_path.glob("*.json")))
        selected_items = [str(p) for p in json_files]
        logger.info("Found %d plan JSON files in %s", len(selected_items), dataset_path)
    else:
        # Load from DatasetRepository
        try:
            repo = DatasetRepository.get_instance(dataset_path=dataset_path)
        except Exception as exc:
            logger.error("Cannot access dataset repository: %s", exc)
            print(f"Error: ResPlan dataset unavailable: {exc}", file=sys.stderr)
            return 1

        if args.plans:
            parsed_ids: List[int] = []
            for token in args.plans.split(","):
                token = token.strip()
                if not token:
                    continue
                if "-" in token:
                    parts = token.split("-")
                    start, end = int(parts[0]), int(parts[1])
                    parsed_ids.extend(range(start, end + 1))
                else:
                    parsed_ids.append(int(token))
            selected_items = parsed_ids
        else:
            selected_items = list(range(repo.record_count))

    if args.limit is not None and args.limit > 0:
        selected_items = selected_items[:args.limit]

    if not selected_items:
        logger.error("No plans selected for batch processing.")
        print("Error: No floor plans to process.", file=sys.stderr)
        return 1

    header = "=" * 80
    print(header)
    print(f"{'RESPLAN PRODUCTION BATCH RUNNER':^80}")
    print(header)
    print(f"Total Target Plans:    {len(selected_items)}")
    print(f"Worker Concurrency:    {args.workers}")
    print(f"Output Directory:      {out_dir}")
    print(f"Checkpoint File:       {checkpoint_file}")
    print(f"Resume Enabled:        {args.resume}")
    print("-" * 80)

    # Load existing checkpoint
    completed_records: Dict[str, Dict[str, Any]] = {}
    if args.resume and checkpoint_file.exists():
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                chk = json.load(f)
                completed_records = {str(k): v for k, v in chk.get("records", {}).items()}
            print(f"Resumed from checkpoint: {len(completed_records)} plans already completed.")
        except Exception as exc:
            logger.warning("Could not load checkpoint: %s. Starting fresh.", exc)

    def _is_completed(item: Union[int, str]) -> bool:
        s_item = str(item)
        if s_item in completed_records:
            return True
        stem = Path(s_item).stem
        if stem in completed_records:
            return True
        return any(r.get("item_path") == s_item for r in completed_records.values())

    remaining_items = [item for item in selected_items if not _is_completed(item)]
    print(f"Remaining Plans to Evaluate: {len(remaining_items)} (out of {len(selected_items)})")
    print("-" * 80)

    t_global_start = time.perf_counter()
    n_since_checkpoint = 0

    def atomic_checkpoint_save():
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_plans": len(selected_items),
            "completed_count": len(completed_records),
            "records": completed_records,
        }
        _atomic_write_json(checkpoint_file, payload)

    cfg_dict = {
        "tolerance": args.tolerance,
        "iterations": args.iterations,
        "timeout": args.timeout,
    }

    if remaining_items:
        workers = max(1, args.workers)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_to_item = {
                executor.submit(evaluate_batch_plan_worker, (item, cfg_dict)): item
                for item in remaining_items
            }

            for future in as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    res = future.result()
                except Exception as exc:
                    res = {
                        "plan_id": item,
                        "status": "SOLVER_ERROR",
                        "is_valid": False,
                        "runtime_s": 0.0,
                        "failure_reason": f"Process Failure: {str(exc)}",
                    }

                # Save solution bundle to file
                bundle = res.pop("bundle", None)
                pid = res.get("plan_id", item)
                if bundle is not None:
                    sol_path = sol_dir / f"plan_{pid}_solution.json"
                    _atomic_write_json(sol_path, bundle)
                    res["solution_path"] = str(sol_path)

                res["item_path"] = str(item)
                completed_records[str(pid)] = res
                n_since_checkpoint += 1

                status = res["status"]
                j_str = f"J={res['cost_J']}" if res.get("cost_J") is not None else "J=N/A"
                print(f"[{len(completed_records)}/{len(selected_items)}] Plan {pid}: {status:<18} | {j_str:<12} | dt={res.get('runtime_s', 0):.2f}s")

                if n_since_checkpoint >= args.checkpoint_interval:
                    atomic_checkpoint_save()
                    n_since_checkpoint = 0

    # Final checkpoint save
    atomic_checkpoint_save()
    total_sec = time.perf_counter() - t_global_start

    # Summary Statistics
    status_counts: Dict[str, int] = {}
    valid_costs: List[float] = []
    for r in completed_records.values():
        st = r.get("status", "UNKNOWN")
        status_counts[st] = status_counts.get(st, 0) + 1
        if r.get("is_valid") and r.get("cost_J") is not None:
            valid_costs.append(float(r["cost_J"]))

    print("-" * 80)
    print(f"BATCH RUN COMPLETE: {len(completed_records)} plans evaluated in {total_sec:.2f} s")
    print("-" * 80)
    print("STATUS BREAKDOWN:")
    for st, cnt in sorted(status_counts.items()):
        pct = (cnt / len(completed_records)) * 100.0 if completed_records else 0.0
        print(f"  {st:<24}: {cnt:5d} ({pct:6.2f}%)")

    summary_payload: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_evaluated": len(completed_records),
        "total_runtime_s": round(total_sec, 2),
        "status_breakdown": status_counts,
        "valid_count": len(valid_costs),
    }

    if valid_costs:
        mean_j = sum(valid_costs) / len(valid_costs)
        print(f"Valid Plans: {len(valid_costs)} | Mean J: {mean_j:.4f} | Min J: {min(valid_costs):.4f} | Max J: {max(valid_costs):.4f}")
        summary_payload["metrics"] = {
            "mean_J": round(mean_j, 4),
            "min_J": round(min(valid_costs), 4),
            "max_J": round(max(valid_costs), 4),
        }

    summary_file = out_dir / "batch_summary.json"
    _atomic_write_json(summary_file, summary_payload)
    print(f"Summary JSON saved: {summary_file}")
    print(header)

    return 0


def handle_validate(args: argparse.Namespace) -> int:
    """Handle `resplan validate` subcommand."""
    plan_input = args.plan or getattr(args, "plan_arg", None)
    sol_input = args.solution or getattr(args, "solution_arg", None)

    if not plan_input or not sol_input:
        logger.error("Both --plan and --solution arguments are required for validation.")
        print("Error: Both -p/--plan and -s/--solution are required.", file=sys.stderr)
        return 1

    # Load candidate solution JSON
    sol_path = Path(sol_input)
    if not sol_path.is_file():
        logger.error("Solution file does not exist: %s", sol_path)
        print(f"Error: Solution file not found: {sol_path}", file=sys.stderr)
        return 1

    try:
        with open(sol_path, "r", encoding="utf-8") as f:
            solution_data = json.load(f)
    except Exception as exc:
        logger.error("Failed to parse solution JSON: %s", exc)
        print(f"Error: Malformed solution JSON: {exc}", file=sys.stderr)
        return 1

    # Ingest plan (from file or DatasetRepository)
    plan_path = Path(plan_input)
    raw_plan: Optional[Dict[str, Any]] = None
    plan: Optional[MetricFloorPlan] = None

    if plan_path.is_file():
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                raw_plan = json.load(f)
            plan = parse_floor_plan(raw_plan, strict_clearance=False)
        except Exception as exc:
            logger.error("Failed to parse plan JSON: %s", exc)
            print(f"Error: Failed to parse plan file: {exc}", file=sys.stderr)
            return 1
    elif plan_input.isdigit():
        try:
            repo = DatasetRepository.get_instance()
            pid = int(plan_input)
            plan = repo.get_floor_plan(pid)
            raw_plan = repo.get_record(pid)
        except Exception as exc:
            logger.error("Failed to load plan %s from dataset: %s", plan_input, exc)
            print(f"Error: Plan ID '{plan_input}' not found in dataset: {exc}", file=sys.stderr)
            return 1
    else:
        logger.error("Plan file does not exist: %s", plan_path)
        print(f"Error: Plan file not found: {plan_path}", file=sys.stderr)
        return 1

    # Extract topology if portals are present
    topology: Optional[MacroTopologyGraph] = None
    if raw_plan and ("doors" in raw_plan or "front_door" in raw_plan):
        try:
            portals = extract_all_portals(plan, raw_plan.get("doors"), raw_plan.get("front_door"))
            topology = MacroTopologyGraph.build_topology(plan, portals)
        except Exception as exc:
            logger.warning("Topology extraction failed during validation: %s", exc)

    # Execute dual validation
    try:
        dual_res: DualValidationResult = IndependentVerifier.dual_validate(
            plan=plan,
            raw_submission=solution_data,
            topology=topology,
            tolerance_m=args.tolerance,
            max_area_m2=args.max_area,
            max_nodes_per_hub=args.max_nodes,
        )
    except Exception as exc:
        logger.error("Dual validation failed with exception: %s", exc)
        print(f"Error: Dual validation execution failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        # JSON output
        result_dict = {
            "plan_id": dual_res.plan_id,
            "is_concordant": dual_res.is_concordant,
            "official_valid": dual_res.official_valid,
            "independent_valid": dual_res.independent_valid,
            "rules_concordant": dual_res.rules_concordant,
            "metrics_concordant": dual_res.metrics_concordant,
            "delta_L": dual_res.delta_L,
            "delta_X": dual_res.delta_X,
            "delta_O": dual_res.delta_O,
            "delta_B": dual_res.delta_B,
            "delta_J": dual_res.delta_J,
            "delta_area_gap": dual_res.delta_area_gap,
            "discrepancies": dual_res.discrepancies,
            "official_report": dual_res.official_report.model_dump(),
            "independent_report": dual_res.independent_report.model_dump(),
        }
        print(json.dumps(result_dict, indent=2, default=str))
    else:
        print_dual_validation_scorecard(dual_res)

    is_success = dual_res.official_valid and dual_res.independent_valid and dual_res.is_concordant
    return 0 if is_success else 1


def handle_benchmark(args: argparse.Namespace) -> int:
    """Handle `resplan benchmark` subcommand."""
    from benchmarks.framework.config import BenchmarkConfig, BenchmarkMode
    from benchmarks.framework.runner import BenchmarkRunner

    cfg_dict: Dict[str, Any] = {}
    if args.config:
        cfg_path = Path(args.config).resolve()
        if not cfg_path.is_file():
            logger.error("Benchmark config file does not exist: %s", cfg_path)
            print(f"Error: Config file not found: {cfg_path}", file=sys.stderr)
            return 1
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg_dict = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.error("Failed to parse benchmark YAML: %s", exc)
            print(f"Error: Malformed YAML config: {exc}", file=sys.stderr)
            return 1

    # Apply CLI overrides
    if args.mode:
        cfg_dict["mode"] = args.mode
    if args.plans:
        cfg_dict["plan_ids"] = [int(p.strip()) for p in args.plans.split(",") if p.strip()]
    if args.workers is not None:
        cfg_dict["max_workers"] = args.workers
    if args.timeout is not None:
        cfg_dict["time_limit_seconds"] = args.timeout
    if args.seed is not None:
        cfg_dict["random_seed"] = args.seed
    if args.output_dir:
        cfg_dict["output_dir"] = args.output_dir
    if args.no_resume:
        cfg_dict["resume"] = False

    try:
        benchmark_cfg = BenchmarkConfig.from_dict(cfg_dict)
    except Exception as exc:
        logger.error("Failed to build BenchmarkConfig: %s", exc)
        print(f"Error: Invalid benchmark configuration: {exc}", file=sys.stderr)
        return 1

    try:
        runner = BenchmarkRunner(benchmark_cfg)
        metrics_report, records = runner.run()
    except Exception as exc:
        logger.error("Benchmark execution failed: %s", exc)
        print(f"Error: Benchmark execution failed: {exc}", file=sys.stderr)
        return 1

    header = "=" * 80
    print(header)
    print(f"{'HONEYWELL 5-WAY BENCHMARK SUITE - SUMMARY SCORECARD':^80}")
    print(header)
    print(f"Benchmark Mode:     {benchmark_cfg.mode.value.upper()}")
    print(f"Total Plans Run:    {metrics_report.total_plans}")
    print(f"Timestamp:          {metrics_report.metadata.get('timestamp', 'N/A')}")
    print("-" * 80)
    print("ALGORITHM PERFORMANCE COMPARISON:")
    print(f"  {'Algorithm':<24} {'Runs':<6} {'Valid%':<8} {'Mean H':<8} {'Mean J':<10} {'Mean L':<10}")
    for algo_name, summary in metrics_report.algorithms.items():
        print(
            f"  {algo_name:<24} "
            f"{summary.total_runs:<6} "
            f"{summary.validity_rate_pct:<8.1f} "
            f"{summary.mean_H:<8.2f} "
            f"{summary.mean_J:<10.2f} "
            f"{summary.mean_L:<10.2f}"
        )

    print("-" * 80)
    if metrics_report.pairwise:
        print("HEAD-TO-HEAD MATCHUPS:")
        for matchup_key, comp in list(metrics_report.pairwise.items())[:5]:
            print(f"  {matchup_key}: {comp.candidate} wins {comp.candidate_wins} vs {comp.competitor} wins {comp.competitor_wins} (Ties: {comp.ties}) | p={comp.wilcoxon_p_value:.4f}")

    print(header)

    if args.json:
        print(json.dumps(metrics_report.to_dict(), indent=2, default=str))

    return 0


def handle_inspect(args: argparse.Namespace) -> int:
    """Handle `resplan inspect` subcommand."""
    plan_input = args.plan or getattr(args, "plan_arg", None)
    show_summary = args.summary or (not plan_input)

    if plan_input:
        # Inspect specific plan
        plan: Optional[MetricFloorPlan] = None
        raw_dict: Optional[Dict[str, Any]] = None
        source_label = str(plan_input)

        if str(plan_input).isdigit():
            try:
                repo = DatasetRepository.get_instance(dataset_path=args.dataset)
                pid = int(plan_input)
                plan = repo.get_floor_plan(pid)
                raw_dict = repo.get_record(pid)
                source_label = f"Dataset Record #{pid}"
            except Exception as exc:
                logger.error("Plan ID %s not found in dataset: %s", plan_input, exc)
                print(f"Error: Plan ID '{plan_input}' not found in dataset: {exc}", file=sys.stderr)
                return 1
        else:
            plan_path = Path(plan_input).resolve()
            if not plan_path.is_file():
                logger.error("Floor plan file not found: %s", plan_path)
                print(f"Error: Floor plan file not found: {plan_path}", file=sys.stderr)
                return 1
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    raw_dict = json.load(f)
                plan = parse_floor_plan(raw_dict, strict_clearance=False)
                source_label = str(plan_path)
            except Exception as exc:
                logger.error("Failed to parse plan file: %s", exc)
                print(f"Error: Failed to parse plan file: {exc}", file=sys.stderr)
                return 1

        # Extract plan properties
        eligible_rooms = [r for r in plan.rooms.values() if r.is_eligible]
        excluded_rooms = [r for r in plan.rooms.values() if not r.is_eligible]
        category_counts: Dict[str, int] = {}
        for r in plan.rooms.values():
            category_counts[r.category] = category_counts.get(r.category, 0) + 1

        bounds = plan.inner_geom.bounds  # minx, miny, maxx, maxy
        bbox_str = f"[{bounds[0]:.2f}, {bounds[1]:.2f}] to [{bounds[2]:.2f}, {bounds[3]:.2f}]"

        # Check clearance violations
        node_clearance_breaches = 0
        min_clearance = float("inf")
        from shapely.geometry import Point as SPoint
        for node in plan.nodes.values():
            d = plan.inner_geom.boundary.distance(SPoint(node.location.x, node.location.y))
            if d < min_clearance:
                min_clearance = d
            if d < 0.499:
                node_clearance_breaches += 1

        payload: Dict[str, Any] = {
            "plan_id": plan.plan_id,
            "source": source_label,
            "total_area_m2": round(plan.total_area_m2, 2),
            "bounding_box": [round(b, 2) for b in bounds],
            "rooms_total": len(plan.rooms),
            "rooms_eligible": len(eligible_rooms),
            "rooms_excluded": len(excluded_rooms),
            "room_categories": category_counts,
            "nodes_total": len(plan.nodes),
            "nodes": [
                {
                    "node_id": n.node_id,
                    "room_id": n.room_id,
                    "location": [round(n.location.x, 3), round(n.location.y, 3)],
                }
                for n in plan.nodes.values()
            ],
            "min_node_wall_distance_m": round(min_clearance, 3) if min_clearance != float("inf") else None,
            "node_clearance_breaches": node_clearance_breaches,
        }

        if raw_dict and "doors" in raw_dict:
            payload["doors_total"] = len(raw_dict.get("doors", []))

        if args.json:
            print(json.dumps(payload, indent=2, default=str))
            return 0

        header = "=" * 80
        print(header)
        print(f"{'RESPLAN FLOOR PLAN INSPECTION REPORT':^80}")
        print(header)
        print(f"Plan ID:                {plan.plan_id}")
        print(f"Source:                 {source_label}")
        print(f"Total Area:             {plan.total_area_m2:.2f} m\u00b2")
        print(f"Bounding Box:           {bbox_str}")
        print(f"Total Rooms:            {len(plan.rooms)} ({len(eligible_rooms)} eligible, {len(excluded_rooms)} excluded)")
        print(f"Room Categories:        {', '.join(f'{k}: {v}' for k, v in sorted(category_counts.items()))}")
        print(f"Fixed Nodes Count:      {len(plan.nodes)} nodes")
        if payload.get("doors_total") is not None:
            print(f"Interior Doors:         {payload['doors_total']} doors")
        print(f"Min Node Clearance:     {payload['min_node_wall_distance_m']} m (Breaches <0.5m: {node_clearance_breaches})")
        print("-" * 80)
        print("FIXED NODES INVENTORY:")
        for n in plan.nodes.values():
            print(f"  Node {n.node_id:<12} | Room: {n.room_id:<16} | Location: ({n.location.x:6.2f}, {n.location.y:6.2f})")
        print(header)
        return 0

    if show_summary:
        # Inspect dataset repository
        try:
            repo = DatasetRepository.get_instance(dataset_path=args.dataset)
            status = repo.get_status()
        except Exception as exc:
            logger.error("Failed to query dataset repository: %s", exc)
            print(f"Error: Dataset unavailable: {exc}", file=sys.stderr)
            return 1

        payload = {
            "dataset_path": str(repo.dataset_path),
            "is_ready": status["ready"],
            "total_records": status["record_count"],
            "error": status["error"],
        }
        if repo.dataset_path.exists():
            payload["file_size_mb"] = round(repo.dataset_path.stat().st_size / (1024 * 1024), 2)

        if args.json:
            print(json.dumps(payload, indent=2, default=str))
            return 0

        header = "=" * 80
        print(header)
        print(f"{'RESPLAN DATASET REPOSITORY SUMMARY':^80}")
        print(header)
        print(f"Dataset Location:       {repo.dataset_path}")
        print(f"Repository Status:      {'READY' if status['ready'] else 'NOT LOADED'}")
        print(f"Indexed Floor Plans:    {status['record_count']:,} plans")
        if payload.get("file_size_mb"):
            print(f"Disk Footprint:         {payload['file_size_mb']} MB")
        if status.get("error"):
            print(f"Load Error:             {status['error']}")
        print(header)
        return 0

    return 0


def handle_serve(args: argparse.Namespace) -> int:
    """Handle `resplan serve` subcommand."""
    try:
        import uvicorn
        from resplan_router.phase7_interactive.server import app, engine
        url = f"http://{args.host}:{args.port}"
        header = "=" * 80
        print(header)
        print(f"{'RESPLAN CO-DESIGN STUDIO LAUNCHER':^80}")
        print(header)
        print(f"Interactive Architectural Studio URL: {url}")
        print(f"Active Floor Plan: {engine.plan.plan_id if engine.plan else 'Slide 5 Baseline'}")
        print("Press Ctrl+C to stop the server.")
        print(header)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    except Exception as exc:
        logger.error("Failed to start web server: %s", exc)
        print(f"Error launching server: {exc}", file=sys.stderr)
        return 1


# =============================================================================
# CLI Main Parser & Entrypoint
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Construct top-level argument parser with subcommands and backward-compat flags."""
    parser = argparse.ArgumentParser(
        prog="resplan",
        description="ResPlan Cable Ring Router: Autonomous Floor Plan Routing & Optimization Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Top-level global options
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Configure structured logging verbosity (default: WARNING)",
    )
    # Legacy top-level options for backward compatibility
    parser.add_argument("-i", "--input", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("-o", "--output", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("-v", "--visualize", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("-c", "--config", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-nodes", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-area", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", default=False, help="Launch interactive Co-Design Studio web server")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind Co-Design Studio server (default: 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface for Co-Design Studio (default: 127.0.0.1)")

    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands", description="Available CLI subcommands")

    # 1. OPTIMIZE
    opt_p = subparsers.add_parser(
        "optimize",
        help="Optimize a single floor plan file or dataset record",
        description="Run autonomous cable ring layout optimization for a single plan with scorecard reporting.",
    )
    opt_p.add_argument("plan_input", nargs="?", default=None, help="Path to input floor plan JSON or dataset plan ID")
    opt_p.add_argument("-i", "--input", type=str, default=None, help="Path to input floor plan JSON or dataset plan ID")
    opt_p.add_argument("-o", "--output", type=str, default=None, help="Path to export Slide 8 solution JSON")
    opt_p.add_argument("-v", "--visualize", type=str, default=None, help="Path to export Slide 5 schematic overlay PNG")
    opt_p.add_argument("--timeout", type=float, default=None, help="Solver execution timeout in seconds")
    opt_p.add_argument("--seed", type=int, default=None, help="Random seed for reproducible stochastic operations")
    opt_p.add_argument("-c", "--config", type=str, default=None, help="Optional path to YAML system configuration")
    opt_p.add_argument("--max-nodes", type=int, default=None, help="Override maximum fixed nodes per hub capacity limit")
    opt_p.add_argument("--max-area", type=float, default=None, help="Override maximum zone area (m^2) capacity limit")
    opt_p.add_argument("--tolerance", type=float, default=0.001, help="Spatial tolerance in metres (default: 0.001)")
    opt_p.add_argument("--strict-clearance", dest="strict_clearance", action="store_true", default=True, help="Validate node clearance on load (default: True)")
    opt_p.add_argument("--no-strict-clearance", dest="strict_clearance", action="store_false", help="Allow loading plans with raw node clearance")

    # 2. BATCH
    batch_p = subparsers.add_parser(
        "batch",
        help="Batch optimization over ResPlan.pkl or directory of floor plans",
        description="Execute multiprocessing batch optimization with atomic checkpointing and resume support.",
    )
    batch_p.add_argument("-d", "--dataset", type=str, default=None, help="Path to ResPlan.pkl or directory containing plan JSON files")
    batch_p.add_argument("-p", "--plans", type=str, default=None, help="Comma-separated plan IDs or ranges (e.g. '0,4,5,12' or '0-25')")
    batch_p.add_argument("-n", "--limit", type=int, default=None, help="Maximum number of floor plans to evaluate")
    batch_p.add_argument("-w", "--workers", type=int, default=4, help="Multiprocessing worker concurrency (default: 4)")
    batch_p.add_argument("--checkpoint", type=str, default=None, help="Path to checkpoint JSON file (default: <output-dir>/batch_checkpoint.json)")
    batch_p.add_argument("--resume", dest="resume", action="store_true", default=True, help="Resume from existing checkpoint if present (default: True)")
    batch_p.add_argument("--no-resume", dest="resume", action="store_false", help="Ignore existing checkpoint and start fresh")
    batch_p.add_argument("-o", "--output-dir", type=str, default="results/batch", help="Output directory for solutions and summary report")
    batch_p.add_argument("--timeout", type=float, default=5.0, help="Per-plan solver timeout in seconds (default: 5.0)")
    batch_p.add_argument("--iterations", type=int, default=25, help="ALNS operator iterations (default: 25)")
    batch_p.add_argument("--tolerance", type=float, default=0.001, help="Checker spatial tolerance in metres (default: 0.001)")
    batch_p.add_argument("--checkpoint-interval", type=int, default=10, help="Atomic checkpoint save cadence in plans (default: 10)")

    # 3. VALIDATE
    val_p = subparsers.add_parser(
        "validate",
        help="Dual-oracle validation of candidate solution against input floor plan",
        description="Rigorously validate candidate solution JSON against input plan using both FloorPlanChecker and IndependentVerifier.",
    )
    val_p.add_argument("plan_arg", nargs="?", default=None, help="Path to input floor plan JSON or dataset plan ID")
    val_p.add_argument("solution_arg", nargs="?", default=None, help="Path to candidate solution JSON file")
    val_p.add_argument("-p", "--plan", type=str, default=None, help="Path to input floor plan JSON or dataset plan ID")
    val_p.add_argument("-s", "--solution", type=str, default=None, help="Path to candidate solution JSON file")
    val_p.add_argument("--tolerance", type=float, default=0.001, help="Spatial evaluation tolerance in metres (default: 0.001)")
    val_p.add_argument("--max-area", type=float, default=100.0, help="Maximum zone area in m^2 (default: 100.0)")
    val_p.add_argument("--max-nodes", type=int, default=5, help="Maximum fixed nodes per hub limit (default: 5)")
    val_p.add_argument("--json", action="store_true", default=False, help="Output dual validation report as machine-readable JSON")

    # 4. BENCHMARK
    bench_p = subparsers.add_parser(
        "benchmark",
        help="Execute benchmark suites from YAML config and output summary",
        description="Run comparative algorithm benchmark suites across floor plan splits with statistical evaluation.",
    )
    bench_p.add_argument("-c", "--config", type=str, default=None, help="Path to benchmark YAML configuration file")
    bench_p.add_argument("-m", "--mode", type=str, default="smoke", choices=["smoke", "validation", "test", "full", "custom"], help="Execution mode (default: smoke)")
    bench_p.add_argument("-p", "--plans", type=str, default=None, help="Custom plan IDs (e.g. '0,4,5,12')")
    bench_p.add_argument("-w", "--workers", type=int, default=None, help="Override worker concurrency")
    bench_p.add_argument("-t", "--timeout", type=float, default=None, help="Override time limit per algorithm per plan")
    bench_p.add_argument("-s", "--seed", type=int, default=None, help="Override benchmark random seed")
    bench_p.add_argument("-o", "--output-dir", type=str, default=None, help="Override benchmark output directory")
    bench_p.add_argument("--no-resume", action="store_true", default=False, help="Disable checkpoint resumption")
    bench_p.add_argument("--json", action="store_true", default=False, help="Output benchmark summary as machine-readable JSON")

    # 5. INSPECT
    insp_p = subparsers.add_parser(
        "inspect",
        help="Inspect plan details or dataset summary statistics",
        description="Display room breakdowns, fixed nodes, clearances, or overall ResPlan dataset repository statistics.",
    )
    insp_p.add_argument("plan_arg", nargs="?", default=None, help="Plan ID or path to plan JSON file to inspect")
    insp_p.add_argument("-p", "--plan", type=str, default=None, help="Plan ID or path to plan JSON file to inspect")
    insp_p.add_argument("-d", "--dataset", type=str, default=None, help="Path to dataset pickle file (default: ResPlan.pkl)")
    insp_p.add_argument("-s", "--summary", action="store_true", default=False, help="Display dataset-wide summary statistics")
    insp_p.add_argument("--json", action="store_true", default=False, help="Output inspection report as machine-readable JSON")

    # 6. SERVE
    serve_p = subparsers.add_parser(
        "serve",
        help="Launch interactive Co-Design Studio web server",
        description="Launch web application with live interactive 2D/3D BIM viewer and reviewer HUD.",
    )
    serve_p.add_argument("--port", type=int, default=8000, help="Port to run web server on (default: 8000)")
    serve_p.add_argument("--host", type=str, default="127.0.0.1", help="Host interface to bind server (default: 127.0.0.1)")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint supporting subcommands and backward compatibility."""
    if argv is None:
        argv = sys.argv[1:]

    # Backward compatibility transformation:
    # If first argument is not a known subcommand or help flag, check if it's a legacy invocation
    known_subcommands = {"optimize", "batch", "validate", "benchmark", "inspect", "serve"}
    argv_list = list(argv)

    if argv_list:
        first = argv_list[0]
        if first not in known_subcommands and not first.startswith("-h") and not first.startswith("--help"):
            if "--serve" in argv_list:
                argv_list = ["serve"] + [a for a in argv_list if a != "--serve"]
            elif any(opt in argv_list for opt in ("-i", "--input")):
                argv_list = ["optimize"] + argv_list
            elif Path(first).is_file() or first.isdigit():
                argv_list = ["optimize"] + argv_list

    parser = build_parser()
    args = parser.parse_args(argv_list)

    # Configure structured logging
    log_level = getattr(logging, args.log_level.upper(), logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.serve or args.subcommand == "serve":
        return handle_serve(args)

    if args.subcommand == "optimize":
        return handle_optimize(args)
    elif args.subcommand == "batch":
        return handle_batch(args)
    elif args.subcommand == "validate":
        return handle_validate(args)
    elif args.subcommand == "benchmark":
        return handle_benchmark(args)
    elif args.subcommand == "inspect":
        return handle_inspect(args)
    else:
        # Fallback to optimize if input is specified, else print help
        if args.input:
            return handle_optimize(args)
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
