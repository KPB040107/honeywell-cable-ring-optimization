"""Benchmark Orchestrator and Paired Evaluation Runner (Agent 5).

Executes matched-budget paired evaluations of research candidates against the frozen
LinUCB-ALNS champion baseline across the independent experimental partition.

Features:
- Incremental disk checkpointing (resumable runs).
- Multi-tier stratification (Compact, Standard, Complex).
- Authoritative evaluation with FloorPlanChecker at 1 mm tolerance.
- Comprehensive paired statistics (Wilcoxon signed-rank, 95% bootstrap CIs).
- Direct export of structured JSON artifacts and markdown tables.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import datetime
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.experimental.candidates import (
    BaseFloorPlanOptimizer,
    get_candidate_registry,
)
from benchmarks.experimental.evaluator import evaluate_paired_plan, evaluate_single_optimizer
from benchmarks.experimental.partition import (
    ExperimentalPartition,
    build_experimental_partition,
)
from benchmarks.experimental.protocol import (
    MatchedBudgetProtocol,
    PairedPlanComparison,
    build_paired_comparison,
)
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from benchmarks.experimental.statistical_tests import (
    StratumEvaluationSummary,
    analyze_paired_comparisons,
)

logger = logging.getLogger("benchmarks.experimental.runner")


class ExperimentalBenchmarkRunner:
    """Orchestrates matched-budget paired benchmark evaluations."""

    def __init__(
        self,
        partition_path: Path | str = "results/benchmarks/experimental/experimental_val_500.json",
        output_dir: Path | str = "results/benchmarks/experimental",
        max_iterations: int = 15,
        time_limit_seconds: float = 5.0,
        base_seed: int = 42,
    ) -> None:
        self.partition_path = Path(partition_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.protocol = MatchedBudgetProtocol(
            max_iterations=max_iterations,
            time_limit_seconds=time_limit_seconds,
            base_random_seed=base_seed,
        )

        if not self.partition_path.exists():
            logger.info("Partition not found at %s. Building new independent partition...", self.partition_path)
            self.partition = build_experimental_partition()
        else:
            self.partition = ExperimentalPartition.load(self.partition_path)

        self.registry = get_candidate_registry(max_iterations=max_iterations)
        self.champion = self.registry["LinUCB_ALNS"]
        self._champion_cache: Dict[int, Any] = {}

    def run_candidate_benchmark(
        self,
        candidate_name: str,
        plan_ids: Optional[Sequence[int]] = None,
        max_plans: Optional[int] = None,
    ) -> Tuple[List[PairedPlanComparison], Dict[str, StratumEvaluationSummary]]:
        """Run matched paired comparison between Champion and specified Candidate."""
        if candidate_name not in self.registry:
            raise KeyError(f"Candidate '{candidate_name}' not in registry: {list(self.registry.keys())}")

        cand_opt = self.registry[candidate_name]
        champ_opt = self.champion

        # Determine target plans
        if plan_ids is None:
            target_pids = list(self.partition.plan_ids)
        else:
            target_pids = list(plan_ids)

        if max_plans is not None and max_plans < len(target_pids):
            # Select balanced subset across tiers
            compact_pids = [p for p in target_pids if self.partition.records[p].complexity_tier == "compact"]
            standard_pids = [p for p in target_pids if self.partition.records[p].complexity_tier == "standard"]
            complex_pids = [p for p in target_pids if self.partition.records[p].complexity_tier == "complex"]

            ratio_c = max(1, int(round(max_plans * 0.20)))
            ratio_s = max(1, int(round(max_plans * 0.60)))
            ratio_x = max(1, max_plans - ratio_c - ratio_s)

            selected = (
                compact_pids[:ratio_c]
                + standard_pids[:ratio_s]
                + complex_pids[:ratio_x]
            )
            target_pids = sorted(selected)

        logger.info(
            "Starting paired evaluation: Champion (LinUCB-ALNS) vs %s on %d plans",
            candidate_name,
            len(target_pids),
        )

        checkpoint_file = self.output_dir / f"checkpoint_paired_{candidate_name}.json"
        comparisons: List[PairedPlanComparison] = []
        completed_pids: Set[int] = set()

        # Load existing checkpoint if available
        if checkpoint_file.exists():
            try:
                with open(checkpoint_file, "r", encoding="utf-8") as f:
                    ckpt_data = json.load(f)
                for item in ckpt_data.get("comparisons", []):
                    c = PairedPlanComparison(
                        plan_id=item["plan_id"],
                        complexity_tier=item["complexity_tier"],
                        champion=PlanRunMetrics(**item["champion"]) if isinstance(item["champion"], dict) else item["champion"],
                        candidate=PlanRunMetrics(**item["candidate"]) if isinstance(item["candidate"], dict) else item["candidate"],
                        delta_h=item["delta_h"],
                        delta_j=item["delta_j"],
                        pct_j_improvement=item["pct_j_improvement"],
                        runtime_speedup=item["runtime_speedup"],
                        outcome=MatchupOutcome(item["outcome"]) if isinstance(item["outcome"], str) else item["outcome"],
                        both_valid=item["both_valid"],
                        feasibility_maintained=item["feasibility_maintained"],
                    )
                    comparisons.append(c)
                    completed_pids.add(item["plan_id"])
                logger.info("Resumed from checkpoint: %d completed plans", len(completed_pids))
            except Exception as exc:
                logger.warning("Could not read checkpoint: %s", exc)

        t_start = time.perf_counter()
        for idx, pid in enumerate(target_pids):
            if pid in completed_pids:
                continue

            tier = self.partition.records[pid].complexity_tier if pid in self.partition.records else "standard"
            t0 = time.perf_counter()
            repo = DatasetRepository.get_instance()
            plan = repo.get_floor_plan(pid)

            if pid not in self._champion_cache:
                self._champion_cache[pid] = evaluate_single_optimizer(champ_opt, plan, pid, self.protocol)
            champ_metrics = self._champion_cache[pid]

            cand_metrics = evaluate_single_optimizer(cand_opt, plan, pid, self.protocol)
            comp = build_paired_comparison(
                plan_id=pid,
                complexity_tier=tier,
                champ_metrics=champ_metrics,
                cand_metrics=cand_metrics,
                cost_tie_tolerance=self.protocol.cost_tie_tolerance,
            )
            comparisons.append(comp)
            completed_pids.add(pid)
            elapsed = time.perf_counter() - t0

            logger.info(
                "[%d/%d] Plan %d (%s): Outcome=%s | Delta_J=%.2f (%.2f%%) | Speedup=%.2fx | Time=%.2fs",
                idx + 1,
                len(target_pids),
                pid,
                tier,
                comp.outcome.value,
                comp.delta_j,
                comp.pct_j_improvement,
                comp.runtime_speedup,
                elapsed,
            )

            # Checkpoint every 5 plans
            if len(comparisons) % 5 == 0:
                with open(checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {"candidate": candidate_name, "comparisons": [c.to_dict() for c in comparisons]},
                        f,
                        indent=2,
                    )

        total_wall_time = time.perf_counter() - t_start

        # Final save of paired comparisons
        final_file = self.output_dir / f"paired_evaluations_{candidate_name}.json"
        with open(final_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "candidate": candidate_name,
                    "total_plans": len(comparisons),
                    "total_wall_time_s": round(total_wall_time, 2),
                    "protocol": self.protocol.to_dict(),
                    "comparisons": [c.to_dict() for c in comparisons],
                },
                f,
                indent=2,
            )

        # Statistical Analysis (Overall + Stratified)
        strata_summaries: Dict[str, StratumEvaluationSummary] = {}
        strata_summaries["overall"] = analyze_paired_comparisons(comparisons, tier_name="overall")

        for tier in ["compact", "standard", "complex"]:
            tier_comps = [c for c in comparisons if c.complexity_tier == tier]
            strata_summaries[tier] = analyze_paired_comparisons(tier_comps, tier_name=tier)

        summary_file = self.output_dir / f"statistical_summary_{candidate_name}.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(
                {k: v.to_dict() for k, v in strata_summaries.items()},
                f,
                indent=2,
            )

        logger.info(
            "Evaluation complete for %s. Win rate: %.2f%%, Non-loss rate: %.2f%%, Mean Delta J: %.2f",
            candidate_name,
            strata_summaries["overall"].win_rate_pct,
            strata_summaries["overall"].non_loss_rate_pct,
            strata_summaries["overall"].mean_delta_j,
        )

        return comparisons, strata_summaries


def run_post_champion_evaluations(
    max_plans: int = 50,
    max_iterations: int = 15,
) -> Dict[str, Dict[str, Any]]:
    """Execute complete post-champion comparative evaluation suite across all candidates."""
    runner = ExperimentalBenchmarkRunner(
        max_iterations=max_iterations,
        time_limit_seconds=5.0,
    )

    candidates_to_test = [
        "Agent2_OptimalityBounds",
        "Agent3_AdvancedOperators",
        "Agent4_PerformanceSpatial",
        "Integrated_PostChampion",
    ]

    master_results: Dict[str, Dict[str, Any]] = {}

    for cand_name in candidates_to_test:
        _, strata = runner.run_candidate_benchmark(
            candidate_name=cand_name,
            max_plans=max_plans,
        )
        master_results[cand_name] = {k: v.to_dict() for k, v in strata.items()}

    master_summary_file = Path("results/benchmarks/experimental/master_evaluation_summary.json")
    master_summary_file.parent.mkdir(parents=True, exist_ok=True)
    with open(master_summary_file, "w", encoding="utf-8") as f:
        json.dump(master_results, f, indent=2)

    try:
        from benchmarks.experimental.export_report import generate_markdown_report
        generate_markdown_report(
            summary_path=master_summary_file,
            partition_path=runner.partition_path,
            output_report_path="reports/post_champion_agent5_evaluation.md",
        )
        logger.info("Automatically generated reports/post_champion_agent5_evaluation.md")
    except Exception as exc:
        logger.warning("Could not auto-generate markdown report: %s", exc)

    return master_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Post-Champion Experimental Benchmark Runner")
    parser.add_argument("--plans", type=int, default=50, help="Number of plans to evaluate (default 50)")
    parser.add_argument("--iterations", type=int, default=15, help="ALNS exploration iterations (default 15)")
    args = parser.parse_args()

    print(f"Executing post-champion benchmark suite ({args.plans} plans, {args.iterations} iterations)...")
    res = run_post_champion_evaluations(max_plans=args.plans, max_iterations=args.iterations)
    print("Master benchmark evaluation completed successfully.")
