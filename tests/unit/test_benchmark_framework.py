"""Unit test suite for Honeywell 5-Way Benchmark Framework.

Tests:
- Configuration dataclasses and mode resolutions.
- Complexity tier assignment (compact, standard, complex).
- Lexicographical dominance evaluation (validity, H minimization, J minimization).
- Paired metrics calculation on synthetic test records.
- Statistical significance tests (paired Student's t-test and Wilcoxon signed-rank test).
- Non-parametric empirical bootstrap confidence interval calculation.
- Fallback LinUCB optimizer instantiation and context extraction.
- Reporter artifact persistence (Wide CSV, Tall CSV, Summary JSON, Markdown).
- End-to-end runner execution and checkpoint resumption on clean floor plans.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
import pytest

from benchmarks.framework.config import (
    AlgorithmConfig,
    AlgorithmId,
    BenchmarkConfig,
    BenchmarkMode,
    ComplexityTier,
)
from benchmarks.framework.metrics import (
    AlgorithmRunResult,
    AlgorithmSummary,
    BenchmarkMetricsReport,
    PairedComparisonResult,
    PlanBenchmarkRecord,
    TierSummary,
    bootstrap_confidence_interval,
    compute_algorithm_summary,
    compute_paired_comparison,
    compute_stratified_tier_metrics,
    evaluate_lexicographical_matchup,
    generate_benchmark_metrics_report,
    run_paired_hypothesis_tests,
)
from benchmarks.framework.reporter import BenchmarkReporter
from benchmarks.framework.runner import (
    ALNSLinUCBFallbackOptimizer,
    BenchmarkRunner,
    resolve_optimizer,
)


class TestBenchmarkConfig:
    """Tests for configuration dataclasses and path resolution."""

    def test_default_config_initialization(self) -> None:
        cfg = BenchmarkConfig(mode=BenchmarkMode.SMOKE)
        assert cfg.mode == BenchmarkMode.SMOKE
        assert len(cfg.algorithms) == 5
        algo_ids = [a.algo_id for a in cfg.algorithms]
        assert AlgorithmId.PORTFOLIO_HYBRID.value in algo_ids
        assert AlgorithmId.ALNS_BANDIT.value in algo_ids
        assert AlgorithmId.ALNS_RANDOM.value in algo_ids
        assert AlgorithmId.LINUCB_ALNS.value in algo_ids
        assert AlgorithmId.PPO_ALNS.value in algo_ids

    def test_tier_classification(self) -> None:
        assert ComplexityTier.from_room_count(1) == ComplexityTier.COMPACT
        assert ComplexityTier.from_room_count(3) == ComplexityTier.COMPACT
        assert ComplexityTier.from_room_count(4) == ComplexityTier.STANDARD
        assert ComplexityTier.from_room_count(5) == ComplexityTier.STANDARD
        assert ComplexityTier.from_room_count(6) == ComplexityTier.COMPLEX
        assert ComplexityTier.from_room_count(12) == ComplexityTier.COMPLEX

    def test_config_serialization(self) -> None:
        cfg = BenchmarkConfig(
            mode=BenchmarkMode.VALIDATION,
            plan_ids=[10, 20, 30],
            time_limit_seconds=3.0,
            max_iterations=15,
        )
        d = cfg.to_dict()
        assert d["mode"] == "validation"
        assert d["plan_ids"] == [10, 20, 30]
        assert d["time_limit_seconds"] == 3.0

        deserialized = BenchmarkConfig.from_dict(d)
        assert deserialized.mode == BenchmarkMode.VALIDATION
        assert deserialized.plan_ids == [10, 20, 30]
        assert len(deserialized.algorithms) == 5


class TestLexicographicalAndMetrics:
    """Tests for competition rules, paired metrics, and statistical hypothesis testing."""

    def test_lexicographical_validity_dominance(self) -> None:
        valid_res = AlgorithmRunResult(
            algorithm_name="AlgoA",
            h_count=5,
            total_cost_J=100.0,
            is_valid=True,
        )
        invalid_res = AlgorithmRunResult(
            algorithm_name="AlgoB",
            h_count=2,
            total_cost_J=10.0,
            is_valid=False,
        )
        # Valid beats invalid regardless of H or J
        assert evaluate_lexicographical_matchup(valid_res, invalid_res) == 1
        assert evaluate_lexicographical_matchup(invalid_res, valid_res) == -1

        # Both invalid ties
        invalid_res2 = AlgorithmRunResult(algorithm_name="AlgoC", is_valid=False)
        assert evaluate_lexicographical_matchup(invalid_res, invalid_res2) == 0

    def test_lexicographical_primary_h_dominance(self) -> None:
        lower_h = AlgorithmRunResult(
            algorithm_name="AlgoA",
            h_count=3,
            total_cost_J=50.0,
            is_valid=True,
        )
        higher_h = AlgorithmRunResult(
            algorithm_name="AlgoB",
            h_count=4,
            total_cost_J=20.0,
            is_valid=True,
        )
        # Lower H wins even with higher J
        assert evaluate_lexicographical_matchup(lower_h, higher_h) == 1
        assert evaluate_lexicographical_matchup(higher_h, lower_h) == -1

    def test_lexicographical_secondary_j_comparison(self) -> None:
        cheaper_j = AlgorithmRunResult(
            algorithm_name="AlgoA",
            h_count=3,
            total_cost_J=25.50,
            is_valid=True,
        )
        costlier_j = AlgorithmRunResult(
            algorithm_name="AlgoB",
            h_count=3,
            total_cost_J=28.75,
            is_valid=True,
        )
        assert evaluate_lexicographical_matchup(cheaper_j, costlier_j) == 1
        assert evaluate_lexicographical_matchup(costlier_j, cheaper_j) == -1

        # Tied J within 1e-4 tolerance
        tied_j = AlgorithmRunResult(
            algorithm_name="AlgoC",
            h_count=3,
            total_cost_J=25.50005,
            is_valid=True,
        )
        assert evaluate_lexicographical_matchup(cheaper_j, tied_j, tolerance=1e-4) == 0

    def test_paired_comparison_synthetic(self) -> None:
        records: List[PlanBenchmarkRecord] = []
        # Create 10 paired plans: candidate wins 7, ties 2, loses 1
        for i in range(10):
            if i < 7:
                cand_j = 20.0
                comp_j = 25.0  # candidate wins (+5 reduction)
            elif i < 9:
                cand_j = 20.0
                comp_j = 20.0  # tie
            else:
                cand_j = 30.0
                comp_j = 25.0  # competitor wins

            records.append(
                PlanBenchmarkRecord(
                    plan_id=i,
                    tier="standard",
                    eligible_rooms=4,
                    total_area_m2=50.0,
                    results={
                        "Candidate": AlgorithmRunResult("Candidate", h_count=3, total_cost_J=cand_j, is_valid=True),
                        "Competitor": AlgorithmRunResult("Competitor", h_count=3, total_cost_J=comp_j, is_valid=True),
                    },
                )
            )

        res = compute_paired_comparison(records, "Candidate", "Competitor")
        assert res.total_pairs == 10
        assert res.candidate_wins == 7
        assert res.ties == 2
        assert res.competitor_wins == 1
        assert res.candidate_win_rate_pct == 70.0
        assert res.candidate_non_loss_rate_pct == 90.0
        assert res.mean_delta_J == 3.0  # (7*5 + 2*0 - 1*5)/10 = 30/10 = 3.0

    def test_statistical_tests_significant_difference(self) -> None:
        cand_costs = [20.0, 21.0, 19.5, 20.2, 19.8, 20.5, 19.9, 20.1]
        comp_costs = [25.0, 26.0, 24.5, 25.2, 24.8, 25.5, 24.9, 25.1]
        t_stat, t_p, w_stat, w_p = run_paired_hypothesis_tests(cand_costs, comp_costs)
        # Strong systematic difference
        assert t_p < 0.001
        assert w_p < 0.05
        assert t_stat > 0

    def test_statistical_tests_identical_distributions(self) -> None:
        cand_costs = [20.0, 21.0, 22.0, 23.0]
        comp_costs = [20.0, 21.0, 22.0, 23.0]
        t_stat, t_p, w_stat, w_p = run_paired_hypothesis_tests(cand_costs, comp_costs)
        assert t_stat == 0.0
        assert t_p == 1.0
        assert w_p == 1.0

    def test_bootstrap_confidence_interval(self) -> None:
        diffs = [2.0, 2.5, 3.0, 2.8, 3.2, 2.7, 2.9, 3.1]
        ci_low, ci_high = bootstrap_confidence_interval(diffs, num_bootstrap=500, random_seed=42)
        assert ci_low <= 2.78 <= ci_high
        assert ci_low > 1.5
        assert ci_high < 4.0

        # Constant data
        c_low, c_high = bootstrap_confidence_interval([5.0, 5.0, 5.0])
        assert c_low == 5.0 and c_high == 5.0

        # Empty data
        e_low, e_high = bootstrap_confidence_interval([])
        assert e_low == 0.0 and e_high == 0.0


class TestLinUCBOptimizerFallback:
    """Tests for autonomous LinUCB fallback optimizer."""

    def test_linucb_fallback_instantiation(self) -> None:
        opt = ALNSLinUCBFallbackOptimizer(name="LinUCB_ALNS", max_iterations=10, alpha=0.5)
        assert opt.name == "LinUCB_ALNS"
        assert opt.max_iterations == 10
        assert opt.alpha == 0.5

    def test_linucb_resolve_optimizer(self) -> None:
        cfg = AlgorithmConfig(
            algo_id=AlgorithmId.LINUCB_ALNS.value,
            display_name="LinUCB-ALNS",
            max_iterations=10,
        )
        opt = resolve_optimizer(cfg)
        assert opt is not None
        assert opt.name == "LinUCB_ALNS"


class TestReportingArtifacts:
    """Tests for artifact generation and export."""

    def test_reporter_exports_and_markdown(self, tmp_path: Path) -> None:
        # Create mock record
        r1 = PlanBenchmarkRecord(
            plan_id=0,
            tier="standard",
            eligible_rooms=4,
            total_area_m2=45.2,
            results={
                "PortfolioHybrid": AlgorithmRunResult("PortfolioHybrid", h_count=3, total_cost_J=24.5, is_valid=True, runtime_s=0.5),
                "ALNS_Bandit": AlgorithmRunResult("ALNS_Bandit", h_count=3, total_cost_J=23.2, is_valid=True, runtime_s=0.8),
                "ALNS_Random": AlgorithmRunResult("ALNS_Random", h_count=3, total_cost_J=23.8, is_valid=True, runtime_s=0.7),
                "LinUCB_ALNS": AlgorithmRunResult("LinUCB_ALNS", h_count=3, total_cost_J=23.0, is_valid=True, runtime_s=0.8),
                "PPO_ALNS": AlgorithmRunResult("PPO_ALNS", h_count=3, total_cost_J=22.8, is_valid=True, runtime_s=1.2),
            },
        )
        records = [r1]
        algos = ["PortfolioHybrid", "ALNS_Bandit", "ALNS_Random", "LinUCB_ALNS", "PPO_ALNS"]

        report = generate_benchmark_metrics_report(records, algos, baseline_name="PortfolioHybrid")

        wide_csv = tmp_path / "results.csv"
        tall_csv = tmp_path / "tall.csv"
        json_file = tmp_path / "summary.json"
        md_file = tmp_path / "report.md"

        BenchmarkReporter.export_wide_csv(records, algos, wide_csv)
        BenchmarkReporter.export_tall_csv(records, tall_csv)
        BenchmarkReporter.export_summary_json(report, json_file)
        md_text = BenchmarkReporter.generate_markdown_report(report, md_file)

        assert wide_csv.exists() and wide_csv.stat().st_size > 0
        assert tall_csv.exists() and tall_csv.stat().st_size > 0
        assert json_file.exists() and json_file.stat().st_size > 0
        assert md_file.exists() and len(md_text) > 100
        assert "Honeywell Cable Ring Optimization: 5-Way Empirical Benchmark Report" in md_text


class TestRunnerExecutionAndResumption:
    """Tests for multi-process runner execution and checkpoint resumption."""

    def test_runner_smoke_execution_and_resumption(self, tmp_path: Path) -> None:
        # Use 2 clean validation plans with small step budget
        cfg = BenchmarkConfig(
            mode=BenchmarkMode.CUSTOM,
            plan_ids=[0, 4],
            time_limit_seconds=2.0,
            max_iterations=3,
            max_workers=1,
            output_dir=tmp_path,
            resume=True,
        )

        runner = BenchmarkRunner(config=cfg)
        report, records = runner.run()

        assert len(records) == 2
        assert cfg.checkpoint_file.exists()

        # Checkpoint resumption test: Second run should load all 2 plans from checkpoint
        runner_resumed = BenchmarkRunner(config=cfg)
        runner_resumed.load_checkpoint()
        assert len(runner_resumed.completed_records) == 2

        # Verify summary report structure
        assert report.total_plans == 2
        assert "PortfolioHybrid" in report.algorithms
        assert "ALNS_Bandit" in report.algorithms
