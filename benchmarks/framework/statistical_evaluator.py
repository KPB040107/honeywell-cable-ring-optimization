"""Advanced Statistical Evaluation Module for Honeywell Cable Ring Optimization.

Exclusive Owner: Agent 2 (Statistical Evaluation Lead)

Provides comprehensive, mathematically rigorous statistical evaluation for benchmark runs:
1. Descriptive Statistics:
   - Mean, Median, Sample Standard Deviation (ddof=1), Quartiles (Q1, Q3),
     and Interquartile Range (IQR = Q3 - Q1) across all key objectives (J, H, L, B, X, O, Area Gap, Runtime).
2. Paired Percentage Improvements:
   - Exact computation: ((J_base - J_algo) / J_base) * 100 for matched problem instances.
3. Official Competition Lexicographical Rule:
   - Strict 4-tier hierarchy: Validity > H (Hubs minimized) > J (Cost minimized) > Delta A_gap (Area Gap minimized).
   - Head-to-head Win / Tie / Loss records, win rates, and non-loss rates.
4. Paired Hypothesis Testing:
   - Paired Student's t-test (parametric) and Wilcoxon signed-rank test (non-parametric).
5. Empirical Non-parametric Bootstrap:
   - B = 2,000 resamples for 95% Confidence Intervals of mean Delta J and mean percentage improvement.
6. Complexity Tier Stratification:
   - Compact (<= 3 rooms), Standard (4-5 rooms), Complex (>= 6 rooms).
7. Full Partition vs Feasible Subset Separation:
   - Clean separation of full instance population vs mutually feasible / valid subsets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import scipy.stats as stats

from benchmarks.framework.config import ComplexityTier
from benchmarks.framework.metrics import AlgorithmRunResult, PlanBenchmarkRecord


# ============================================================================
# Dataclasses & Data Contracts
# ============================================================================

@dataclass(frozen=True)
class DescriptiveStatistics:
    """Summary descriptive statistics with robust handling of small or empty samples."""
    count: int
    mean: float
    median: float
    std: float
    q25: float
    q75: float
    iqr: float
    min_val: float
    max_val: float

    @classmethod
    def empty(cls) -> DescriptiveStatistics:
        return cls(
            count=0,
            mean=0.0,
            median=0.0,
            std=0.0,
            q25=0.0,
            q75=0.0,
            iqr=0.0,
            min_val=0.0,
            max_val=0.0,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WinTieLossRecord:
    """Head-to-head competition record under official lexicographical hierarchy."""
    total_matchups: int
    wins: int
    ties: int
    losses: int
    win_rate_pct: float
    tie_rate_pct: float
    loss_rate_pct: float
    non_loss_rate_pct: float

    @classmethod
    def from_counts(cls, wins: int, ties: int, losses: int) -> WinTieLossRecord:
        total = wins + ties + losses
        if total == 0:
            return cls(
                total_matchups=0,
                wins=0,
                ties=0,
                losses=0,
                win_rate_pct=0.0,
                tie_rate_pct=0.0,
                loss_rate_pct=0.0,
                non_loss_rate_pct=0.0,
            )
        win_rate = round((wins / total) * 100.0, 2)
        tie_rate = round((ties / total) * 100.0, 2)
        loss_rate = round((losses / total) * 100.0, 2)
        non_loss_rate = round(((wins + ties) / total) * 100.0, 2)
        return cls(
            total_matchups=total,
            wins=wins,
            ties=ties,
            losses=losses,
            win_rate_pct=win_rate,
            tie_rate_pct=tie_rate,
            loss_rate_pct=loss_rate,
            non_loss_rate_pct=non_loss_rate,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HypothesisTestResult:
    """Outcomes from parametric or non-parametric paired hypothesis testing."""
    test_name: str
    statistic: float
    p_value: float
    is_significant_05: bool
    is_significant_01: bool
    alternative: str = "two-sided"
    sample_size: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BootstrapCIResult:
    """Non-parametric empirical bootstrap confidence interval result."""
    metric_name: str
    observed_mean: float
    ci_lower: float
    ci_upper: float
    confidence_level: float = 0.95
    num_resamples: int = 2000
    standard_error: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlgorithmFullProfile:
    """Complete profile of an algorithm over evaluated instances."""
    algorithm_name: str
    total_runs: int
    valid_runs: int
    validity_rate_pct: float
    # Descriptive metrics on feasible (valid) runs:
    cost_J: DescriptiveStatistics
    hubs_H: DescriptiveStatistics
    length_L: DescriptiveStatistics
    bends_B: DescriptiveStatistics
    crossings_X: DescriptiveStatistics
    overlap_O: DescriptiveStatistics
    area_gap: DescriptiveStatistics
    runtime_s: DescriptiveStatistics

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm_name": self.algorithm_name,
            "total_runs": self.total_runs,
            "valid_runs": self.valid_runs,
            "validity_rate_pct": self.validity_rate_pct,
            "cost_J": self.cost_J.to_dict(),
            "hubs_H": self.hubs_H.to_dict(),
            "length_L": self.length_L.to_dict(),
            "bends_B": self.bends_B.to_dict(),
            "crossings_X": self.crossings_X.to_dict(),
            "overlap_O": self.overlap_O.to_dict(),
            "area_gap": self.area_gap.to_dict(),
            "runtime_s": self.runtime_s.to_dict(),
        }


@dataclass
class PairedEvaluationResult:
    """Comprehensive paired evaluation between candidate and baseline algorithms."""
    candidate_name: str
    baseline_name: str
    total_pairs: int
    feasible_pairs: int
    # Competition Lexicographical Records:
    # Full partition (Validity > H > J > Delta A_gap)
    full_lexicographical_record: WinTieLossRecord
    # Feasible subset (strictly valid pairs: H > J > Delta A_gap)
    feasible_lexicographical_record: WinTieLossRecord
    # Delta J metrics on feasible pairs (J_base - J_algo: positive = candidate is better):
    delta_J_stats: DescriptiveStatistics
    # Paired Percentage Improvements ((J_base - J_algo) / J_base * 100):
    pct_improvement_stats: DescriptiveStatistics
    # Hypothesis testing on feasible pairs:
    paired_t_test: HypothesisTestResult
    wilcoxon_signed_rank_test: HypothesisTestResult
    # Bootstrap 95% Confidence Intervals (B=2,000):
    bootstrap_ci_delta_J: BootstrapCIResult
    bootstrap_ci_pct_improvement: BootstrapCIResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_name": self.candidate_name,
            "baseline_name": self.baseline_name,
            "total_pairs": self.total_pairs,
            "feasible_pairs": self.feasible_pairs,
            "full_lexicographical_record": self.full_lexicographical_record.to_dict(),
            "feasible_lexicographical_record": self.feasible_lexicographical_record.to_dict(),
            "delta_J_stats": self.delta_J_stats.to_dict(),
            "pct_improvement_stats": self.pct_improvement_stats.to_dict(),
            "paired_t_test": self.paired_t_test.to_dict(),
            "wilcoxon_signed_rank_test": self.wilcoxon_signed_rank_test.to_dict(),
            "bootstrap_ci_delta_J": self.bootstrap_ci_delta_J.to_dict(),
            "bootstrap_ci_pct_improvement": self.bootstrap_ci_pct_improvement.to_dict(),
        }


@dataclass
class TierEvaluationResult:
    """Stratified evaluation within a specific complexity tier."""
    tier: str
    plan_count: int
    pct_of_total: float
    # Algorithm profiles (full vs feasible separated inside profile)
    algorithms: Dict[str, AlgorithmFullProfile] = field(default_factory=dict)
    # Paired comparisons against baseline
    pairwise_vs_baseline: Dict[str, PairedEvaluationResult] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "plan_count": self.plan_count,
            "pct_of_total": self.pct_of_total,
            "algorithms": {k: v.to_dict() for k, v in self.algorithms.items()},
            "pairwise_vs_baseline": {k: v.to_dict() for k, v in self.pairwise_vs_baseline.items()},
        }


@dataclass
class ComprehensiveStatisticalReport:
    """Master statistical report across overall dataset and all complexity tiers."""
    total_plans: int
    baseline_name: str
    algorithms_overall: Dict[str, AlgorithmFullProfile]
    pairwise_overall: Dict[str, PairedEvaluationResult]
    stratified_tiers: Dict[str, TierEvaluationResult]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_plans": self.total_plans,
            "baseline_name": self.baseline_name,
            "metadata": self.metadata,
            "algorithms_overall": {k: v.to_dict() for k, v in self.algorithms_overall.items()},
            "pairwise_overall": {k: v.to_dict() for k, v in self.pairwise_overall.items()},
            "stratified_tiers": {k: v.to_dict() for k, v in self.stratified_tiers.items()},
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ============================================================================
# Core Statistical Functions
# ============================================================================

def compute_descriptive_stats(
    values: Sequence[float],
    ddof: int = 1,
) -> DescriptiveStatistics:
    """Compute mean, median, sample std (ddof=1), Q1, Q3, IQR, min, max.
    
    Filters out non-finite (NaN, inf) values.
    """
    clean_vals = [float(v) for v in values if np.isfinite(v)]
    n = len(clean_vals)
    if n == 0:
        return DescriptiveStatistics.empty()

    arr = np.array(clean_vals, dtype=np.float64)
    mean_val = float(np.mean(arr))
    median_val = float(np.median(arr))

    if n > 1:
        std_val = float(np.std(arr, ddof=ddof))
    else:
        std_val = 0.0

    q25 = float(np.percentile(arr, 25))
    q75 = float(np.percentile(arr, 75))
    iqr_val = float(q75 - q25)
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))

    return DescriptiveStatistics(
        count=n,
        mean=round(mean_val, 4),
        median=round(median_val, 4),
        std=round(std_val, 4),
        q25=round(q25, 4),
        q75=round(q75, 4),
        iqr=round(iqr_val, 4),
        min_val=round(min_val, 4),
        max_val=round(max_val, 4),
    )


def compute_paired_percentage_improvements(
    base_costs: Sequence[float],
    algo_costs: Sequence[float],
    min_base_cost_threshold: float = 1e-6,
) -> List[float]:
    """Compute paired percentage improvements: ((J_base - J_algo) / J_base) * 100.
    
    Positive value indicates improvement (reduction in cost J).
    Pairs with J_base <= threshold or non-finite costs are excluded.
    """
    improvements: List[float] = []
    for j_base, j_algo in zip(base_costs, algo_costs):
        if not (np.isfinite(j_base) and np.isfinite(j_algo)):
            continue
        if j_base <= min_base_cost_threshold:
            continue
        pct = ((j_base - j_algo) / j_base) * 100.0
        improvements.append(float(pct))
    return improvements


def evaluate_lexicographical_matchup(
    candidate: AlgorithmRunResult,
    competitor: AlgorithmRunResult,
    tolerance_j: float = 1e-4,
    tolerance_gap: float = 1e-4,
) -> int:
    """Evaluate head-to-head match under official competition lexicographical criteria:
    
    Hierarchy:
        1. Validity Dominance (True > False)
        2. Hub Count H (minimized: lower beats higher)
        3. Routing Cost J (minimized: lower beats higher within tolerance)
        4. Area Gap Delta A_gap (minimized: lower beats higher within tolerance)
    
    Returns:
        +1: Candidate wins
        -1: Competitor wins (Candidate loses)
         0: Tie
    """
    # 1. Validity Dominance
    if not candidate.is_valid and not competitor.is_valid:
        return 0
    if candidate.is_valid and not competitor.is_valid:
        return 1
    if not candidate.is_valid and competitor.is_valid:
        return -1

    # 2. Hub Count H
    if candidate.h_count < competitor.h_count:
        return 1
    if candidate.h_count > competitor.h_count:
        return -1

    # 3. Routing Cost J
    delta_j = competitor.total_cost_J - candidate.total_cost_J
    if delta_j > tolerance_j:
        return 1
    if delta_j < -tolerance_j:
        return -1

    # 4. Area Gap Delta A_gap
    delta_gap = competitor.area_gap_m2 - candidate.area_gap_m2
    if delta_gap > tolerance_gap:
        return 1
    if delta_gap < -tolerance_gap:
        return -1

    # Exact Tie
    return 0


def run_paired_hypothesis_tests(
    base_costs: Sequence[float],
    algo_costs: Sequence[float],
) -> Tuple[HypothesisTestResult, HypothesisTestResult]:
    """Execute paired Student's t-test and Wilcoxon signed-rank test on matched cost pairs.
    
    Differences are computed as d = base_costs - algo_costs.
    A positive d indicates algo is superior (lower cost).
    
    Returns:
        (t_test_result, wilcoxon_test_result)
    """
    clean_pairs = [
        (float(b), float(a))
        for b, a in zip(base_costs, algo_costs)
        if np.isfinite(b) and np.isfinite(a)
    ]
    n = len(clean_pairs)
    if n < 2:
        empty_t = HypothesisTestResult(
            test_name="Paired Student's t-test",
            statistic=0.0,
            p_value=1.0,
            is_significant_05=False,
            is_significant_01=False,
            sample_size=n,
            note="Insufficient samples (n < 2)",
        )
        empty_w = HypothesisTestResult(
            test_name="Wilcoxon signed-rank test",
            statistic=0.0,
            p_value=1.0,
            is_significant_05=False,
            is_significant_01=False,
            sample_size=n,
            note="Insufficient samples (n < 2)",
        )
        return empty_t, empty_w

    b_arr = np.array([p[0] for p in clean_pairs], dtype=np.float64)
    a_arr = np.array([p[1] for p in clean_pairs], dtype=np.float64)
    diffs = b_arr - a_arr

    # Paired Student's t-test
    if np.allclose(diffs, 0.0, atol=1e-7) or np.std(diffs, ddof=1) < 1e-9:
        t_stat, t_pval = 0.0, 1.0
        t_note = "Zero variance across paired differences"
    else:
        try:
            t_res = stats.ttest_rel(b_arr, a_arr)
            t_stat = float(t_res.statistic) if not math.isnan(t_res.statistic) else 0.0
            t_pval = float(t_res.pvalue) if not math.isnan(t_res.pvalue) else 1.0
            t_note = ""
        except Exception as e:
            t_stat, t_pval = 0.0, 1.0
            t_note = f"Exception in t-test: {e}"

    t_result = HypothesisTestResult(
        test_name="Paired Student's t-test",
        statistic=round(t_stat, 4),
        p_value=float(t_pval),
        is_significant_05=(t_pval < 0.05),
        is_significant_01=(t_pval < 0.01),
        sample_size=n,
        note=t_note,
    )

    # Wilcoxon signed-rank test
    non_zero_diffs = diffs[np.abs(diffs) > 1e-7]
    if len(non_zero_diffs) < 1:
        w_stat, w_pval = 0.0, 1.0
        w_note = "All paired differences are zero within tolerance"
    else:
        try:
            w_res = stats.wilcoxon(b_arr, a_arr, zero_method="wilcox")
            w_stat = float(w_res.statistic) if not math.isnan(w_res.statistic) else 0.0
            w_pval = float(w_res.pvalue) if not math.isnan(w_res.pvalue) else 1.0
            w_note = ""
        except Exception as e:
            w_stat, w_pval = 0.0, 1.0
            w_note = f"Exception in Wilcoxon test: {e}"

    w_result = HypothesisTestResult(
        test_name="Wilcoxon signed-rank test",
        statistic=round(w_stat, 4),
        p_value=float(w_pval),
        is_significant_05=(w_pval < 0.05),
        is_significant_01=(w_pval < 0.01),
        sample_size=n,
        note=w_note,
    )

    return t_result, w_result


def bootstrap_confidence_interval(
    data: Sequence[float],
    num_bootstrap: int = 2000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
    metric_name: str = "mean",
) -> BootstrapCIResult:
    """Compute non-parametric empirical percentile bootstrap confidence interval.
    
    Default B = 2,000 resamples for high-precision 95% CI estimation.
    """
    clean_vals = [float(x) for x in data if np.isfinite(x)]
    n = len(clean_vals)
    if n == 0:
        return BootstrapCIResult(
            metric_name=metric_name,
            observed_mean=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            confidence_level=confidence_level,
            num_resamples=num_bootstrap,
            standard_error=0.0,
        )

    arr = np.array(clean_vals, dtype=np.float64)
    obs_mean = float(np.mean(arr))

    if n == 1 or np.allclose(arr, arr[0], atol=1e-7):
        return BootstrapCIResult(
            metric_name=metric_name,
            observed_mean=round(obs_mean, 4),
            ci_lower=round(obs_mean, 4),
            ci_upper=round(obs_mean, 4),
            confidence_level=confidence_level,
            num_resamples=num_bootstrap,
            standard_error=0.0,
        )

    rng = np.random.RandomState(random_seed)
    indices = rng.randint(0, n, size=(num_bootstrap, n))
    bootstrap_means = np.mean(arr[indices], axis=1)

    alpha = 1.0 - confidence_level
    lower_pct = (alpha / 2.0) * 100.0
    upper_pct = (1.0 - alpha / 2.0) * 100.0

    ci_lower = float(np.percentile(bootstrap_means, lower_pct))
    ci_upper = float(np.percentile(bootstrap_means, upper_pct))
    se = float(np.std(bootstrap_means, ddof=1))

    return BootstrapCIResult(
        metric_name=metric_name,
        observed_mean=round(obs_mean, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        confidence_level=confidence_level,
        num_resamples=num_bootstrap,
        standard_error=round(se, 4),
    )


# ============================================================================
# StatisticalEvaluator Engine
# ============================================================================

class StatisticalEvaluator:
    """Production evaluation engine computing full-partition and feasible-subset metrics."""

    def __init__(
        self,
        num_bootstrap: int = 2000,
        confidence_level: float = 0.95,
        random_seed: int = 42,
        tolerance_j: float = 1e-4,
        tolerance_gap: float = 1e-4,
    ) -> None:
        self.num_bootstrap = num_bootstrap
        self.confidence_level = confidence_level
        self.random_seed = random_seed
        self.tolerance_j = tolerance_j
        self.tolerance_gap = tolerance_gap

    def evaluate_algorithm_profile(
        self,
        records: Sequence[PlanBenchmarkRecord],
        algorithm_name: str,
    ) -> AlgorithmFullProfile:
        """Compute full algorithm profile with clear separation of total vs feasible runs."""
        total_runs = len(records)
        runs: List[AlgorithmRunResult] = []
        for r in records:
            res = r.results.get(algorithm_name)
            if res is not None:
                runs.append(res)

        valid_runs = [res for res in runs if res.is_valid]
        valid_count = len(valid_runs)
        validity_pct = round((valid_count / max(1, total_runs)) * 100.0, 2)

        runtimes = [res.runtime_s for res in runs]
        runtime_stats = compute_descriptive_stats(runtimes)

        if valid_count > 0:
            j_stats = compute_descriptive_stats([res.total_cost_J for res in valid_runs])
            h_stats = compute_descriptive_stats([float(res.h_count) for res in valid_runs])
            l_stats = compute_descriptive_stats([res.total_length_m for res in valid_runs])
            b_stats = compute_descriptive_stats([float(res.total_bends) for res in valid_runs])
            x_stats = compute_descriptive_stats([float(res.total_crossings) for res in valid_runs])
            o_stats = compute_descriptive_stats([res.total_overlap_m for res in valid_runs])
            gap_stats = compute_descriptive_stats([res.area_gap_m2 for res in valid_runs])
        else:
            j_stats = DescriptiveStatistics.empty()
            h_stats = DescriptiveStatistics.empty()
            l_stats = DescriptiveStatistics.empty()
            b_stats = DescriptiveStatistics.empty()
            x_stats = DescriptiveStatistics.empty()
            o_stats = DescriptiveStatistics.empty()
            gap_stats = DescriptiveStatistics.empty()

        return AlgorithmFullProfile(
            algorithm_name=algorithm_name,
            total_runs=total_runs,
            valid_runs=valid_count,
            validity_rate_pct=validity_pct,
            cost_J=j_stats,
            hubs_H=h_stats,
            length_L=l_stats,
            bends_B=b_stats,
            crossings_X=x_stats,
            overlap_O=o_stats,
            area_gap=gap_stats,
            runtime_s=runtime_stats,
        )

    def evaluate_paired_matchup(
        self,
        records: Sequence[PlanBenchmarkRecord],
        candidate_name: str,
        baseline_name: str,
    ) -> PairedEvaluationResult:
        """Perform paired comparison separating full partition vs feasible subset.
        
        - Full Partition Record: Evaluates competition outcomes across all plans (Validity > H > J > Delta A_gap).
        - Feasible Subset Record: Evaluates outcomes strictly on mutually valid pairs.
        - Continuous Statistical Metrics: Evaluated on mutually valid pairs (Delta J, % improvement, t-test, Wilcoxon, B=2000 bootstrap CI).
        """
        total_pairs = len(records)
        full_wins = 0
        full_ties = 0
        full_losses = 0

        feas_wins = 0
        feas_ties = 0
        feas_losses = 0

        base_costs: List[float] = []
        cand_costs: List[float] = []
        delta_j_list: List[float] = []

        for r in records:
            c_res = r.results.get(candidate_name)
            b_res = r.results.get(baseline_name)
            if c_res is None or b_res is None:
                continue

            # Full partition lexicographical outcome
            outcome = evaluate_lexicographical_matchup(
                candidate=c_res,
                competitor=b_res,
                tolerance_j=self.tolerance_j,
                tolerance_gap=self.tolerance_gap,
            )
            if outcome > 0:
                full_wins += 1
            elif outcome < 0:
                full_losses += 1
            else:
                full_ties += 1

            # Feasible subset check
            if c_res.is_valid and b_res.is_valid:
                # Feasible lexicographical outcome
                if outcome > 0:
                    feas_wins += 1
                elif outcome < 0:
                    feas_losses += 1
                else:
                    feas_ties += 1

                b_j = c_res.total_cost_J  # wait! let's be careful! b_res is baseline, c_res is candidate!
                # candidate cost = c_res.total_cost_J, baseline cost = b_res.total_cost_J
                cand_costs.append(c_res.total_cost_J)
                base_costs.append(b_res.total_cost_J)
                delta_j = b_res.total_cost_J - c_res.total_cost_J
                delta_j_list.append(delta_j)

        feasible_pairs_count = len(delta_j_list)
        pct_imp_list = compute_paired_percentage_improvements(base_costs, cand_costs)

        full_record = WinTieLossRecord.from_counts(wins=full_wins, ties=full_ties, losses=full_losses)
        feas_record = WinTieLossRecord.from_counts(wins=feas_wins, ties=feas_ties, losses=feas_losses)

        delta_j_stats = compute_descriptive_stats(delta_j_list)
        pct_imp_stats = compute_descriptive_stats(pct_imp_list)

        t_test, wilcoxon = run_paired_hypothesis_tests(base_costs, cand_costs)

        ci_delta_j = bootstrap_confidence_interval(
            delta_j_list,
            num_bootstrap=self.num_bootstrap,
            confidence_level=self.confidence_level,
            random_seed=self.random_seed,
            metric_name="mean_delta_J",
        )
        ci_pct_imp = bootstrap_confidence_interval(
            pct_imp_list,
            num_bootstrap=self.num_bootstrap,
            confidence_level=self.confidence_level,
            random_seed=self.random_seed,
            metric_name="mean_pct_improvement",
        )

        return PairedEvaluationResult(
            candidate_name=candidate_name,
            baseline_name=baseline_name,
            total_pairs=total_pairs,
            feasible_pairs=feasible_pairs_count,
            full_lexicographical_record=full_record,
            feasible_lexicographical_record=feas_record,
            delta_J_stats=delta_j_stats,
            pct_improvement_stats=pct_imp_stats,
            paired_t_test=t_test,
            wilcoxon_signed_rank_test=wilcoxon,
            bootstrap_ci_delta_J=ci_delta_j,
            bootstrap_ci_pct_improvement=ci_pct_imp,
        )

    def evaluate_stratified_tiers(
        self,
        records: Sequence[PlanBenchmarkRecord],
        algorithm_names: Sequence[str],
        baseline_name: str = "PortfolioHybrid",
    ) -> Dict[str, TierEvaluationResult]:
        """Stratify results by complexity tier (Compact: <=3, Standard: 4-5, Complex: >=6)."""
        total_plans = max(1, len(records))
        tiers = [ComplexityTier.COMPACT.value, ComplexityTier.STANDARD.value, ComplexityTier.COMPLEX.value]
        stratified: Dict[str, TierEvaluationResult] = {}

        for t in tiers:
            sub_records = [r for r in records if r.tier == t]
            p_count = len(sub_records)
            pct = round((p_count / total_plans) * 100.0, 2)

            algo_profiles: Dict[str, AlgorithmFullProfile] = {}
            pair_results: Dict[str, PairedEvaluationResult] = {}

            if p_count > 0:
                for algo in algorithm_names:
                    algo_profiles[algo] = self.evaluate_algorithm_profile(sub_records, algo)

                for algo in algorithm_names:
                    if algo != baseline_name:
                        key = f"{algo}_vs_{baseline_name}"
                        pair_results[key] = self.evaluate_paired_matchup(
                            records=sub_records,
                            candidate_name=algo,
                            baseline_name=baseline_name,
                        )

            stratified[t] = TierEvaluationResult(
                tier=t,
                plan_count=p_count,
                pct_of_total=pct,
                algorithms=algo_profiles,
                pairwise_vs_baseline=pair_results,
            )

        return stratified

    def evaluate(
        self,
        records: Sequence[PlanBenchmarkRecord],
        algorithm_names: Sequence[str],
        baseline_name: str = "PortfolioHybrid",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ComprehensiveStatisticalReport:
        """Generate comprehensive statistical evaluation across overall dataset and all tiers."""
        # 1. Overall algorithm profiles
        algos_overall: Dict[str, AlgorithmFullProfile] = {}
        for algo in algorithm_names:
            algos_overall[algo] = self.evaluate_algorithm_profile(records, algo)

        # 2. Overall pairwise comparisons vs baseline
        pairs_overall: Dict[str, PairedEvaluationResult] = {}
        for algo in algorithm_names:
            if algo != baseline_name:
                key = f"{algo}_vs_{baseline_name}"
                pairs_overall[key] = self.evaluate_paired_matchup(
                    records=records,
                    candidate_name=algo,
                    baseline_name=baseline_name,
                )

        # Also evaluate key secondary pairwise matchups if present
        secondary_pairs = [
            ("LinUCB_ALNS", "ALNS_Bandit"),
            ("PPO_ALNS", "ALNS_Bandit"),
            ("PPO_ALNS", "LinUCB_ALNS"),
            ("ALNS_Bandit", "ALNS_Random"),
        ]
        for c1, c2 in secondary_pairs:
            if c1 in algorithm_names and c2 in algorithm_names:
                key = f"{c1}_vs_{c2}"
                pairs_overall[key] = self.evaluate_paired_matchup(
                    records=records,
                    candidate_name=c1,
                    baseline_name=c2,
                )

        # 3. Stratified tiers
        stratified_tiers = self.evaluate_stratified_tiers(
            records=records,
            algorithm_names=algorithm_names,
            baseline_name=baseline_name,
        )

        return ComprehensiveStatisticalReport(
            total_plans=len(records),
            baseline_name=baseline_name,
            algorithms_overall=algos_overall,
            pairwise_overall=pairs_overall,
            stratified_tiers=stratified_tiers,
            metadata=dict(metadata or {}),
        )

    def evaluate_paired_arrays(
        self,
        candidate_costs: Sequence[float],
        baseline_costs: Sequence[float],
        candidate_name: str = "Candidate",
        baseline_name: str = "Baseline",
    ) -> PairedEvaluationResult:
        """Convenience method for evaluating raw paired cost arrays."""
        cand_arr = [float(c) for c in candidate_costs]
        base_arr = [float(b) for b in baseline_costs]
        n = min(len(cand_arr), len(base_arr))
        cand_arr = cand_arr[:n]
        base_arr = base_arr[:n]

        records: List[PlanBenchmarkRecord] = []
        for i, (c_cost, b_cost) in enumerate(zip(cand_arr, base_arr)):
            rec = PlanBenchmarkRecord(
                plan_id=i,
                tier="standard",
                eligible_rooms=4,
                total_area_m2=100.0,
                results={
                    candidate_name: AlgorithmRunResult(
                        algorithm_name=candidate_name,
                        h_count=2,
                        total_cost_J=c_cost,
                        is_valid=np.isfinite(c_cost),
                    ),
                    baseline_name: AlgorithmRunResult(
                        algorithm_name=baseline_name,
                        h_count=2,
                        total_cost_J=b_cost,
                        is_valid=np.isfinite(b_cost),
                    ),
                },
            )
            records.append(rec)

        return self.evaluate_paired_matchup(
            records=records,
            candidate_name=candidate_name,
            baseline_name=baseline_name,
        )


def format_markdown_summary(report: ComprehensiveStatisticalReport) -> str:
    """Generate high-density Markdown summary tables from a statistical report."""
    lines: List[str] = [
        f"# Statistical Benchmark Evaluation Report",
        f"",
        f"- **Total Evaluated Plans:** {report.total_plans}",
        f"- **Benchmark Baseline:** `{report.baseline_name}`",
        f"",
        f"## 1. Algorithm Profiles (Separating Full Partition vs Feasible Subset)",
        f"",
        f"| Algorithm | Total Runs | Valid Runs | Validity Rate | Mean J (Feas) | Median J (Feas) | Std J (Feas) | IQR J (Feas) | Mean H | Mean Runtime (s) |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for name, prof in report.algorithms_overall.items():
        lines.append(
            f"| **{name}** | {prof.total_runs} | {prof.valid_runs} | {prof.validity_rate_pct:.1f}% | "
            f"{prof.cost_J.mean:.2f} | {prof.cost_J.median:.2f} | {prof.cost_J.std:.2f} | {prof.cost_J.iqr:.2f} | "
            f"{prof.hubs_H.mean:.2f} | {prof.runtime_s.mean:.3f} |"
        )

    lines.extend([
        f"",
        f"## 2. Head-to-Head Lexicographical Standings (Validity > H > J > Delta A_gap)",
        f"",
        f"### Full Partition (All Instances, Infeasibility Punished)",
        f"| Matchup | Pairs | Wins | Ties | Losses | Win Rate (%) | Non-Loss Rate (%) |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for key, pair in report.pairwise_overall.items():
        rec = pair.full_lexicographical_record
        lines.append(
            f"| `{key}` | {rec.total_matchups} | {rec.wins} | {rec.ties} | {rec.losses} | "
            f"**{rec.win_rate_pct:.1f}%** | {rec.non_loss_rate_pct:.1f}% |"
        )

    lines.extend([
        f"",
        f"### Feasible Subset (Mutually Valid Topologies Only)",
        f"| Matchup | Valid Pairs | Wins | Ties | Losses | Win Rate (%) | Non-Loss Rate (%) |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for key, pair in report.pairwise_overall.items():
        rec = pair.feasible_lexicographical_record
        lines.append(
            f"| `{key}` | {rec.total_matchups} | {rec.wins} | {rec.ties} | {rec.losses} | "
            f"**{rec.win_rate_pct:.1f}%** | {rec.non_loss_rate_pct:.1f}% |"
        )

    lines.extend([
        f"",
        f"## 3. Paired Significance Testing & Non-Parametric Bootstrap 95% CIs (B=2,000)",
        f"",
        f"| Matchup | Mean Delta J | Median Delta J | Mean % Imp | Student t p-val | Wilcoxon p-val | Bootstrap 95% CI (Delta J) | Bootstrap 95% CI (% Imp) |",
        f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for key, pair in report.pairwise_overall.items():
        t_p = f"{pair.paired_t_test.p_value:.4e}" if pair.paired_t_test.p_value < 0.0001 else f"{pair.paired_t_test.p_value:.4f}"
        w_p = f"{pair.wilcoxon_signed_rank_test.p_value:.4e}" if pair.wilcoxon_signed_rank_test.p_value < 0.0001 else f"{pair.wilcoxon_signed_rank_test.p_value:.4f}"
        ci_j = f"[{pair.bootstrap_ci_delta_J.ci_lower:.2f}, {pair.bootstrap_ci_delta_J.ci_upper:.2f}]"
        ci_pct = f"[{pair.bootstrap_ci_pct_improvement.ci_lower:.2f}%, {pair.bootstrap_ci_pct_improvement.ci_upper:.2f}%]"
        lines.append(
            f"| `{key}` | {pair.delta_J_stats.mean:+.2f} | {pair.delta_J_stats.median:+.2f} | "
            f"{pair.pct_improvement_stats.mean:+.2f}% | {t_p} | {w_p} | {ci_j} | {ci_pct} |"
        )

    lines.extend([
        f"",
        f"## 4. Stratified Breakdown Across Complexity Tiers",
        f"",
    ])

    for t_name, t_res in report.stratified_tiers.items():
        lines.extend([
            f"### Tier: `{t_name.upper()}` ({t_res.plan_count} plans, {t_res.pct_of_total:.1f}% of dataset)",
            f"",
            f"| Algorithm | Valid Runs | Mean J (Feas) | Median J (Feas) | IQR J | Full Win Rate vs Base | Feas Win Rate vs Base |",
            f"| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ])
        for algo_name, prof in t_res.algorithms.items():
            pair_key = f"{algo_name}_vs_{report.baseline_name}"
            if pair_key in t_res.pairwise_vs_baseline:
                p_res = t_res.pairwise_vs_baseline[pair_key]
                f_win = f"{p_res.full_lexicographical_record.win_rate_pct:.1f}%"
                feas_win = f"{p_res.feasible_lexicographical_record.win_rate_pct:.1f}%"
            elif algo_name == report.baseline_name:
                f_win = "-- (Baseline)"
                feas_win = "-- (Baseline)"
            else:
                f_win = "N/A"
                feas_win = "N/A"

            lines.append(
                f"| **{algo_name}** | {prof.valid_runs}/{prof.total_runs} | "
                f"{prof.cost_J.mean:.2f} | {prof.cost_J.median:.2f} | {prof.cost_J.iqr:.2f} | "
                f"{f_win} | {feas_win} |"
            )
        lines.append("")

    return "\n".join(lines)

