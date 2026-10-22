"""Unit tests for Benchmark Reliability, Resumption, Crash Isolation, and Data Integrity.

Author: Agent 4 (Benchmark Reliability Lead)
Verification targets:
1. Zero train/test leakage and deterministic test split selection.
2. Atomic disk writes, JSON/CSV integrity verifiers.
3. Resumable benchmark execution and checkpoint plan skipping.
4. Solver crash and invalid plan failure isolation.
5. Initial solution parity across all benchmarked algorithms.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List
import pytest

from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository

from benchmarks.framework.config import (
    AlgorithmConfig,
    AlgorithmId,
    BenchmarkConfig,
    BenchmarkMode,
)
from benchmarks.framework.metrics import (
    AlgorithmRunResult,
    PlanBenchmarkRecord,
)
from benchmarks.framework.reliability_verifier import (
    BenchmarkReliabilityVerifier,
    CheckResult,
    VerificationStatus,
    atomic_write_csv,
    atomic_write_file,
    atomic_write_json,
    verify_checkpoint_integrity,
    verify_csv_integrity,
    verify_failure_isolation,
    verify_initial_state_parity,
    verify_json_integrity,
    verify_quarantine_integrity,
    verify_resumable_execution,
    verify_split_leakage,
    verify_test_split_determinism,
    verify_time_budget_enforcement,
)
from benchmarks.framework.runner import (
    BenchmarkRunner,
    _worker_evaluate_plan,
)


# ============================================================================
# 1. Dataset Leakage & Determinism Tests
# ============================================================================

class TestDatasetLeakageAndDeterminism:
    """Tests for split integrity, zero train/test leakage, and determinism."""

    def test_split_disjointness_and_zero_leakage(self) -> None:
        """Verify that split.json has zero train/test leakage and disjoint splits."""
        res = verify_split_leakage("split.json")
        assert res.status == VerificationStatus.PASS
        assert res.details["test_count"] == 1632
        assert res.details["total_unique_plans"] == 17000
        # All pairwise intersections must be strictly zero
        for name, count in res.details["intersections"].items():
            assert count == 0, f"Leakage detected in {name}: count={count}"

    def test_test_split_determinism(self) -> None:
        """Verify that reading split.json test IDs is 100% deterministic."""
        res = verify_test_split_determinism("split.json", repetitions=5)
        assert res.status == VerificationStatus.PASS
        assert res.details["test_count"] == 1632
        assert res.details["identical_across_reads"] is True
        assert res.details["all_integers"] is True
        assert res.details["is_sorted"] is True

    def test_quarantine_integrity(self) -> None:
        """Verify that test split partitions cleanly into 1,578 clean and 54 quarantined plans."""
        res = verify_quarantine_integrity("split.json")
        assert res.status == VerificationStatus.PASS
        assert res.details["raw_test_count"] == 1632
        assert res.details["clean_test_count"] == 1578
        assert res.details["quarantined_test_count"] == 54
        assert res.details["quarantined_test_count"] + res.details["clean_test_count"] == 1632

    def test_simulated_split_leakage_detection(self, tmp_path: Path) -> None:
        """Verify that verify_split_leakage detects deliberate train/test leakage."""
        corrupt_split = tmp_path / "corrupt_split.json"
        data = {
            "train": [1, 2, 3, 4],
            "val": [5, 6],
            "test": [4, 7, 8],  # Plan 4 is leaked into test!
            "augmented": [9],
        }
        with open(corrupt_split, "w", encoding="utf-8") as f:
            json.dump(data, f)

        res = verify_split_leakage(corrupt_split)
        assert res.status == VerificationStatus.FAIL
        assert "Data leakage detected" in res.message
        assert res.details["intersections"]["train_and_test"] == 1

    def test_simulated_duplicate_detection(self, tmp_path: Path) -> None:
        """Verify that duplicate IDs within a single split trigger failure."""
        dup_split = tmp_path / "dup_split.json"
        data = {
            "train": [1, 2, 2, 3],  # Duplicate 2
            "val": [5],
            "test": [7],
            "augmented": [9],
        }
        with open(dup_split, "w", encoding="utf-8") as f:
            json.dump(data, f)

        res = verify_split_leakage(dup_split)
        assert res.status == VerificationStatus.FAIL
        assert "Duplicate plan IDs detected" in res.message


# ============================================================================
# 2. Atomic Disk Writes & Data Integrity Tests
# ============================================================================

class TestAtomicWritesAndDataIntegrity:
    """Tests for atomic file writes and JSON/CSV data integrity."""

    def test_atomic_write_json_success(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        payload = {"model": "HoneywellRing", "version": 2, "metrics": [1.0, 2.0]}

        p = atomic_write_json(target, payload)
        assert p.exists()
        assert target.exists()

        valid, msg, loaded = verify_json_integrity(target, required_keys=["model", "version", "metrics"])
        assert valid is True
        assert loaded == payload

    def test_atomic_write_csv_success(self, tmp_path: Path) -> None:
        target = tmp_path / "data.csv"
        rows = [
            {"plan_id": 1, "tier": "compact", "cost_J": 12.34},
            {"plan_id": 2, "tier": "standard", "cost_J": 45.67},
        ]
        headers = ["plan_id", "tier", "cost_J"]

        p = atomic_write_csv(target, rows, headers)
        assert p.exists()

        valid, msg, row_count = verify_csv_integrity(target, expected_columns=headers, min_rows=2)
        assert valid is True
        assert row_count == 2

    def test_json_integrity_corrupted_and_missing_keys(self, tmp_path: Path) -> None:
        # 1. Non-existent file
        v, msg, _ = verify_json_integrity(tmp_path / "non_existent.json")
        assert v is False
        assert "does not exist" in msg

        # 2. Empty file
        empty_f = tmp_path / "empty.json"
        empty_f.write_text("", encoding="utf-8")
        v, msg, _ = verify_json_integrity(empty_f)
        assert v is False
        assert "empty" in msg

        # 3. Corrupted syntax
        corrupt_f = tmp_path / "corrupt.json"
        corrupt_f.write_text('{"bad": json syntax,', encoding="utf-8")
        v, msg, _ = verify_json_integrity(corrupt_f)
        assert v is False
        assert "Corrupted JSON" in msg

        # 4. Missing required keys
        partial_f = tmp_path / "partial.json"
        partial_f.write_text('{"keyA": 1}', encoding="utf-8")
        v, msg, _ = verify_json_integrity(partial_f, required_keys=["keyA", "keyB"])
        assert v is False
        assert "Missing required keys" in msg

    def test_csv_integrity_corrupted_and_ragged_rows(self, tmp_path: Path) -> None:
        # 1. Empty file
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("", encoding="utf-8")
        v, msg, count = verify_csv_integrity(empty_csv)
        assert v is False

        # 2. Missing columns
        csv_missing = tmp_path / "missing.csv"
        csv_missing.write_text("col1,col2\n1,2\n", encoding="utf-8")
        v, msg, count = verify_csv_integrity(csv_missing, expected_columns=["col1", "col2", "col3"])
        assert v is False
        assert "missing expected columns" in msg

        # 3. Ragged row
        ragged_csv = tmp_path / "ragged.csv"
        ragged_csv.write_text("col1,col2\n1,2\n3,4,5\n", encoding="utf-8")
        v, msg, count = verify_csv_integrity(ragged_csv, expected_columns=["col1", "col2"])
        assert v is False
        assert "Ragged CSV row" in msg

    def test_checkpoint_integrity_valid_and_corrupted(self, tmp_path: Path) -> None:
        valid_ckpt = tmp_path / "valid_ckpt.json"
        data = {
            "timestamp": "2026-10-22 12:00:00 UTC",
            "mode": "custom",
            "config": {},
            "completed_plan_ids": [10, 20],
            "records": [
                {"plan_id": 10, "tier": "compact", "results": {}},
                {"plan_id": 20, "tier": "standard", "results": {}},
            ],
        }
        with open(valid_ckpt, "w", encoding="utf-8") as f:
            json.dump(data, f)

        valid, msg, loaded = verify_checkpoint_integrity(valid_ckpt)
        assert valid is True
        assert "Checkpoint intact" in msg

        # Mismatched lengths
        corrupt_ckpt = tmp_path / "mismatch_ckpt.json"
        data["completed_plan_ids"] = [10]  # Only 1 id for 2 records
        with open(corrupt_ckpt, "w", encoding="utf-8") as f:
            json.dump(data, f)

        valid, msg, _ = verify_checkpoint_integrity(corrupt_ckpt)
        assert valid is False
        assert "Checkpoint mismatch" in msg


# ============================================================================
# 3. Resumable Execution & Checkpoint Skipping Tests
# ============================================================================

class TestResumptionLogic:
    """Tests for benchmark runner resumption and already-completed plan skipping."""

    def test_load_checkpoint_populates_completed_records(self, tmp_path: Path) -> None:
        ckpt_path = tmp_path / "benchmark_checkpoint_custom.json"
        record1 = PlanBenchmarkRecord(
            plan_id=10,
            tier="compact",
            eligible_rooms=3,
            total_area_m2=45.0,
            results={
                "PortfolioHybrid": AlgorithmRunResult(
                    algorithm_name="PortfolioHybrid",
                    h_count=1,
                    total_cost_J=10.0,
                    is_valid=True,
                )
            },
        )
        record2 = PlanBenchmarkRecord(
            plan_id=20,
            tier="standard",
            eligible_rooms=5,
            total_area_m2=85.0,
            results={
                "PortfolioHybrid": AlgorithmRunResult(
                    algorithm_name="PortfolioHybrid",
                    h_count=2,
                    total_cost_J=20.0,
                    is_valid=True,
                )
            },
        )

        data = {
            "timestamp": "2026-10-22 12:00:00 UTC",
            "mode": "custom",
            "config": {},
            "completed_plan_ids": [10, 20],
            "records": [record1.to_dict(), record2.to_dict()],
        }
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        cfg = BenchmarkConfig(
            mode=BenchmarkMode.CUSTOM,
            plan_ids=[10, 20, 30],
            checkpoint_file=ckpt_path,
            output_dir=tmp_path,
            resume=True,
        )

        runner = BenchmarkRunner(config=cfg)
        runner.load_checkpoint()

        assert len(runner.completed_records) == 2
        assert 10 in runner.completed_records
        assert 20 in runner.completed_records
        assert 30 not in runner.completed_records

        # When resolving remaining plans, 10 and 20 must be skipped
        target_ids = cfg.resolve_plan_ids()
        remaining = [pid for pid in target_ids if pid not in runner.completed_records]
        assert remaining == [30]

    def test_resumable_execution_end_to_end(self, tmp_path: Path) -> None:
        """Test verify_resumable_execution on 2 clean plans."""
        res = verify_resumable_execution(
            plan_ids=[0, 4],
            output_dir=tmp_path / "resumption_dir",
            time_limit=0.5,
            max_iterations=1,
        )
        assert res.status == VerificationStatus.PASS
        assert res.details["resumed_checkpoint_plans"] == 1
        assert res.details["stage2_total_plans"] == 2
        assert res.details["unique_plan_count"] == 2


# ============================================================================
# 4. Failure Isolation & Crash Containment Tests
# ============================================================================

class TestCrashAndFailureIsolation:
    """Tests for isolating solver crashes and missing/invalid floor plans."""

    def test_worker_isolated_on_invalid_plan_id(self, tmp_path: Path) -> None:
        """Worker must return LOAD_ERROR and is_valid=False for non-existent plans without crashing."""
        cfg = BenchmarkConfig(
            mode=BenchmarkMode.CUSTOM,
            plan_ids=[999999],
            output_dir=tmp_path,
        )
        raw_dict = _worker_evaluate_plan((999999, cfg.to_dict()))

        assert raw_dict["plan_id"] == 999999
        assert "results" in raw_dict
        for algo_name, res in raw_dict["results"].items():
            assert res["is_valid"] is False
            assert res["solver_status"] == "LOAD_ERROR"

    def test_worker_isolated_on_unknown_algorithm_crash(self, tmp_path: Path) -> None:
        """Worker must isolate an algorithm error and continue without unhandled exception."""
        bad_algo = AlgorithmConfig(
            algo_id="NonExistent_Optimizer",
            display_name="CrashingAlgorithm",
            enabled=True,
        )
        cfg = BenchmarkConfig(
            mode=BenchmarkMode.CUSTOM,
            plan_ids=[0],
            algorithms=[bad_algo],
            output_dir=tmp_path,
        )
        raw_dict = _worker_evaluate_plan((0, cfg.to_dict()))
        assert raw_dict["plan_id"] == 0
        algo_res = raw_dict["results"]["NonExistent_Optimizer"]
        assert algo_res["is_valid"] is False
        assert algo_res["solver_status"] == "ERROR"
        assert algo_res["total_cost_J"] == float("inf")

    def test_batch_failure_isolation_end_to_end(self, tmp_path: Path) -> None:
        """Test verify_failure_isolation on mixed batch (invalid plan + valid plan)."""
        res = verify_failure_isolation(tmp_path / "fail_dir")
        assert res.status == VerificationStatus.PASS
        assert res.details["mixed_batch_records"] == 2
        assert res.details["plan_statuses"][999999] == "LOAD_ERROR"
        assert res.details["plan_statuses"][0] in ("OPTIMAL", "FEASIBLE")


# ============================================================================
# 5. Algorithm Parity & Resource Budgets Tests
# ============================================================================

class TestAlgorithmParityAndBudgets:
    """Tests for identical initial solution parity and fair time budget enforcement."""

    def test_initial_state_parity(self) -> None:
        """Verify that ALNS variants start from identical PortfolioHybrid incumbent."""
        res = verify_initial_state_parity(plan_id=0, random_seed=42)
        assert res.status == VerificationStatus.PASS
        assert res.details["parity_confirmed"] is True

        # Confirm all algorithms reported identical starting cost J and hub count H
        costs = res.details["algorithm_costs"]
        hubs = res.details["algorithm_hubs"]
        base_j = res.details["base_cost_J"]
        base_h = res.details["base_h_count"]

        for algo, cost in costs.items():
            assert abs(cost - base_j) < 1e-4, f"Initial cost disparity in {algo}: {cost} != {base_j}"
        for algo, h in hubs.items():
            assert h == base_h, f"Initial hub disparity in {algo}: {h} != {base_h}"

    def test_time_budget_enforcement(self) -> None:
        """Verify that algorithms finish within the designated budget and tolerance."""
        res = verify_time_budget_enforcement(plan_id=0, budget_seconds=1.5, seed=42)
        assert res.status in (VerificationStatus.PASS, VerificationStatus.WARN)
        assert len(res.details["exceeded"]) == 0


# ============================================================================
# 6. BenchmarkReliabilityVerifier Master Suite Tests
# ============================================================================

class TestBenchmarkReliabilityVerifierSuite:
    """Tests for master orchestrator report generation and serialization."""

    def test_verifier_report_generation(self, tmp_path: Path) -> None:
        verifier = BenchmarkReliabilityVerifier(
            split_path="split.json",
            temp_dir=tmp_path / "audit_temp",
        )
        report = verifier.run_full_audit(test_parity_plan_id=0, fast_mode=True)

        assert report.total_checks >= 6
        assert report.failed_checks == 0
        assert report.all_passed is True

        # Test JSON serialization
        json_path = tmp_path / "audit_report.json"
        report.export_json(json_path)
        assert json_path.exists()
        valid, msg, loaded = verify_json_integrity(json_path, required_keys=["timestamp", "all_passed", "summary", "checks"])
        assert valid is True

        # Test Markdown generation
        md_text = report.to_markdown()
        assert "# Benchmark Reliability Verification Report" in md_text
        assert "Audit Checklist & Results" in md_text
        assert "[PASS]" in md_text
