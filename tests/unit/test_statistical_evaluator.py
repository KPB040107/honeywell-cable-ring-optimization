"""Unit tests for Advanced Statistical Evaluation Module (Agent 2).

Tests:
1. Descriptive Statistics (mean, median, sample std ddof=1, quartiles Q1/Q3, IQR, min/max).
2. Paired Percentage Improvements ((J_base - J_algo) / J_base * 100).
3. Official Competition Lexicographical Rule (Validity > H > J > Delta A_gap).
4. Paired Hypothesis Tests (Student's t-test and Wilcoxon signed-rank test).
5. Empirical Non-parametric Bootstrap Confidence Intervals (B=2,000 resamples).
6. Complexity Tier Stratification (Compact <=3, Standard 4-5, Complex >=6).
7. Separation of Full Partition vs Feasible Subset.
8. End-to-end StatisticalEvaluator engine and report serialization.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List
import numpy as np
import pytest

from benchmarks.framework.config import ComplexityTier
from benchmarks.framework.metrics import AlgorithmRunResult, PlanBenchmarkRecord
from benchmarks.framework.statistical_evaluator import (
    AlgorithmFullProfile,
    BootstrapCIResult,
    ComprehensiveStatisticalReport,
    DescriptiveStatistics,
    HypothesisTestResult,
    PairedEvaluationResult,
    StatisticalEvaluator,
    TierEvaluationResult,
    WinTieLossRecord,
    bootstrap_confidence_interval,
    compute_descriptive_stats,
    compute_paired_percentage_improvements,
    evaluate_lexicographical_matchup,
    format_markdown_summary,
    run_paired_hypothesis_tests,
)


# ============================================================================
# 1. Tests for Descriptive Statistics
# ============================================================================

class TestDescriptiveStatistics:
    """Tests for descriptive statistics including IQR, sample std, and quartiles."""

    def test_basic_descriptive_stats(self) -> None:
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = compute_descriptive_stats(data)

        assert stats.count == 5
        assert stats.mean == pytest.approx(30.0)
        assert stats.median == pytest.approx(30.0)
        # Sample standard deviation (ddof=1) of [10, 20, 30, 40, 50]
        expected_std = float(np.std(data, ddof=1))
        assert stats.std == pytest.approx(round(expected_std, 4))
        # Percentiles
        assert stats.q25 == pytest.approx(20.0)
        assert stats.q75 == pytest.approx(40.0)
        assert stats.iqr == pytest.approx(20.0)
        assert stats.min_val == pytest.approx(10.0)
        assert stats.max_val == pytest.approx(50.0)

    def test_empty_and_single_element(self) -> None:
        empty_stats = compute_descriptive_stats([])
        assert empty_stats.count == 0
        assert empty_stats.mean == 0.0
        assert empty_stats.std == 0.0
        assert empty_stats.iqr == 0.0

        single_stats = compute_descriptive_stats([42.0])
        assert single_stats.count == 1
        assert single_stats.mean == 42.0
        assert single_stats.median == 42.0
        assert single_stats.std == 0.0
        assert single_stats.iqr == 0.0
        assert single_stats.min_val == 42.0
        assert single_stats.max_val == 42.0

    def test_two_elements_sample_std(self) -> None:
        stats = compute_descriptive_stats([10.0, 20.0])
        assert stats.count == 2
        assert stats.mean == 15.0
        # For n=2, diff=10, var = 100/1 = 100, std = 10 / sqrt(2) * sqrt(2) approx 7.0711
        assert stats.std == pytest.approx(7.0711, abs=1e-4)

    def test_non_finite_filtering(self) -> None:
        dirty_data = [10.0, float("nan"), 20.0, float("inf"), float("-inf"), 30.0]
        stats = compute_descriptive_stats(dirty_data)
        assert stats.count == 3
        assert stats.mean == pytest.approx(20.0)
        assert stats.min_val == pytest.approx(10.0)
        assert stats.max_val == pytest.approx(30.0)

    def test_to_dict_roundtrip(self) -> None:
        stats = compute_descriptive_stats([1.0, 2.0, 3.0])
        d = stats.to_dict()
        assert d["count"] == 3
        assert d["mean"] == 2.0
        assert "iqr" in d


# ============================================================================
# 2. Tests for Paired Percentage Improvements
# ============================================================================

class TestPairedPercentageImprovements:
    """Tests for paired percentage improvements ((J_base - J_algo) / J_base * 100)."""

    def test_positive_and_negative_improvements(self) -> None:
        base = [100.0, 200.0, 50.0]
        algo = [80.0, 220.0, 50.0]  # +20%, -10%, 0%
        pcts = compute_paired_percentage_improvements(base, algo)

        assert len(pcts) == 3
        assert pcts[0] == pytest.approx(20.0)
        assert pcts[1] == pytest.approx(-10.0)
        assert pcts[2] == pytest.approx(0.0)

    def test_division_by_zero_and_non_finite_protection(self) -> None:
        base = [0.0, -5.0, float("nan"), 100.0]
        algo = [10.0, 20.0, 30.0, 90.0]
        pcts = compute_paired_percentage_improvements(base, algo)

        assert len(pcts) == 1
        assert pcts[0] == pytest.approx(10.0)  # (100 - 90)/100 * 100 = 10%


# ============================================================================
# 3. Tests for Official Lexicographical Criteria
# ============================================================================

class TestLexicographicalMatchup:
    """Tests official 4-tier lexicographical rule: Validity > H > J > Delta A_gap."""

    def test_tier1_validity_dominance(self) -> None:
        cand_valid = AlgorithmRunResult(
            algorithm_name="Candidate",
            h_count=5,
            total_cost_J=500.0,
            area_gap_m2=50.0,
            is_valid=True,
        )
        comp_invalid = AlgorithmRunResult(
            algorithm_name="Competitor",
            h_count=1,
            total_cost_J=10.0,
            area_gap_m2=0.0,
            is_valid=False,
        )
        # Valid beats invalid regardless of H or J
        assert evaluate_lexicographical_matchup(cand_valid, comp_invalid) == 1
        assert evaluate_lexicographical_matchup(comp_invalid, cand_valid) == -1

        # Both invalid is a tie
        cand_invalid = AlgorithmRunResult(algorithm_name="Candidate", is_valid=False)
        assert evaluate_lexicographical_matchup(cand_invalid, comp_invalid) == 0

    def test_tier2_hub_minimization(self) -> None:
        cand_fewer_hubs = AlgorithmRunResult(
            algorithm_name="Candidate",
            h_count=2,
            total_cost_J=250.0,
            is_valid=True,
        )
        comp_more_hubs = AlgorithmRunResult(
            algorithm_name="Competitor",
            h_count=3,
            total_cost_J=180.0,  # Lower cost, but more hubs!
            is_valid=True,
        )
        # H=2 beats H=3 even though J is worse
        assert evaluate_lexicographical_matchup(cand_fewer_hubs, comp_more_hubs) == 1
        assert evaluate_lexicographical_matchup(comp_more_hubs, cand_fewer_hubs) == -1

    def test_tier3_routing_cost_minimization(self) -> None:
        cand_lower_cost = AlgorithmRunResult(
            algorithm_name="Candidate",
            h_count=2,
            total_cost_J=150.0,
            is_valid=True,
        )
        comp_higher_cost = AlgorithmRunResult(
            algorithm_name="Competitor",
            h_count=2,
            total_cost_J=155.0,
            is_valid=True,
        )
        assert evaluate_lexicographical_matchup(cand_lower_cost, comp_higher_cost) == 1
        assert evaluate_lexicographical_matchup(comp_higher_cost, cand_lower_cost) == -1

    def test_tier4_area_gap_minimization(self) -> None:
        cand_better_gap = AlgorithmRunResult(
            algorithm_name="Candidate",
            h_count=2,
            total_cost_J=150.00001,  # within 1e-4 tolerance of comp
            area_gap_m2=10.0,        # smaller area gap (better balance)
            is_valid=True,
        )
        comp_worse_gap = AlgorithmRunResult(
            algorithm_name="Competitor",
            h_count=2,
            total_cost_J=150.00002,
            area_gap_m2=25.0,
            is_valid=True,
        )
        # H tied, J tied within tolerance -> Area Gap decides
        assert evaluate_lexicographical_matchup(cand_better_gap, comp_worse_gap) == 1
        assert evaluate_lexicographical_matchup(comp_worse_gap, cand_better_gap) == -1

    def test_exact_tie(self) -> None:
        cand = AlgorithmRunResult(
            algorithm_name="Candidate",
            h_count=2,
            total_cost_J=150.0,
            area_gap_m2=12.0,
            is_valid=True,
        )
        comp = AlgorithmRunResult(
            algorithm_name="Competitor",
            h_count=2,
            total_cost_J=150.0,
            area_gap_m2=12.0,
            is_valid=True,
        )
        assert evaluate_lexicographical_matchup(cand, comp) == 0

    def test_win_tie_loss_record_calculation(self) -> None:
        record = WinTieLossRecord.from_counts(wins=16, ties=4, losses=5)
        assert record.total_matchups == 25
        assert record.wins == 16
        assert record.ties == 4
        assert record.losses == 5
        assert record.win_rate_pct == pytest.approx(64.0)
        assert record.tie_rate_pct == pytest.approx(16.0)
        assert record.loss_rate_pct == pytest.approx(20.0)
        assert record.non_loss_rate_pct == pytest.approx(80.0)


# ============================================================================
# 4. Tests for Paired Hypothesis Testing
# ============================================================================

class TestPairedHypothesisTests:
    """Tests paired Student's t-test and Wilcoxon signed-rank test."""

    def test_significant_difference_synthetic(self) -> None:
        np.random.seed(42)
        base = list(np.random.normal(loc=120.0, scale=8.0, size=50))
        # Candidate is consistently 15 units better with noise
        cand = [b - 15.0 + float(np.random.normal(0, 2.0)) for b in base]

        t_res, w_res = run_paired_hypothesis_tests(base, cand)

        assert t_res.statistic > 0.0  # base > cand (positive difference)
        assert t_res.p_value < 1e-10
        assert t_res.is_significant_05 is True
        assert t_res.is_significant_01 is True
        assert t_res.sample_size == 50

        assert w_res.statistic >= 0.0
        assert w_res.p_value < 1e-8
        assert w_res.is_significant_05 is True
        assert w_res.is_significant_01 is True

    def test_identical_distributions(self) -> None:
        costs = [100.0, 105.0, 110.0, 95.0, 102.0]
        t_res, w_res = run_paired_hypothesis_tests(costs, costs)

        assert t_res.statistic == 0.0
        assert t_res.p_value == 1.0
        assert t_res.is_significant_05 is False

        assert w_res.statistic == 0.0
        assert w_res.p_value == 1.0
        assert w_res.is_significant_05 is False

    def test_constant_non_zero_shift(self) -> None:
        # Zero variance in paired difference (e.g. cand is exactly 5 units lower everywhere)
        base = [100.0, 120.0, 140.0, 160.0]
        cand = [95.0, 115.0, 135.0, 155.0]

        t_res, w_res = run_paired_hypothesis_tests(base, cand)
        # Because variance of diffs is zero, t-test handles safely without crash
        assert t_res.p_value in [0.0, 1.0] or np.isfinite(t_res.p_value)
        # Wilcoxon can still rank the differences
        assert np.isfinite(w_res.statistic)

    def test_insufficient_samples(self) -> None:
        t_res, w_res = run_paired_hypothesis_tests([100.0], [90.0])
        assert t_res.sample_size == 1
        assert t_res.p_value == 1.0
        assert w_res.sample_size == 1
        assert w_res.p_value == 1.0


# ============================================================================
# 5. Tests for Bootstrap Confidence Interval (B=2,000)
# ============================================================================

class TestBootstrapConfidenceInterval:
    """Tests empirical non-parametric bootstrap with B=2,000 resamples."""

    def test_bootstrap_known_normal(self) -> None:
        rng = np.random.RandomState(42)
        diffs = rng.normal(loc=12.5, scale=3.0, size=100)

        ci = bootstrap_confidence_interval(
            data=diffs,
            num_bootstrap=2000,
            confidence_level=0.95,
            random_seed=42,
            metric_name="mean_delta_J",
        )

        assert ci.num_resamples == 2000
        assert ci.confidence_level == 0.95
        assert ci.observed_mean == pytest.approx(float(np.mean(diffs)), abs=0.01)
        # 95% CI must contain observed mean
        assert ci.ci_lower <= ci.observed_mean <= ci.ci_upper
        # CI width should be approximately 2 * 1.96 * (3.0 / sqrt(100)) approx 1.18
        assert 11.5 <= ci.ci_lower <= 12.5
        assert 12.5 <= ci.ci_upper <= 13.5

    def test_bootstrap_constant_and_empty(self) -> None:
        empty_ci = bootstrap_confidence_interval([])
        assert empty_ci.observed_mean == 0.0
        assert empty_ci.ci_lower == 0.0
        assert empty_ci.ci_upper == 0.0

        const_ci = bootstrap_confidence_interval([5.5, 5.5, 5.5, 5.5])
        assert const_ci.observed_mean == 5.5
        assert const_ci.ci_lower == 5.5
        assert const_ci.ci_upper == 5.5
        assert const_ci.standard_error == 0.0

    def test_bootstrap_reproducibility(self) -> None:
        data = [1.0, 2.5, 3.8, 4.2, 5.1, 6.7]
        ci1 = bootstrap_confidence_interval(data, num_bootstrap=2000, random_seed=12345)
        ci2 = bootstrap_confidence_interval(data, num_bootstrap=2000, random_seed=12345)
        assert ci1.ci_lower == ci2.ci_lower
        assert ci1.ci_upper == ci2.ci_upper
        assert ci1.standard_error == ci2.standard_error


# ============================================================================
# 6. Tests for Complexity Tier Stratification
# ============================================================================

class TestTierStratification:
    """Tests stratification into Compact (<=3), Standard (4-5), Complex (>=6)."""

    def test_stratified_evaluation(self) -> None:
        # Create 6 synthetic records: 2 compact, 2 standard, 2 complex
        records: List[PlanBenchmarkRecord] = [
            # Compact (room count <= 3)
            PlanBenchmarkRecord(
                plan_id=1,
                tier=ComplexityTier.COMPACT.value,
                eligible_rooms=2,
                total_area_m2=40.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=1, total_cost_J=50.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=1, total_cost_J=55.0, is_valid=True),
                },
            ),
            PlanBenchmarkRecord(
                plan_id=2,
                tier=ComplexityTier.COMPACT.value,
                eligible_rooms=3,
                total_area_m2=60.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=1, total_cost_J=60.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=1, total_cost_J=62.0, is_valid=True),
                },
            ),
            # Standard (room count 4-5)
            PlanBenchmarkRecord(
                plan_id=3,
                tier=ComplexityTier.STANDARD.value,
                eligible_rooms=4,
                total_area_m2=120.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=2, total_cost_J=100.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=2, total_cost_J=110.0, is_valid=True),
                },
            ),
            PlanBenchmarkRecord(
                plan_id=4,
                tier=ComplexityTier.STANDARD.value,
                eligible_rooms=5,
                total_area_m2=150.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=2, total_cost_J=120.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=2, total_cost_J=130.0, is_valid=True),
                },
            ),
            # Complex (room count >= 6)
            PlanBenchmarkRecord(
                plan_id=5,
                tier=ComplexityTier.COMPLEX.value,
                eligible_rooms=6,
                total_area_m2=200.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=3, total_cost_J=180.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=3, total_cost_J=200.0, is_valid=True),
                },
            ),
            PlanBenchmarkRecord(
                plan_id=6,
                tier=ComplexityTier.COMPLEX.value,
                eligible_rooms=8,
                total_area_m2=280.0,
                results={
                    "AlgoA": AlgorithmRunResult(algorithm_name="AlgoA", h_count=4, total_cost_J=240.0, is_valid=True),
                    "Baseline": AlgorithmRunResult(algorithm_name="Baseline", h_count=4, total_cost_J=270.0, is_valid=True),
                },
            ),
        ]

        evaluator = StatisticalEvaluator(num_bootstrap=500, random_seed=42)
        stratified = evaluator.evaluate_stratified_tiers(
            records=records,
            algorithm_names=["AlgoA", "Baseline"],
            baseline_name="Baseline",
        )

        assert "compact" in stratified
        assert "standard" in stratified
        assert "complex" in stratified

        compact = stratified["compact"]
        assert compact.plan_count == 2
        assert compact.pct_of_total == pytest.approx(33.33, abs=0.1)
        assert compact.algorithms["AlgoA"].valid_runs == 2

        standard = stratified["standard"]
        assert standard.plan_count == 2
        assert "AlgoA_vs_Baseline" in standard.pairwise_vs_baseline
        pair_std = standard.pairwise_vs_baseline["AlgoA_vs_Baseline"]
        assert pair_std.delta_J_stats.mean == pytest.approx(10.0)  # (110-100 + 130-120)/2 = 10.0

        complex_tier = stratified["complex"]
        assert complex_tier.plan_count == 2
        pair_comp = complex_tier.pairwise_vs_baseline["AlgoA_vs_Baseline"]
        assert pair_comp.delta_J_stats.mean == pytest.approx(25.0)  # (20+30)/2 = 25.0


# ============================================================================
# 7. Tests for Separation of Full Partition vs Feasible Subset
# ============================================================================

class TestFullPartitionVsFeasibleSubsetSeparation:
    """Verifies that infeasible runs are correctly separated between full population and feasible subset."""

    def test_separation_with_invalid_solutions(self) -> None:
        records: List[PlanBenchmarkRecord] = [
            # Plan 1: Both Valid. Candidate has lower cost.
            PlanBenchmarkRecord(
                plan_id=1,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    "Candidate": AlgorithmRunResult("Candidate", h_count=2, total_cost_J=100.0, is_valid=True),
                    "Baseline": AlgorithmRunResult("Baseline", h_count=2, total_cost_J=120.0, is_valid=True),
                },
            ),
            # Plan 2: Both Valid. Baseline has lower cost.
            PlanBenchmarkRecord(
                plan_id=2,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    "Candidate": AlgorithmRunResult("Candidate", h_count=2, total_cost_J=115.0, is_valid=True),
                    "Baseline": AlgorithmRunResult("Baseline", h_count=2, total_cost_J=110.0, is_valid=True),
                },
            ),
            # Plan 3: Candidate Valid, Baseline Invalid -> Candidate wins by validity dominance.
            PlanBenchmarkRecord(
                plan_id=3,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    "Candidate": AlgorithmRunResult("Candidate", h_count=2, total_cost_J=105.0, is_valid=True),
                    "Baseline": AlgorithmRunResult("Baseline", h_count=2, total_cost_J=90.0, is_valid=False),
                },
            ),
            # Plan 4: Candidate Invalid, Baseline Valid -> Candidate loses by validity dominance.
            PlanBenchmarkRecord(
                plan_id=4,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    "Candidate": AlgorithmRunResult("Candidate", h_count=1, total_cost_J=80.0, is_valid=False),
                    "Baseline": AlgorithmRunResult("Baseline", h_count=2, total_cost_J=110.0, is_valid=True),
                },
            ),
            # Plan 5: Both Invalid -> Tie.
            PlanBenchmarkRecord(
                plan_id=5,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    "Candidate": AlgorithmRunResult("Candidate", is_valid=False),
                    "Baseline": AlgorithmRunResult("Baseline", is_valid=False),
                },
            ),
        ]

        evaluator = StatisticalEvaluator(num_bootstrap=500, random_seed=42)
        pair_res = evaluator.evaluate_paired_matchup(
            records=records,
            candidate_name="Candidate",
            baseline_name="Baseline",
        )

        # Full Partition Record (all 5 instances):
        # Plan 1: Cand win (lower cost)
        # Plan 2: Cand loss (comp lower cost)
        # Plan 3: Cand win (validity dominance)
        # Plan 4: Cand loss (validity dominance)
        # Plan 5: Tie (both invalid)
        full_rec = pair_res.full_lexicographical_record
        assert full_rec.total_matchups == 5
        assert full_rec.wins == 2
        assert full_rec.losses == 2
        assert full_rec.ties == 1
        assert full_rec.win_rate_pct == 40.0
        assert full_rec.non_loss_rate_pct == 60.0

        # Feasible Subset Record (mutually valid pairs only: Plans 1 and 2):
        feas_rec = pair_res.feasible_lexicographical_record
        assert feas_rec.total_matchups == 2
        assert feas_rec.wins == 1
        assert feas_rec.losses == 1
        assert feas_rec.ties == 0
        assert feas_rec.win_rate_pct == 50.0

        # Continuous Delta J evaluated strictly on the 2 feasible pairs:
        # Delta J: (120 - 100) = +20, (110 - 115) = -5
        assert pair_res.feasible_pairs == 2
        assert pair_res.delta_J_stats.count == 2
        assert pair_res.delta_J_stats.mean == pytest.approx(7.5)  # (20 + (-5)) / 2 = 7.5
        assert pair_res.delta_J_stats.min_val == pytest.approx(-5.0)
        assert pair_res.delta_J_stats.max_val == pytest.approx(20.0)

        # Percentage Improvements: 20/120*100 = 16.6667%, -5/110*100 = -4.5455%
        assert pair_res.pct_improvement_stats.count == 2
        assert pair_res.pct_improvement_stats.mean == pytest.approx((16.6667 - 4.5455) / 2.0, abs=0.01)

        # Algorithm Full Profile checks
        cand_prof = evaluator.evaluate_algorithm_profile(records, "Candidate")
        assert cand_prof.total_runs == 5
        assert cand_prof.valid_runs == 3  # Plans 1, 2, 3
        assert cand_prof.validity_rate_pct == 60.0
        # Cost J descriptive stats strictly on valid runs (100, 115, 105)
        assert cand_prof.cost_J.count == 3
        assert cand_prof.cost_J.mean == pytest.approx(106.6667, abs=1e-3)


# ============================================================================
# 8. Tests for End-to-End Engine & Markdown Formatting
# ============================================================================

class TestEndToEndStatisticalEvaluator:
    """Tests master evaluate() pipeline, serializability, and markdown table generation."""

    def test_master_evaluation_and_serialization(self) -> None:
        np.random.seed(42)
        n_plans = 12
        records: List[PlanBenchmarkRecord] = []

        for i in range(n_plans):
            rooms = 2 + (i % 6)  # rooms from 2 to 7
            tier = ComplexityTier.from_room_count(rooms).value
            base_cost = 100.0 + i * 10.0

            records.append(
                PlanBenchmarkRecord(
                    plan_id=i,
                    tier=tier,
                    eligible_rooms=rooms,
                    total_area_m2=rooms * 25.0,
                    results={
                        "PortfolioHybrid": AlgorithmRunResult(
                            algorithm_name="PortfolioHybrid",
                            h_count=2,
                            total_cost_J=base_cost,
                            total_length_m=base_cost * 0.8,
                            total_bends=12,
                            total_crossings=0,
                            total_overlap_m=0.0,
                            area_gap_m2=15.0,
                            is_valid=True,
                            runtime_s=1.2,
                        ),
                        "ALNS_Bandit": AlgorithmRunResult(
                            algorithm_name="ALNS_Bandit",
                            h_count=2,
                            total_cost_J=base_cost - 5.0,
                            total_length_m=(base_cost - 5.0) * 0.8,
                            total_bends=10,
                            total_crossings=0,
                            total_overlap_m=0.0,
                            area_gap_m2=12.0,
                            is_valid=True,
                            runtime_s=1.8,
                        ),
                        "LinUCB_ALNS": AlgorithmRunResult(
                            algorithm_name="LinUCB_ALNS",
                            h_count=2,
                            total_cost_J=base_cost - 12.0,
                            total_length_m=(base_cost - 12.0) * 0.8,
                            total_bends=8,
                            total_crossings=0,
                            total_overlap_m=0.0,
                            area_gap_m2=8.0,
                            is_valid=True,
                            runtime_s=2.1,
                        ),
                    },
                )
            )

        evaluator = StatisticalEvaluator(num_bootstrap=1000, random_seed=42)
        report = evaluator.evaluate(
            records=records,
            algorithm_names=["PortfolioHybrid", "ALNS_Bandit", "LinUCB_ALNS"],
            baseline_name="PortfolioHybrid",
            metadata={"benchmark_version": "v2.0", "author": "Agent2"},
        )

        assert report.total_plans == 12
        assert report.baseline_name == "PortfolioHybrid"
        assert len(report.algorithms_overall) == 3
        assert "LinUCB_ALNS_vs_PortfolioHybrid" in report.pairwise_overall

        linucb_pair = report.pairwise_overall["LinUCB_ALNS_vs_PortfolioHybrid"]
        assert linucb_pair.delta_J_stats.mean == pytest.approx(12.0)
        assert linucb_pair.full_lexicographical_record.wins == 12
        assert linucb_pair.full_lexicographical_record.win_rate_pct == 100.0
        assert linucb_pair.bootstrap_ci_delta_J.observed_mean == pytest.approx(12.0)

        # JSON Round-trip
        report_json = report.to_json()
        assert isinstance(report_json, str)
        parsed = json.loads(report_json)
        assert parsed["total_plans"] == 12
        assert parsed["metadata"]["author"] == "Agent2"

        # Markdown summary generation
        md_text = format_markdown_summary(report)
        assert "# Statistical Benchmark Evaluation Report" in md_text
        assert "Head-to-Head Lexicographical Standings" in md_text
        assert "LinUCB_ALNS" in md_text
        assert "Feasible Subset" in md_text
        assert "COMPACT" in md_text
        assert "STANDARD" in md_text
        assert "COMPLEX" in md_text

    def test_convenience_evaluate_paired_arrays(self) -> None:
        evaluator = StatisticalEvaluator(num_bootstrap=500, random_seed=42)
        cand_costs = [85.0, 92.0, 110.0, 78.0]
        base_costs = [100.0, 100.0, 120.0, 80.0]

        pair_res = evaluator.evaluate_paired_arrays(
            candidate_costs=cand_costs,
            baseline_costs=base_costs,
            candidate_name="MyAlgo",
            baseline_name="BaseAlgo",
        )

        assert pair_res.candidate_name == "MyAlgo"
        assert pair_res.baseline_name == "BaseAlgo"
        assert pair_res.total_pairs == 4
        assert pair_res.full_lexicographical_record.wins == 4
        assert pair_res.delta_J_stats.mean == pytest.approx(8.75)  # (15+8+10+2)/4 = 35/4 = 8.75

    def test_mixed_wilcoxon_ranks(self) -> None:
        # 10 pairs with mixed positive and negative differences
        base = [100.0, 105.0, 95.0, 110.0, 120.0, 85.0, 90.0, 115.0, 130.0, 100.0]
        cand = [95.0, 108.0, 90.0, 105.0, 125.0, 80.0, 92.0, 110.0, 128.0, 98.0]
        # diffs: +5, -3, +5, +5, -5, +5, -2, +5, +2, +2
        t_res, w_res = run_paired_hypothesis_tests(base, cand)
        assert w_res.statistic > 0.0  # neither sum of ranks is zero
        assert 0.0 < w_res.p_value <= 1.0
        assert t_res.statistic > 0.0

    def test_empty_records_and_missing_algorithm_handling(self) -> None:
        evaluator = StatisticalEvaluator()
        empty_report = evaluator.evaluate(
            records=[],
            algorithm_names=["PortfolioHybrid", "LinUCB_ALNS"],
            baseline_name="PortfolioHybrid",
        )
        assert empty_report.total_plans == 0
        assert empty_report.algorithms_overall["PortfolioHybrid"].total_runs == 0
        assert empty_report.algorithms_overall["PortfolioHybrid"].valid_runs == 0
        assert empty_report.pairwise_overall["LinUCB_ALNS_vs_PortfolioHybrid"].total_pairs == 0

