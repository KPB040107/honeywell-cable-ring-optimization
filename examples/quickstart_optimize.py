"""Quickstart: Single Floor Plan Optimization.

Demonstrates end-to-end cable ring routing and hub placement for a single residential
floor plan using the official Honeywell Cable Ring Router public API (`optimize_plan`).

Usage:
    python examples/quickstart_optimize.py
    python examples/quickstart_optimize.py --input examples/sample_floor_plan.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional, Union

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

from resplan_router import OptimizationConfig, OptimizationResult, SystemConfig, optimize_plan, validate_solution


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ResPlan Cable Ring Router - Single Plan Optimization Example",
    )
    default_input = REPO_ROOT / "examples" / "sample_floor_plan.json"
    parser.add_argument(
        "-i", "--input",
        type=str,
        default=str(default_input),
        help=f"Path to input floor plan JSON (default: {default_input.name})",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="output/quickstart_solution.json",
        help="Path to export Slide 8 solution JSON (default: output/quickstart_solution.json)",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        default="production",
        choices=["production", "v2", "v1", "pipeline", "greedy"],
        help="Optimizer algorithm selection (default: production)",
    )
    args = parser.parse_args()

    input_file = Path(args.input)
    if not input_file.is_file():
        print(f"Error: Floor plan file not found at {input_file}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("HONEYWELL CABLE RING ROUTER - SINGLE PLAN OPTIMIZATION")
    print("=" * 70)
    print(f"Loading input:     {input_file.resolve()}")
    print(f"Selected engine:   {args.algorithm}")

    config = OptimizationConfig(
        algorithm=args.algorithm,
        time_limit_seconds=10.0,
        export_slide8=True,
    )

    t0 = time.perf_counter()
    # Execute single-plan optimization via public API
    result: OptimizationResult = optimize_plan(input_file, config=config)
    elapsed = time.perf_counter() - t0

    print(f"Optimization completed in {elapsed:.3f} seconds.")
    print("-" * 70)
    print(f"Plan ID:                {result.plan_id}")
    print(f"Solver Status:          {result.status.value}")
    print(f"Validity:               {'PASSED (100% compliant)' if result.is_valid else 'FAILED'}")
    print(f"Algorithm:              {result.algorithm}")
    print(f"Hubs Placed (H):        {result.hub_count}")
    print(f"Total Cable Length (L): {result.total_length_m:.2f} m")
    print(f"90° Bends (B):          {result.total_bends}")
    print(f"Crossings (X):          {result.total_crossings}")
    print(f"Overlap (O):            {result.total_overlap_m:.2f} m")
    print(f"Area Gap:               {result.area_gap_m2:.2f} m²")
    print(f"Official Cost J:        {result.total_cost_J:.2f}")
    print("-" * 70)

    print("Rule Compliance Breakdown:")
    for rule_id in sorted(result.rules_passed.keys()):
        passed = result.rules_passed[rule_id]
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status} {rule_id}")

    if not result.is_valid:
        print("\nRule Violations:")
        for v in result.violations:
            print(f"  - {v}")
        return 1

    # Save Slide 8 JSON submission
    out_path = Path(args.output)
    result.save_slide8(out_path)
    print(f"\nSolution JSON successfully exported to: {out_path.resolve()}")

    # Perform independent oracle audit
    val = validate_solution(input_file, result)
    print(f"Independent Oracle Audit: {'VERIFIED' if val.is_valid else 'AUDIT_FAILED'} ({val.message})")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
