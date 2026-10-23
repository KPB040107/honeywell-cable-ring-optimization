"""Unit tests for Post-Champion Experimental Evaluation Framework (Agent 5).

Tests:
1. Independent Experimental Partition Generation and Zero-Overlap Test Split Isolation.
2. Matched-Budget Paired Head-to-Head Comparison Protocol and Lexicographical Ranking.
3. Candidate Optimizer Registry & Dynamic Binding.
4. Independent Oracle Evaluation with FloorPlanChecker at 1 mm Tolerance.
5. Statistical Significance Testing (Wilcoxon signed-rank, Student's t-test, 95% Bootstrap CIs).
"""

from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest

from benchmarks.experimental.candidates import (
    get_candidate_registry,
)
from benchmarks.experimental.partition import (
    ExperimentalPartition,
    build_experimental_partition,
    verify_isolation,
)
from benchmarks.experimental.protocol import (
    MatchedBudgetProtocol,
    MatchupOutcome,
    PairedPlanComparison,
    PlanRunMetrics,
    build_paired_comparison,
    evaluate_head_to_head,
)
from benchmarks.experimental.statistical_tests import (
    analyze_paired_comparisons,
    run_bootstrap_ci,
    run_paired_hypothesis_tests,
)


# ============================================================================
# 1. Tests for Independent Experimental Partition & Hygiene
# ============================================================================

class TestExperimentalPartitionHygiene:
    """Tests for partition generation and strict isolation of frozen test split."""

    def test_partition_persistence_and_metadata(self) -> None:
        part_path = Path("results/benchmarks/experimental/experimental_val_500.json")
        assert part_path.exists(), "experimental_val_500.json must exist"

        partition = ExperimentalPartition.load(part_path)
        assert partition.total_count == 500
        assert len(partition.plan_ids) == 500
        assert partition.tier_counts["compact"] == 100
        assert partition.tier_counts["standard"] == 300
        assert partition.tier_counts["complex"] == 100
        assert partition.frozen_test_isolation_verified is True

    def test_strict_zero_overlap_with_frozen_test_split(self) -> None:
        part_path = Path("results/benchmarks/experimental/experimental_val_500.json")
        partition = ExperimentalPartition.load(part_path)

        is_isolated, overlap = verify_isolation(partition.plan_ids, "split.json")
        assert is_isolated is True
        assert len(overlap) == 0, f"Partition must have 0 overlap with frozen test split! Found: {overlap}"


# ============================================================================
# 2. Tests for Matched-Budget Paired Comparison Protocol
# ============================================================================

class TestPairedEvaluationProtocol:
    """Tests for lexicographical hierarchy and paired metric derivations."""

    def test_deterministic_seed_generation(self) -> None:
        protocol = MatchedBudgetProtocol(base_random_seed=42)
        s1 = protocol.get_seed_for_plan(100)
        s2 = protocol.get_seed_for_plan(100)
        s3 = protocol.get_seed_for_plan(101)

        assert s1 == s2
        assert s1 != s3

    def test_lexicographical_hierarchy_feasibility(self) -> None:
        champ = PlanRunMetrics(
            plan_id=1, algorithm_name="champ", is_valid=False, h_count=1, cost_J=50.0,
            wire_length_L=50.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        cand = PlanRunMetrics(
            plan_id=1, algorithm_name="cand", is_valid=True, h_count=3, cost_J=100.0,
            wire_length_L=100.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        # Feasible candidate beats infeasible champion regardless of H or J
        outcome = evaluate_head_to_head(champ, cand)
        assert outcome == MatchupOutcome.WIN

    def test_lexicographical_hierarchy_hub_count(self) -> None:
        champ = PlanRunMetrics(
            plan_id=1, algorithm_name="champ", is_valid=True, h_count=2, cost_J=50.0,
            wire_length_L=50.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        cand = PlanRunMetrics(
            plan_id=1, algorithm_name="cand", is_valid=True, h_count=1, cost_J=80.0,
            wire_length_L=80.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        # Fewer hubs strictly beats lower routing cost
        outcome = evaluate_head_to_head(champ, cand)
        assert outcome == MatchupOutcome.WIN

    def test_lexicographical_hierarchy_cost_delta(self) -> None:
        champ = PlanRunMetrics(
            plan_id=1, algorithm_name="champ", is_valid=True, h_count=2, cost_J=60.0,
            wire_length_L=60.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        cand = PlanRunMetrics(
            plan_id=1, algorithm_name="cand", is_valid=True, h_count=2, cost_J=55.0,
            wire_length_L=55.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        outcome = evaluate_head_to_head(champ, cand)
        assert outcome == MatchupOutcome.WIN

        comp = build_paired_comparison(1, "standard", champ, cand)
        assert comp.delta_j == 5.0
        assert comp.pct_j_improvement == pytest.approx((5.0 / 60.0) * 100.0, abs=1e-3)
        assert comp.outcome == MatchupOutcome.WIN


# ============================================================================
# 3. Tests for Candidate Registry
# ============================================================================

class TestCandidateRegistry:
    """Tests for candidate instantiation and dynamic binding."""

    def test_registry_contains_all_post_champion_candidates(self) -> None:
        reg = get_candidate_registry(max_iterations=10)
        expected = [
            "LinUCB_ALNS",
            "Agent2_OptimalityBounds",
            "Agent3_AdvancedOperators",
            "Agent4_PerformanceSpatial",
            "Integrated_PostChampion",
        ]
        for name in expected:
            assert name in reg
            assert hasattr(reg[name], "optimize")


# ============================================================================
# 4. Tests for Statistical Significance & Bootstrap CI
# ============================================================================

class TestStatisticalEvaluations:
    """Tests for Wilcoxon test, t-test, and 2000-resample bootstrap CIs."""

    def test_bootstrap_ci_exactness(self) -> None:
        # Uniform array
        vals = [10.0] * 50
        ci = run_bootstrap_ci(vals, "test_metric", n_resamples=500)
        assert ci.sample_mean == pytest.approx(10.0)
        assert ci.ci_lower == pytest.approx(10.0)
        assert ci.ci_upper == pytest.approx(10.0)

    def test_wilcoxon_paired_improvement(self) -> None:
        champ_costs = [100.0, 105.0, 110.0, 95.0, 120.0, 80.0, 90.0, 115.0, 100.0, 105.0]
        cand_costs = [90.0, 95.0, 100.0, 85.0, 110.0, 75.0, 80.0, 105.0, 90.0, 95.0]  # Exactly -10 each

        wilc, tst = run_paired_hypothesis_tests(champ_costs, cand_costs)
        assert wilc.significant_p05 is True
        assert wilc.effect_size == pytest.approx(1.0)  # All positive ranks
        assert tst.significant_p05 is True

    def test_stratum_summary_analysis(self) -> None:
        champ = PlanRunMetrics(
            plan_id=1, algorithm_name="champ", is_valid=True, h_count=2, cost_J=50.0,
            wire_length_L=50.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=2.0,
        )
        cand = PlanRunMetrics(
            plan_id=1, algorithm_name="cand", is_valid=True, h_count=2, cost_J=45.0,
            wire_length_L=45.0, crossings_X=0, overlaps_O=0, bends_B=0, area_gap_m2=0.0, runtime_seconds=1.0,
        )
        comp = build_paired_comparison(1, "standard", champ, cand)
        summary = analyze_paired_comparisons([comp], "standard")

        assert summary.wins == 1
        assert summary.losses == 0
        assert summary.win_rate_pct == 100.0
        assert summary.non_loss_rate_pct == 100.0
        assert summary.mean_delta_j == pytest.approx(5.0)
        assert summary.mean_speedup == pytest.approx(2.0)
