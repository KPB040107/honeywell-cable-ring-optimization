"""Quickstart: Batch Floor Plan Optimization.

Demonstrates concurrent batch optimization, checkpointing, and summary reporting
across multiple residential floor plans using the official Honeywell Cable Ring Router
public API (`optimize_batch`).

Usage:
    python examples/quickstart_batch.py
    python examples/quickstart_batch.py --workers 2 --algorithm production
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import List

# UTF-8 stdout protection
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

# Ensure repository root and src are on path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from resplan_router import BatchResult, OptimizationConfig, OptimizationResult, optimize_batch


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ResPlan Cable Ring Router - Batch Processing Example",
    )
    parser.add_argument(
        "-d", "--input-dir",
        type=str,
        default=None,
        help="Directory containing floor plan JSON files (default: sample fixtures)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default="output/batch_quickstart",
        help="Directory to save batch results and checkpoints (default: output/batch_quickstart)",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=2,
        help="Number of concurrent worker threads (default: 2)",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        default="production",
        choices=["production", "v2", "v1", "pipeline", "greedy"],
        help="Optimizer algorithm selection (default: production)",
    )
    args = parser.parse_args()

    # Collect plan files
    if args.input_dir:
        dir_path = Path(args.input_dir)
        plan_files = sorted(list(dir_path.glob("*.json")))
    else:
        # Default collection of verified fixtures
        plan_files = [
            REPO_ROOT / "examples" / "sample_floor_plan.json",
            REPO_ROOT / "tests" / "fixtures" / "new_four_room_plan.json",
            REPO_ROOT / "tests" / "fixtures" / "slide5_schematic.json",
        ]
        plan_files = [p for p in plan_files if p.is_file()]

    if not plan_files:
        print("Error: No floor plan JSON files found to process.", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = out_dir / "checkpoint.json"

    print("=" * 80)
    print("HONEYWELL CABLE RING ROUTER - BATCH OPTIMIZATION PIPELINE")
    print("=" * 80)
    print(f"Total plans queued: {len(plan_files)}")
    print(f"Worker concurrency: {args.workers}")
    print(f"Selected engine:    {args.algorithm}")
    print(f"Output directory:   {out_dir.resolve()}")
    print("-" * 80)

    config = OptimizationConfig(
        algorithm=args.algorithm,
        time_limit_seconds=10.0,
        export_slide8=True,
    )

    t0 = time.perf_counter()
    # Execute batch optimization via public API
    batch_res: BatchResult = optimize_batch(
        plans=plan_files,
        config=config,
        max_workers=args.workers,
        checkpoint_path=checkpoint_file,
    )
    wall_clock = time.perf_counter() - t0

    # Print summary table
    print(f"{'Plan ID':<30} | {'Status':<7} | {'Hubs':<4} | {'Length':<8} | {'Cost J':<8} | {'Time (s)':<8}")
    print("-" * 80)

    for r in batch_res.results:
        val_status = "PASS" if r.is_valid else "FAIL"
        print(
            f"{r.plan_id[:30]:<30} | {val_status:<7} | {r.hub_count:<4} | "
            f"{r.total_length_m:<8.2f} | {r.total_cost_J:<8.2f} | {r.runtime_s:<8.3f}"
        )
        # Export individual Slide 8 JSON solution if valid
        if r.is_valid and r.slide8_dict:
            sol_path = out_dir / f"{r.plan_id}_solution.json"
            r.save_slide8(sol_path)

    print("-" * 80)
    valid_pct = (batch_res.successful / batch_res.total_plans * 100) if batch_res.total_plans > 0 else 0.0
    mean_cost = (batch_res.total_cost_J / batch_res.successful) if batch_res.successful > 0 else 0.0
    print(
        f"Batch Summary: {batch_res.successful}/{batch_res.total_plans} valid ({valid_pct:.1f}%) | "
        f"Mean Cost J: {mean_cost:.2f} | Wall Clock: {wall_clock:.2f}s"
    )

    # Save summary report JSON
    summary_path = out_dir / "batch_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(batch_res.to_json(indent=2))
    print(f"Summary manifest saved to: {summary_path.resolve()}")
    print("=" * 80)

    return 0 if batch_res.successful == batch_res.total_plans else 1


if __name__ == "__main__":
    sys.exit(main())
