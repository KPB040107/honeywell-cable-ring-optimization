"""Top-Level CLI Execution Suite for Honeywell 5-Way Benchmarking.

Provides reproducible, parallelized benchmark execution across:
1. PortfolioHybrid
2. Standalone ALNS (Adaptive Bandit)
3. Random ALNS
4. LinUCB-ALNS
5. Trained PPO-ALNS

Modes:
  --smoke      : Fast sanity smoke test (25 clean plans)
  --validation : Rigorous statistical validation benchmark (100 clean plans)
  --full       : Comprehensive evaluation on all available held-out clean plans
  --plans      : Custom explicit plan count or comma-delimited ID list (e.g. "0,4,5,12")

Features:
  - ProcessPoolExecutor parallel worker pool
  - Incremental checkpointing to disk (progress is never lost)
  - Automatic resumption of interrupted runs
  - Auto-generated CSV, JSON, and GitHub-flavored Markdown reports

Usage Examples:
  python benchmarks/benchmark_suite.py --smoke --workers 4
  python benchmarks/benchmark_suite.py --validation --workers 6 --time-limit 5.0
  python benchmarks/benchmark_suite.py --plans 0,4,5,12 --workers 2 --iterations 10
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from benchmarks.framework.config import (
    AlgorithmConfig,
    AlgorithmId,
    BenchmarkConfig,
    BenchmarkMode,
)
from benchmarks.framework.runner import BenchmarkRunner


def build_arg_parser() -> argparse.ArgumentParser:
    """Build comprehensive CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Honeywell Cable Ring Optimization 5-Way Benchmark Suite",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--smoke",
        action="store_true",
        help="Run fast smoke benchmark (25 floor plans, default 10 iterations)",
    )
    mode_group.add_argument(
        "--validation",
        action="store_true",
        help="Run full validation benchmark (100 floor plans, default 25 iterations)",
    )
    mode_group.add_argument(
        "--test",
        action="store_true",
        help="Run rigorous held-out test split benchmark (1,632 floor plans from split.json)",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Run comprehensive benchmark on all 17,000 floor plans in ResPlan dataset",
    )

    parser.add_argument(
        "--plans",
        type=str,
        default=None,
        help="Custom plan selection: integer count (e.g. '30') or comma-separated IDs (e.g. '0,4,5,12')",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, max(1, (os.cpu_count() or 4) - 1)),
        help="Number of concurrent worker processes for ProcessPoolExecutor",
    )

    parser.add_argument(
        "--time-limit",
        type=float,
        default=5.0,
        help="Time limit in seconds per floor plan evaluation",
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="ALNS iteration budget per plan (defaults to 10 for smoke, 25 for validation)",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Master random seed for reproducibility",
    )

    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=1,
        help="Frequency (in plans) to persist incremental checkpoints to disk",
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Disable checkpoint resumption and restart benchmark from scratch",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/benchmarks",
        help="Output directory for CSV, JSON, and checkpoint artifacts",
    )

    parser.add_argument(
        "--report-file",
        type=str,
        default=None,
        help="Path for generated Markdown summary report",
    )

    parser.add_argument(
        "--ppo-checkpoint",
        type=str,
        default="checkpoints/ppo_alns/best_model.pt",
        help="Path to trained PPO policy weights",
    )

    parser.add_argument(
        "--algorithms",
        type=str,
        default=None,
        help="Comma-separated subset of algorithms to run (e.g. 'PortfolioHybrid,ALNS_Bandit,PPO_ALNS')",
    )

    return parser


def parse_plan_selection(plans_arg: Optional[str]) -> List[int]:
    """Parse integer count or comma-delimited plan IDs from CLI argument."""
    if not plans_arg:
        return []
    plans_arg = plans_arg.strip()
    if "," in plans_arg:
        return [int(x.strip()) for x in plans_arg.split(",") if x.strip()]
    try:
        count = int(plans_arg)
        from rl.dataset import ResPlanDatasetManager
        mgr = ResPlanDatasetManager()
        return mgr.clean_val_ids[:count] if mgr.clean_val_ids else list(range(count))
    except ValueError:
        return [int(x) for x in plans_arg.split() if x.isdigit()]


def main() -> int:
    """CLI Entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # Determine execution mode
    if args.smoke:
        mode = BenchmarkMode.SMOKE
        default_iterations = 10
    elif args.validation:
        mode = BenchmarkMode.VALIDATION
        default_iterations = 25
    elif args.test:
        mode = BenchmarkMode.TEST
        default_iterations = 25
    elif args.full:
        mode = BenchmarkMode.FULL
        default_iterations = 25
    elif args.plans:
        mode = BenchmarkMode.CUSTOM
        default_iterations = 25
    else:
        # Default to smoke mode if no mode specified
        mode = BenchmarkMode.SMOKE
        default_iterations = 10

    iterations = args.iterations if args.iterations is not None else default_iterations
    custom_plan_ids = parse_plan_selection(args.plans)

    # Initialize configuration
    cfg = BenchmarkConfig(
        mode=mode,
        plan_ids=custom_plan_ids,
        time_limit_seconds=args.time_limit,
        max_iterations=iterations,
        random_seed=args.seed,
        max_workers=args.workers,
        output_dir=Path(args.output_dir),
        checkpoint_interval=args.checkpoint_interval,
        resume=not args.no_resume,
        ppo_checkpoint_path=args.ppo_checkpoint,
    )

    # Filter algorithms if specified
    if args.algorithms:
        selected_names = set(x.strip() for x in args.algorithms.split(","))
        cfg.algorithms = [
            algo for algo in cfg.algorithms
            if algo.algo_id in selected_names or algo.display_name in selected_names
        ]

    if args.report_file:
        cfg.report_md_file = Path(args.report_file)

    runner = BenchmarkRunner(config=cfg)
    report, records = runner.run()

    print(f"\n[Success] Benchmark completed successfully for {len(records)} plans.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
