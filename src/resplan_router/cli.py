"""Command-line interface (CLI) for the ResPlan Cable Ring Router.

Provides command-line routing orchestration, JSON export, schematic visualization,
and reviewer scorecard reporting.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
from typing import Any, Optional

from resplan_router.config import SystemConfig
from resplan_router.phase6_output.pipeline import route_floor_plan


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
    print(f"{'RESPLAN CABLE RING ROUTER — REVIEWER SCORECARD':^80}")
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
    print(f"  Area Gap (ΔA_gap):       {evaluation.area_gap_m2:.2f} m²")
    print(f"  Total Cost J (Official): {evaluation.total_cost_J:.2f}")
    print(sub)
    print("HARD VALIDITY RULES (Slide 3 Checklist):")
    rule_descriptions = {
        "Rule 1": "Full Coverage (all eligible rooms & nodes served)",
        "Rule 2": "Capacity Limits (<= 100 m² & <= 5 nodes per hub)",
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
            loc_str = f" at ({v.location.x:.2f}, {v.location.y:.2f})" if v.location else ""
            print(f"  - [Rule {v.rule_number}] {v.rule_name}{loc_str}: {v.message}")

    print(header)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="resplan-router",
        description="ResPlan Cable Ring Router: Autonomous Floor Plan Routing Pipeline",
    )
    parser.add_argument(
        "-i",
        "--input",
        required=False,
        default=None,
        type=str,
        help="Path to input floor plan JSON file",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Launch interactive Co-Design Studio web server with live reviewer HUD",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the Co-Design Studio server on (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host interface to bind the Co-Design Studio server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Path to export Slide 8 solution JSON file",
    )
    parser.add_argument(
        "-v",
        "--visualize",
        type=str,
        default=None,
        help="Path to render Slide 5 schematic overlay PNG",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Optional path to YAML configuration file",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=None,
        help="Override maximum fixed nodes per hub capacity limit",
    )
    parser.add_argument(
        "--max-area",
        type=float,
        default=None,
        help="Override maximum zone area (m^2) capacity limit",
    )

    args = parser.parse_args(argv)

    if args.serve:
        import uvicorn
        from resplan_router.phase7_interactive.server import app, engine
        url = f"http://{args.host}:{args.port}"
        print("=" * 80)
        print(f"{'RESPLAN CO-DESIGN STUDIO LAUNCHER':^80}")
        print("=" * 80)
        print(f"Interactive Architectural Studio URL: {url}")
        print(f"Active Floor Plan: {engine.plan.plan_id if engine.plan else 'Slide 5 Baseline'}")
        print("Press Ctrl+C to stop the server.")
        print("=" * 80)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if not args.input:
        parser.error("the following arguments are required: -i/--input (unless --serve is specified)")

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: Input file does not exist: {input_path}", file=sys.stderr)
        return 1

    # Load configuration
    if args.config:
        cfg = SystemConfig.from_yaml(args.config)
    else:
        cfg = SystemConfig()

    # Apply overrides
    overrides = {}
    if args.max_nodes is not None:
        overrides["max_nodes_per_hub"] = args.max_nodes
    if args.max_area is not None:
        overrides["max_area_m2"] = args.max_area
    if overrides:
        cfg = cfg.with_overrides(**overrides)

    t0 = time.perf_counter()
    try:
        exported_json, evaluation, bundle = route_floor_plan(
            raw_plan_dict_or_path=input_path,
            config=cfg,
            output_json_path=args.output,
            render_png_path=args.visualize,
        )
    except Exception as exc:
        print(f"Error during route execution: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - t0

    plan_id = exported_json.get("plan_id", input_path.stem)
    print_scorecard(
        plan_id=plan_id,
        evaluation=evaluation,
        elapsed_sec=elapsed,
        output_json=args.output,
        render_png=args.visualize,
    )

    return 0 if evaluation.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
