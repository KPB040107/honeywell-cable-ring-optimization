"""Modular 5-Way Benchmarking Framework for Honeywell Cable Ring Optimization.

Exports:
- BenchmarkConfig, AlgorithmConfig, BenchmarkMode, ComplexityTier, AlgorithmId
- BenchmarkRunner, ALNSLinUCBFallbackOptimizer
- BenchmarkReporter
- AlgorithmRunResult, PlanBenchmarkRecord, BenchmarkMetricsReport
- Paired statistical significance testing and bootstrap CI utilities
"""

from __future__ import annotations

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

__all__ = [
    "AlgorithmConfig",
    "AlgorithmId",
    "AlgorithmRunResult",
    "AlgorithmSummary",
    "BenchmarkConfig",
    "BenchmarkMetricsReport",
    "BenchmarkMode",
    "BenchmarkReporter",
    "BenchmarkRunner",
    "ComplexityTier",
    "PairedComparisonResult",
    "PlanBenchmarkRecord",
    "TierSummary",
    "ALNSLinUCBFallbackOptimizer",
    "resolve_optimizer",
    "bootstrap_confidence_interval",
    "compute_algorithm_summary",
    "compute_paired_comparison",
    "compute_stratified_tier_metrics",
    "evaluate_lexicographical_matchup",
    "generate_benchmark_metrics_report",
    "run_paired_hypothesis_tests",
]
