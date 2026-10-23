"""Rigorously Formulated Statistical Testing Suite for Post-Champion Research (Agent 5).

Implements:
1. Wilcoxon Signed-Rank Test (Non-parametric paired comparison for Delta J and Delta H).
2. Paired Student's t-test (Parametric counterpart).
3. Non-parametric Empirical Bootstrap (B=2,000 resamples for 95% Confidence Intervals).
4. Effect size estimation: Cohen's d_z and Wilcoxon Rank-Biserial correlation r.
5. Complexity Tier Stratification (Compact, Standard, Complex).
6. Lexicographical Win/Tie/Loss and Feasibility Maintenance analysis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.stats as stats

from benchmarks.experimental.protocol import MatchupOutcome, PairedPlanComparison


@dataclass
class BootstrapCI:
    """95% Confidence Interval computed via empirical bootstrap."""
    metric_name: str
    sample_mean: float
    sample_median: float
    ci_lower: float
    ci_upper: float
    bootstrap_iterations: int = 2000
    confidence_level: float = 0.95

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HypothesisTestResult:
    """Statistical hypothesis test outcome."""
    test_name: str
    statistic: float
    p_value: float
    significant_p05: bool
    significant_p01: bool
    effect_size: float
    effect_size_type: str  # 'cohen_dz' or 'rank_biserial_r'
    interpretation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StratumEvaluationSummary:
    """Summary of paired evaluation within a specific stratum (or overall)."""
    tier: str
    total_plans: int
    mutually_valid_plans: int
    champ_valid_count: int
    cand_valid_count: int
    cand_feasibility_rate_pct: float
    champ_feasibility_rate_pct: float
    
    # Lexicographical matchup
    wins: int
    ties: int
    losses: int
    win_rate_pct: float
    tie_rate_pct: float
    loss_rate_pct: float
    non_loss_rate_pct: float

    # Objective deltas (mutually valid plans)
    mean_delta_h: float
    mean_delta_j: float
    median_delta_j: float
    mean_pct_j_improvement: float
    median_pct_j_improvement: float
    mean_speedup: float
    median_speedup: float

    # Statistical tests
    wilcoxon_test: HypothesisTestResult
    t_test: HypothesisTestResult
    delta_j_ci: BootstrapCI
    pct_j_ci: BootstrapCI
    speedup_ci: BootstrapCI

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["wilcoxon_test"] = self.wilcoxon_test.to_dict()
        d["t_test"] = self.t_test.to_dict()
        d["delta_j_ci"] = self.delta_j_ci.to_dict()
        d["pct_j_ci"] = self.pct_j_ci.to_dict()
        d["speedup_ci"] = self.speedup_ci.to_dict()
        return d


def run_bootstrap_ci(
    values: Sequence[float],
    metric_name: str,
    n_resamples: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 42,
) -> BootstrapCI:
    """Compute empirical percentile bootstrap 95% confidence interval."""
    arr = np.array(values, dtype=np.float64)
    if len(arr) == 0:
        return BootstrapCI(
            metric_name=metric_name,
            sample_mean=0.0,
            sample_median=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            bootstrap_iterations=n_resamples,
            confidence_level=confidence_level,
        )

    sample_mean = float(np.mean(arr))
    sample_median = float(np.median(arr))

    if len(arr) == 1 or np.all(arr == arr[0]):
        return BootstrapCI(
            metric_name=metric_name,
            sample_mean=round(sample_mean, 4),
            sample_median=round(sample_median, 4),
            ci_lower=round(sample_mean, 4),
            ci_upper=round(sample_mean, 4),
            bootstrap_iterations=n_resamples,
            confidence_level=confidence_level,
        )

    rng = np.random.default_rng(seed)
    boot_indices = rng.integers(0, len(arr), size=(n_resamples, len(arr)))
    boot_means = np.mean(arr[boot_indices], axis=1)

    alpha = 1.0 - confidence_level
    lower_pct = (alpha / 2.0) * 100.0
    upper_pct = (1.0 - alpha / 2.0) * 100.0

    ci_lower = float(np.percentile(boot_means, lower_pct))
    ci_upper = float(np.percentile(boot_means, upper_pct))

    return BootstrapCI(
        metric_name=metric_name,
        sample_mean=round(sample_mean, 4),
        sample_median=round(sample_median, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        bootstrap_iterations=n_resamples,
        confidence_level=confidence_level,
    )


def run_paired_hypothesis_tests(
    champ_vals: Sequence[float],
    cand_vals: Sequence[float],
) -> Tuple[HypothesisTestResult, HypothesisTestResult]:
    """Execute Wilcoxon signed-rank and Paired Student's t-test.

    H0: Candidate costs are equal to or greater than Champion costs.
    H1: Candidate costs are significantly lower than Champion costs (Delta J > 0).
    """
    c_arr = np.array(champ_vals, dtype=np.float64)
    x_arr = np.array(cand_vals, dtype=np.float64)
    n = len(c_arr)

    if n < 2:
        empty_wilc = HypothesisTestResult(
            test_name="Wilcoxon Signed-Rank Test",
            statistic=0.0,
            p_value=1.0,
            significant_p05=False,
            significant_p01=False,
            effect_size=0.0,
            effect_size_type="rank_biserial_r",
            interpretation="Insufficient samples (N < 2)",
        )
        empty_t = HypothesisTestResult(
            test_name="Paired Student's t-test",
            statistic=0.0,
            p_value=1.0,
            significant_p05=False,
            significant_p01=False,
            effect_size=0.0,
            effect_size_type="cohen_dz",
            interpretation="Insufficient samples (N < 2)",
        )
        return empty_wilc, empty_t

    diffs = c_arr - x_arr  # Positive diffs = candidate is better

    # Wilcoxon signed-rank test
    non_zero = diffs[diffs != 0.0]
    if len(non_zero) == 0:
        wilcoxon_res = HypothesisTestResult(
            test_name="Wilcoxon Signed-Rank Test",
            statistic=0.0,
            p_value=1.0,
            significant_p05=False,
            significant_p01=False,
            effect_size=0.0,
            effect_size_type="rank_biserial_r",
            interpretation="Identical performance on all instances (Zero difference)",
        )
    else:
        try:
            w_res = stats.wilcoxon(c_arr, x_arr, alternative="two-sided")
            w_stat = float(w_res.statistic)
            w_pval = float(w_res.pvalue)

            # Rank-biserial correlation r
            # Sum of ranks for positive vs negative differences
            pos_diffs = diffs[diffs > 0]
            neg_diffs = diffs[diffs < 0]
            r_biserial = 0.0
            if len(non_zero) > 0:
                ranks = stats.rankdata(np.abs(non_zero))
                w_plus = np.sum(ranks[non_zero > 0])
                w_minus = np.sum(ranks[non_zero < 0])
                total_w = w_plus + w_minus
                if total_w > 0:
                    r_biserial = float((w_plus - w_minus) / total_w)

            wilcoxon_res = HypothesisTestResult(
                test_name="Wilcoxon Signed-Rank Test",
                statistic=round(w_stat, 4),
                p_value=round(w_pval, 6),
                significant_p05=w_pval < 0.05,
                significant_p01=w_pval < 0.01,
                effect_size=round(r_biserial, 4),
                effect_size_type="rank_biserial_r",
                interpretation=(
                    "Statistically significant improvement" if (w_pval < 0.05 and np.mean(diffs) > 0)
                    else "Statistically significant regression" if (w_pval < 0.05 and np.mean(diffs) < 0)
                    else "No statistically significant difference (p >= 0.05)"
                ),
            )
        except Exception as exc:
            wilcoxon_res = HypothesisTestResult(
                test_name="Wilcoxon Signed-Rank Test",
                statistic=0.0,
                p_value=1.0,
                significant_p05=False,
                significant_p01=False,
                effect_size=0.0,
                effect_size_type="rank_biserial_r",
                interpretation=f"Error in test: {exc}",
            )

    # Paired Student's t-test
    try:
        t_res = stats.ttest_rel(c_arr, x_arr)
        t_stat = float(t_res.statistic)
        t_pval = float(t_res.pvalue)

        mean_d = float(np.mean(diffs))
        std_d = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
        cohen_dz = (mean_d / std_d) if std_d > 1e-8 else 0.0

        t_test_res = HypothesisTestResult(
            test_name="Paired Student's t-test",
            statistic=round(t_stat, 4),
            p_value=round(t_pval, 6),
            significant_p05=t_pval < 0.05,
            significant_p01=t_pval < 0.01,
            effect_size=round(cohen_dz, 4),
            effect_size_type="cohen_dz",
            interpretation=(
                "Statistically significant improvement" if (t_pval < 0.05 and mean_d > 0)
                else "Statistically significant regression" if (t_pval < 0.05 and mean_d < 0)
                else "No statistically significant difference (p >= 0.05)"
            ),
        )
    except Exception as exc:
        t_test_res = HypothesisTestResult(
            test_name="Paired Student's t-test",
            statistic=0.0,
            p_value=1.0,
            significant_p05=False,
            significant_p01=False,
            effect_size=0.0,
            effect_size_type="cohen_dz",
            interpretation=f"Error in t-test: {exc}",
        )

    return wilcoxon_res, t_test_res


def analyze_paired_comparisons(
    comparisons: Sequence[PairedPlanComparison],
    tier_name: str = "all",
) -> StratumEvaluationSummary:
    """Analyze a collection of paired plan comparisons."""
    total = len(comparisons)
    if total == 0:
        empty_wilc, empty_t = run_paired_hypothesis_tests([], [])
        empty_ci = run_bootstrap_ci([], "empty")
        return StratumEvaluationSummary(
            tier=tier_name,
            total_plans=0,
            mutually_valid_plans=0,
            champ_valid_count=0,
            cand_valid_count=0,
            cand_feasibility_rate_pct=0.0,
            champ_feasibility_rate_pct=0.0,
            wins=0,
            ties=0,
            losses=0,
            win_rate_pct=0.0,
            tie_rate_pct=0.0,
            loss_rate_pct=0.0,
            non_loss_rate_pct=0.0,
            mean_delta_h=0.0,
            mean_delta_j=0.0,
            median_delta_j=0.0,
            mean_pct_j_improvement=0.0,
            median_pct_j_improvement=0.0,
            mean_speedup=0.0,
            median_speedup=0.0,
            wilcoxon_test=empty_wilc,
            t_test=empty_t,
            delta_j_ci=empty_ci,
            pct_j_ci=empty_ci,
            speedup_ci=empty_ci,
        )

    champ_valid = sum(1 for c in comparisons if c.champion.is_valid)
    cand_valid = sum(1 for c in comparisons if c.candidate.is_valid)
    mutually_valid = [c for c in comparisons if c.both_valid]

    wins = sum(1 for c in comparisons if c.outcome == MatchupOutcome.WIN)
    ties = sum(1 for c in comparisons if c.outcome == MatchupOutcome.TIE)
    losses = sum(1 for c in comparisons if c.outcome == MatchupOutcome.LOSS)

    win_rate = round((wins / total) * 100.0, 2)
    tie_rate = round((ties / total) * 100.0, 2)
    loss_rate = round((losses / total) * 100.0, 2)
    non_loss_rate = round(((wins + ties) / total) * 100.0, 2)

    cand_feas_pct = round((cand_valid / total) * 100.0, 2)
    champ_feas_pct = round((champ_valid / total) * 100.0, 2)

    if mutually_valid:
        delta_h_vals = [c.delta_h for c in mutually_valid]
        delta_j_vals = [c.delta_j for c in mutually_valid]
        pct_j_vals = [c.pct_j_improvement for c in mutually_valid]
        speedup_vals = [c.runtime_speedup for c in mutually_valid]

        champ_costs = [c.champion.cost_J for c in mutually_valid]
        cand_costs = [c.candidate.cost_J for c in mutually_valid]

        mean_dh = float(np.mean(delta_h_vals))
        mean_dj = float(np.mean(delta_j_vals))
        med_dj = float(np.median(delta_j_vals))
        mean_pct_j = float(np.mean(pct_j_vals))
        med_pct_j = float(np.median(pct_j_vals))
        mean_spd = float(np.mean(speedup_vals))
        med_spd = float(np.median(speedup_vals))

        wilc, tst = run_paired_hypothesis_tests(champ_costs, cand_costs)
        ci_dj = run_bootstrap_ci(delta_j_vals, "delta_j")
        ci_pct = run_bootstrap_ci(pct_j_vals, "pct_j_improvement")
        ci_spd = run_bootstrap_ci(speedup_vals, "runtime_speedup")
    else:
        mean_dh = 0.0
        mean_dj = 0.0
        med_dj = 0.0
        mean_pct_j = 0.0
        med_pct_j = 0.0
        mean_spd = 0.0
        med_spd = 0.0
        wilc, tst = run_paired_hypothesis_tests([], [])
        ci_dj = run_bootstrap_ci([], "delta_j")
        ci_pct = run_bootstrap_ci([], "pct_j_improvement")
        ci_spd = run_bootstrap_ci([], "runtime_speedup")

    return StratumEvaluationSummary(
        tier=tier_name,
        total_plans=total,
        mutually_valid_plans=len(mutually_valid),
        champ_valid_count=champ_valid,
        cand_valid_count=cand_valid,
        cand_feasibility_rate_pct=cand_feas_pct,
        champ_feasibility_rate_pct=champ_feas_pct,
        wins=wins,
        ties=ties,
        losses=losses,
        win_rate_pct=win_rate,
        tie_rate_pct=tie_rate,
        loss_rate_pct=loss_rate,
        non_loss_rate_pct=non_loss_rate,
        mean_delta_h=round(mean_dh, 4),
        mean_delta_j=round(mean_dj, 4),
        median_delta_j=round(med_dj, 4),
        mean_pct_j_improvement=round(mean_pct_j, 4),
        median_pct_j_improvement=round(med_pct_j, 4),
        mean_speedup=round(mean_spd, 4),
        median_speedup=round(med_spd, 4),
        wilcoxon_test=wilc,
        t_test=tst,
        delta_j_ci=ci_dj,
        pct_j_ci=ci_pct,
        speedup_ci=ci_spd,
    )
