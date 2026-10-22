"""Statistical metrics and paired analysis for Honeywell 5-way benchmark framework.

Computes:
- Primary and secondary objectives: H (Hub count), J (Routing cost), L (Length),
  B (Bends), X (Crossings), O (Overlaps), Area Gap, Validity, and Runtime.
- Competition Lexicographical Rule: Validity dominant, then H (minimized), then J (minimized).
- Head-to-head Win / Tie / Loss counts and win rates.
- Cost reduction Delta J (J_competitor - J_candidate) and percentage improvements.
- Rigorous statistical hypothesis testing: Paired Student's t-test and Wilcoxon signed-rank test.
- Non-parametric empirical bootstrap 95% confidence intervals (BCa/percentile).
- Stratification across Complexity Tiers: Compact (<=3), Standard (4-5), Complex (>=6).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.stats as stats

from benchmarks.framework.config import ComplexityTier


@dataclass
class AlgorithmRunResult:
    """Metrics from a single algorithm run on one floor plan."""
    algorithm_name: str
    h_count: int = 0
    total_cost_J: float = float("inf")
    total_length_m: float = 0.0
    total_bends: int = 0
    total_crossings: int = 0
    total_overlap_m: float = 0.0
    area_gap_m2: float = 0.0
    is_valid: bool = False
    solver_status: str = "UNKNOWN"
    runtime_s: float = 0.0
    diagnostic_info: Dict[str, Any] = field(default_factory=dict)
    violations: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> AlgorithmRunResult:
        """Create from dictionary with fallback defaults."""
        return cls(
            algorithm_name=d.get("algorithm_name", "Unknown"),
            h_count=int(d.get("h_count", 0)),
            total_cost_J=float(d.get("total_cost_J", float("inf"))),
            total_length_m=float(d.get("total_length_m", 0.0)),
            total_bends=int(d.get("total_bends", 0)),
            total_crossings=int(d.get("total_crossings", 0)),
            total_overlap_m=float(d.get("total_overlap_m", 0.0)),
            area_gap_m2=float(d.get("area_gap_m2", 0.0)),
            is_valid=bool(d.get("is_valid", False)),
            solver_status=str(d.get("solver_status", "UNKNOWN")),
            runtime_s=float(d.get("runtime_s", 0.0)),
            diagnostic_info=dict(d.get("diagnostic_info", {})),
            violations=list(d.get("violations", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanBenchmarkRecord:
    """Complete paired benchmark results for one floor plan across all algorithms."""
    plan_id: int
    tier: str
    eligible_rooms: int
    total_area_m2: float
    results: Dict[str, AlgorithmRunResult]

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PlanBenchmarkRecord:
        res_map: Dict[str, AlgorithmRunResult] = {}
        for algo, r in d.get("results", {}).items():
            if isinstance(r, AlgorithmRunResult):
                res_map[algo] = r
            elif isinstance(r, dict):
                res_map[algo] = AlgorithmRunResult.from_dict(r)
        return cls(
            plan_id=int(d["plan_id"]),
            tier=str(d.get("tier", "standard")),
            eligible_rooms=int(d.get("eligible_rooms", 0)),
            total_area_m2=float(d.get("total_area_m2", 0.0)),
            results=res_map,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "tier": self.tier,
            "eligible_rooms": self.eligible_rooms,
            "total_area_m2": self.total_area_m2,
            "results": {algo: r.to_dict() for algo, r in self.results.items()},
        }


@dataclass
class AlgorithmSummary:
    """Aggregate statistical summary for one algorithm across evaluated floor plans."""
    algo_name: str
    total_runs: int
    valid_count: int
    validity_rate_pct: float
    mean_H: float = 0.0
    median_H: float = 0.0
    mean_J: float = 0.0
    median_J: float = 0.0
    std_J: float = 0.0
    min_J: float = 0.0
    max_J: float = 0.0
    mean_L: float = 0.0
    mean_B: float = 0.0
    mean_X: float = 0.0
    mean_O: float = 0.0
    mean_area_gap: float = 0.0
    mean_runtime_s: float = 0.0
    median_runtime_s: float = 0.0
    total_runtime_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PairedComparisonResult:
    """Head-to-head statistical comparison between candidate algorithm and competitor."""
    candidate: str
    competitor: str
    total_pairs: int
    valid_pairs: int
    candidate_wins: int
    competitor_wins: int
    ties: int
    candidate_win_rate_pct: float
    candidate_non_loss_rate_pct: float
    mean_delta_J: float  # competitor.J - candidate.J (positive = candidate is better)
    median_delta_J: float
    mean_pct_improvement: float
    ttest_statistic: float
    ttest_p_value: float
    wilcoxon_statistic: float
    wilcoxon_p_value: float
    bootstrap_ci_95_delta_J: Tuple[float, float]
    bootstrap_ci_95_pct_improvement: Tuple[float, float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TierSummary:
    """Performance metrics stratified by floor plan complexity tier."""
    tier: str
    plan_count: int
    pct_of_dataset: float
    algorithms: Dict[str, AlgorithmSummary] = field(default_factory=dict)
    pairwise_vs_baseline: Dict[str, PairedComparisonResult] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "plan_count": self.plan_count,
            "pct_of_dataset": self.pct_of_dataset,
            "algorithms": {k: v.to_dict() for k, v in self.algorithms.items()},
            "pairwise_vs_baseline": {k: v.to_dict() for k, v in self.pairwise_vs_baseline.items()},
        }


@dataclass
class BenchmarkMetricsReport:
    """Master benchmark analysis report containing overall and stratified statistics."""
    total_plans: int
    algorithms: Dict[str, AlgorithmSummary]
    pairwise: Dict[str, PairedComparisonResult]
    tiers: Dict[str, TierSummary]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_plans": self.total_plans,
            "metadata": self.metadata,
            "algorithms": {k: v.to_dict() for k, v in self.algorithms.items()},
            "pairwise": {k: v.to_dict() for k, v in self.pairwise.items()},
            "tiers": {k: v.to_dict() for k, v in self.tiers.items()},
        }


# ============================================================================
# Statistical Computation Functions
# ============================================================================

def bootstrap_confidence_interval(
    data: Sequence[float],
    num_bootstrap: int = 1000,
    confidence_level: float = 0.95,
    random_seed: int = 42,
) -> Tuple[float, float]:
    """Compute non-parametric empirical percentile bootstrap confidence interval for mean."""
    arr = np.array([x for x in data if not (math.isnan(x) or math.isinf(x))], dtype=np.float64)
    if len(arr) == 0:
        return (0.0, 0.0)
    if len(arr) == 1 or np.all(arr == arr[0]):
        val = float(arr[0])
        return (round(val, 4), round(val, 4))

    rng = np.random.RandomState(random_seed)
    n = len(arr)
    # Resample with replacement
    indices = rng.randint(0, n, size=(num_bootstrap, n))
    bootstrap_means = np.mean(arr[indices], axis=1)

    alpha = 1.0 - confidence_level
    lower_pct = (alpha / 2.0) * 100.0
    upper_pct = (1.0 - alpha / 2.0) * 100.0

    ci_lower = float(np.percentile(bootstrap_means, lower_pct))
    ci_upper = float(np.percentile(bootstrap_means, upper_pct))
    return (round(ci_lower, 4), round(ci_upper, 4))


def run_paired_hypothesis_tests(
    candidate_costs: Sequence[float],
    competitor_costs: Sequence[float],
) -> Tuple[float, float, float, float]:
    """Execute paired Student's t-test and Wilcoxon signed-rank test on matched cost pairs.
    
    Returns:
        (ttest_statistic, ttest_p_value, wilcoxon_statistic, wilcoxon_p_value)
    """
    cand = np.array(candidate_costs, dtype=np.float64)
    comp = np.array(competitor_costs, dtype=np.float64)

    # Filter invalid/infinite
    mask = np.isfinite(cand) & np.isfinite(comp)
    cand_valid = cand[mask]
    comp_valid = comp[mask]

    n = len(cand_valid)
    if n < 2 or np.allclose(cand_valid, comp_valid, atol=1e-5):
        return (0.0, 1.0, 0.0, 1.0)

    # Paired Student's t-test
    try:
        t_res = stats.ttest_rel(comp_valid, cand_valid)
        t_stat = float(t_res.statistic) if not math.isnan(t_res.statistic) else 0.0
        t_pval = float(t_res.pvalue) if not math.isnan(t_res.pvalue) else 1.0
    except Exception:
        t_stat, t_pval = 0.0, 1.0

    # Wilcoxon signed-rank test
    diffs = comp_valid - cand_valid
    non_zero = diffs[np.abs(diffs) > 1e-6]
    if len(non_zero) < 1:
        w_stat, w_pval = 0.0, 1.0
    else:
        try:
            w_res = stats.wilcoxon(comp_valid, cand_valid, zero_method="wilcox")
            w_stat = float(w_res.statistic) if not math.isnan(w_res.statistic) else 0.0
            w_pval = float(w_res.pvalue) if not math.isnan(w_res.pvalue) else 1.0
        except Exception:
            w_stat, w_pval = 0.0, 1.0

    return (round(t_stat, 4), float(t_pval), round(w_stat, 4), float(w_pval))


def evaluate_lexicographical_matchup(
    candidate_res: AlgorithmRunResult,
    competitor_res: AlgorithmRunResult,
    tolerance: float = 1e-4,
) -> int:
    """Evaluate head-to-head outcome under competition lexicographical rules.
    
    Returns:
        +1: Candidate wins
        -1: Competitor wins
         0: Tie
    """
    # 1. Validity Dominance
    if not candidate_res.is_valid and not competitor_res.is_valid:
        return 0
    if candidate_res.is_valid and not competitor_res.is_valid:
        return 1
    if not candidate_res.is_valid and competitor_res.is_valid:
        return -1

    # 2. Primary Objective: Minimum Hubs (H)
    if candidate_res.h_count < competitor_res.h_count:
        return 1
    if candidate_res.h_count > competitor_res.h_count:
        return -1

    # 3. Secondary Objective: Routing Cost J (lower is better)
    delta_j = competitor_res.total_cost_J - candidate_res.total_cost_J
    if delta_j > tolerance:
        return 1
    if delta_j < -tolerance:
        return -1

    return 0


def compute_algorithm_summary(
    records: Sequence[PlanBenchmarkRecord],
    algo_name: str,
) -> AlgorithmSummary:
    """Compute aggregate descriptive statistics for one algorithm across plans."""
    total_runs = len(records)
    if total_runs == 0:
        return AlgorithmSummary(
            algo_name=algo_name,
            total_runs=0,
            valid_count=0,
            validity_rate_pct=0.0,
        )

    run_list = [r.results.get(algo_name) for r in records if algo_name in r.results]
    valid_runs = [r for r in run_list if r is not None and r.is_valid]
    valid_count = len(valid_runs)
    validity_rate = (valid_count / total_runs) * 100.0

    runtimes = [r.runtime_s for r in run_list if r is not None]

    if valid_count > 0:
        h_vals = [r.h_count for r in valid_runs]
        j_vals = [r.total_cost_J for r in valid_runs]
        l_vals = [r.total_length_m for r in valid_runs]
        b_vals = [r.total_bends for r in valid_runs]
        x_vals = [r.total_crossings for r in valid_runs]
        o_vals = [r.total_overlap_m for r in valid_runs]
        gap_vals = [r.area_gap_m2 for r in valid_runs]

        return AlgorithmSummary(
            algo_name=algo_name,
            total_runs=total_runs,
            valid_count=valid_count,
            validity_rate_pct=round(validity_rate, 2),
            mean_H=round(float(np.mean(h_vals)), 2),
            median_H=round(float(np.median(h_vals)), 2),
            mean_J=round(float(np.mean(j_vals)), 4),
            median_J=round(float(np.median(j_vals)), 4),
            std_J=round(float(np.std(j_vals)), 4),
            min_J=round(float(np.min(j_vals)), 4),
            max_J=round(float(np.max(j_vals)), 4),
            mean_L=round(float(np.mean(l_vals)), 2),
            mean_B=round(float(np.mean(b_vals)), 2),
            mean_X=round(float(np.mean(x_vals)), 2),
            mean_O=round(float(np.mean(o_vals)), 4),
            mean_area_gap=round(float(np.mean(gap_vals)), 4),
            mean_runtime_s=round(float(np.mean(runtimes)), 3) if runtimes else 0.0,
            median_runtime_s=round(float(np.median(runtimes)), 3) if runtimes else 0.0,
            total_runtime_s=round(float(np.sum(runtimes)), 2) if runtimes else 0.0,
        )

    return AlgorithmSummary(
        algo_name=algo_name,
        total_runs=total_runs,
        valid_count=0,
        validity_rate_pct=0.0,
        mean_runtime_s=round(float(np.mean(runtimes)), 3) if runtimes else 0.0,
        median_runtime_s=round(float(np.median(runtimes)), 3) if runtimes else 0.0,
        total_runtime_s=round(float(np.sum(runtimes)), 2) if runtimes else 0.0,
    )


def compute_paired_comparison(
    records: Sequence[PlanBenchmarkRecord],
    candidate_name: str,
    competitor_name: str,
    tolerance: float = 1e-4,
) -> PairedComparisonResult:
    """Perform paired head-to-head analysis between candidate and competitor algorithms."""
    total_pairs = len(records)
    cand_wins = 0
    comp_wins = 0
    ties = 0
    valid_pairs = 0

    delta_j_list: List[float] = []
    pct_imp_list: List[float] = []
    cand_costs: List[float] = []
    comp_costs: List[float] = []

    for r in records:
        c_res = r.results.get(candidate_name)
        comp_res = r.results.get(competitor_name)
        if c_res is None or comp_res is None:
            continue

        outcome = evaluate_lexicographical_matchup(c_res, comp_res, tolerance=tolerance)
        if outcome > 0:
            cand_wins += 1
        elif outcome < 0:
            comp_wins += 1
        else:
            ties += 1

        if c_res.is_valid and comp_res.is_valid:
            valid_pairs += 1
            cand_costs.append(c_res.total_cost_J)
            comp_costs.append(comp_res.total_cost_J)

            # Delta J: competitor.J - candidate.J (positive = candidate is better / lower cost)
            d_j = comp_res.total_cost_J - c_res.total_cost_J
            delta_j_list.append(d_j)

            if comp_res.total_cost_J > 1e-6:
                pct = (d_j / comp_res.total_cost_J) * 100.0
                pct_imp_list.append(pct)

    cand_win_rate = (cand_wins / max(1, total_pairs)) * 100.0
    cand_non_loss_rate = ((cand_wins + ties) / max(1, total_pairs)) * 100.0

    mean_delta_j = float(np.mean(delta_j_list)) if delta_j_list else 0.0
    median_delta_j = float(np.median(delta_j_list)) if delta_j_list else 0.0
    mean_pct_imp = float(np.mean(pct_imp_list)) if pct_imp_list else 0.0

    t_stat, t_pval, w_stat, w_pval = run_paired_hypothesis_tests(cand_costs, comp_costs)
    ci_delta_j = bootstrap_confidence_interval(delta_j_list)
    ci_pct_imp = bootstrap_confidence_interval(pct_imp_list)

    return PairedComparisonResult(
        candidate=candidate_name,
        competitor=competitor_name,
        total_pairs=total_pairs,
        valid_pairs=valid_pairs,
        candidate_wins=cand_wins,
        competitor_wins=comp_wins,
        ties=ties,
        candidate_win_rate_pct=round(cand_win_rate, 2),
        candidate_non_loss_rate_pct=round(cand_non_loss_rate, 2),
        mean_delta_J=round(mean_delta_j, 4),
        median_delta_J=round(median_delta_j, 4),
        mean_pct_improvement=round(mean_pct_imp, 3),
        ttest_statistic=t_stat,
        ttest_p_value=t_pval,
        wilcoxon_statistic=w_stat,
        wilcoxon_p_value=w_pval,
        bootstrap_ci_95_delta_J=ci_delta_j,
        bootstrap_ci_95_pct_improvement=ci_pct_imp,
    )


def compute_stratified_tier_metrics(
    records: Sequence[PlanBenchmarkRecord],
    algo_names: Sequence[str],
    baseline_name: str = "PortfolioHybrid",
) -> Dict[str, TierSummary]:
    """Stratify performance across compact (<=3), standard (4-5), and complex (>=6) tiers."""
    total_plans = max(1, len(records))
    tiers = [ComplexityTier.COMPACT.value, ComplexityTier.STANDARD.value, ComplexityTier.COMPLEX.value]
    stratified: Dict[str, TierSummary] = {}

    for t in tiers:
        sub_records = [r for r in records if r.tier == t]
        p_count = len(sub_records)
        pct = (p_count / total_plans) * 100.0

        algo_sums: Dict[str, AlgorithmSummary] = {}
        pair_sums: Dict[str, PairedComparisonResult] = {}

        if p_count > 0:
            for algo in algo_names:
                algo_sums[algo] = compute_algorithm_summary(sub_records, algo)

            for algo in algo_names:
                if algo != baseline_name:
                    pair_sums[f"{algo}_vs_{baseline_name}"] = compute_paired_comparison(
                        records=sub_records,
                        candidate_name=algo,
                        competitor_name=baseline_name,
                    )

        stratified[t] = TierSummary(
            tier=t,
            plan_count=p_count,
            pct_of_dataset=round(pct, 2),
            algorithms=algo_sums,
            pairwise_vs_baseline=pair_sums,
        )

    return stratified


def generate_benchmark_metrics_report(
    records: Sequence[PlanBenchmarkRecord],
    algo_names: Sequence[str],
    baseline_name: str = "PortfolioHybrid",
    metadata: Optional[Dict[str, Any]] = None,
) -> BenchmarkMetricsReport:
    """Master statistical report generation for all algorithms and stratifications."""
    algos_dict: Dict[str, AlgorithmSummary] = {}
    for algo in algo_names:
        algos_dict[algo] = compute_algorithm_summary(records, algo)

    # Pairwise comparisons
    pairwise_dict: Dict[str, PairedComparisonResult] = {}
    for candidate in algo_names:
        if candidate == baseline_name:
            continue
        pair_key = f"{candidate}_vs_{baseline_name}"
        pairwise_dict[pair_key] = compute_paired_comparison(
            records=records,
            candidate_name=candidate,
            competitor_name=baseline_name,
        )

    # Also compute LinUCB vs Bandit and PPO vs Bandit if present
    for special_pair in [("LinUCB_ALNS", "ALNS_Bandit"), ("PPO_ALNS", "ALNS_Bandit"), ("PPO_ALNS", "LinUCB_ALNS")]:
        c1, c2 = special_pair
        if c1 in algo_names and c2 in algo_names:
            pair_key = f"{c1}_vs_{c2}"
            pairwise_dict[pair_key] = compute_paired_comparison(
                records=records,
                candidate_name=c1,
                competitor_name=c2,
            )

    tier_dict = compute_stratified_tier_metrics(
        records=records,
        algo_names=algo_names,
        baseline_name=baseline_name,
    )

    return BenchmarkMetricsReport(
        total_plans=len(records),
        algorithms=algos_dict,
        pairwise=pairwise_dict,
        tiers=tier_dict,
        metadata=dict(metadata or {}),
    )
