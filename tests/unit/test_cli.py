"""Unit and integration test suite for the ResPlan CLI (`resplan_router.cli`).

Verifies:
- Argument parsing across all subcommands (`optimize`, `batch`, `validate`, `benchmark`, `inspect`, `serve`)
- Backward-compatible top-level legacy flags
- Nonzero exit codes on failure and error conditions
- Output formatting, reviewer scorecards, and JSON serialization
- Multi-worker batch processing, atomic checkpointing, and resume capabilities
- Dual-oracle validation against FloorPlanChecker and IndependentVerifier
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict
import pytest
import yaml

from resplan_router.cli import (
    build_parser,
    evaluate_batch_plan_worker,
    main,
    print_dual_validation_scorecard,
    print_scorecard,
)
from resplan_router.contracts import EvaluationReport, MetricFloorPlan
from resplan_router.experimental.independent_verifier import DualValidationResult, IndependentVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
DEMO_OUTPUTS_DIR = REPO_ROOT / "tests" / "demo_outputs"


# =============================================================================
# 1. Argument Parsing & Help Flags
# =============================================================================

class TestCLIParsing:
    """Test argument parsing and backward compatibility."""

    def test_top_level_help(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr().out
        assert "ResPlan Cable Ring Router" in captured
        assert "optimize" in captured
        assert "batch" in captured
        assert "validate" in captured
        assert "benchmark" in captured
        assert "inspect" in captured

    def test_subcommand_helps(self, capsys):
        for sub in ["optimize", "batch", "validate", "benchmark", "inspect", "serve"]:
            with pytest.raises(SystemExit) as excinfo:
                main([sub, "--help"])
            assert excinfo.value.code == 0

    def test_unknown_subcommand_exits_nonzero(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["non_existent_subcommand"])
        assert excinfo.value.code != 0

    def test_backward_compatible_legacy_flags(self, tmp_path):
        """Verify legacy syntax: resplan -i plan.json -o out.json routes to optimize."""
        out_json = tmp_path / "legacy_out.json"
        ret = main([
            "-i",
            str(FIXTURES_DIR / "demo_three_rooms_window.json"),
            "-o",
            str(out_json),
        ])
        assert ret == 0
        assert out_json.exists()
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "plan_id" in data
            assert "hubs" in data

    def test_backward_compatible_positional_plan(self, tmp_path):
        """Verify positional plan invocation: resplan plan.json routes to optimize."""
        out_json = tmp_path / "pos_out.json"
        ret = main([
            str(FIXTURES_DIR / "demo_three_rooms_window.json"),
            "-o",
            str(out_json),
        ])
        assert ret == 0
        assert out_json.exists()


# =============================================================================
# 2. `resplan optimize` Subcommand Tests
# =============================================================================

class TestCLIOptimize:
    """Test the single-plan optimization subcommand."""

    def test_optimize_valid_fixture(self, tmp_path, capsys):
        out_json = tmp_path / "sol.json"
        ret = main([
            "optimize",
            "-i",
            str(FIXTURES_DIR / "demo_three_rooms_window.json"),
            "-o",
            str(out_json),
        ])
        assert ret == 0
        captured = capsys.readouterr().out
        assert "RESPLAN CABLE RING ROUTER - REVIEWER SCORECARD" in captured
        assert "VALID (100% Rules Passed)" in captured
        assert "Total Cost J (Official):" in captured
        assert out_json.exists()

    def test_optimize_with_visualize(self, tmp_path):
        out_json = tmp_path / "sol.json"
        out_png = tmp_path / "overlay.png"
        ret = main([
            "optimize",
            str(FIXTURES_DIR / "demo_three_rooms_window.json"),
            "-o",
            str(out_json),
            "-v",
            str(out_png),
        ])
        assert ret == 0
        assert out_json.exists()
        assert out_png.exists()
        assert out_png.stat().st_size > 0

    def test_optimize_with_seed_and_timeout(self, tmp_path):
        out_json = tmp_path / "sol_seed.json"
        ret = main([
            "optimize",
            str(FIXTURES_DIR / "demo_three_rooms_window.json"),
            "-o",
            str(out_json),
            "--seed",
            "42",
            "--timeout",
            "10.0",
        ])
        assert ret == 0
        assert out_json.exists()

    def test_optimize_missing_input_file_returns_nonzero(self, capsys):
        ret = main(["optimize", "-i", "non_existent_floor_plan.json"])
        assert ret != 0
        err = capsys.readouterr().err
        assert "does not exist" in err or "Error:" in err

    def test_optimize_invalid_json_returns_nonzero(self, tmp_path, capsys):
        bad_json = tmp_path / "corrupt.json"
        bad_json.write_text("{ this is malformed json", encoding="utf-8")
        ret = main(["optimize", "-i", str(bad_json)])
        assert ret != 0

    def test_optimize_dataset_record_lookup(self, tmp_path):
        """Test optimizing by integer dataset plan ID."""
        out_json = tmp_path / "plan_0_sol.json"
        ret = main([
            "optimize",
            "-i",
            "0",
            "-o",
            str(out_json),
        ])
        assert ret == 0
        assert out_json.exists()


# =============================================================================
# 3. `resplan batch` Subcommand Tests
# =============================================================================

class TestCLIBatch:
    """Test batch execution, worker multiprocessing, checkpointing, and resume."""

    def test_batch_directory_mode(self, tmp_path, capsys):
        # Create a directory with 2 fixture plans
        batch_input_dir = tmp_path / "plans"
        batch_input_dir.mkdir()
        for name in ["demo_three_rooms_window.json", "demo_disconnected_zones.json"]:
            src = FIXTURES_DIR / name
            dest = batch_input_dir / name
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        out_dir = tmp_path / "batch_out"
        ret = main([
            "batch",
            "-d",
            str(batch_input_dir),
            "-w",
            "2",
            "-o",
            str(out_dir),
            "--checkpoint-interval",
            "1",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "RESPLAN PRODUCTION BATCH RUNNER" in out
        assert "BATCH RUN COMPLETE: 2 plans evaluated" in out

        # Verify artifacts
        chk_file = out_dir / "batch_checkpoint.json"
        sum_file = out_dir / "batch_summary.json"
        sol_dir = out_dir / "solutions"

        assert chk_file.exists()
        assert sum_file.exists()
        assert sol_dir.exists()

        with open(sum_file, "r", encoding="utf-8") as f:
            summary = json.load(f)
            assert summary["total_evaluated"] == 2
            assert summary["valid_count"] == 2

    def test_batch_checkpoint_resume(self, tmp_path, capsys):
        batch_input_dir = tmp_path / "plans_resume"
        batch_input_dir.mkdir()
        for name in ["demo_three_rooms_window.json", "demo_disconnected_zones.json"]:
            src = FIXTURES_DIR / name
            dest = batch_input_dir / name
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        out_dir = tmp_path / "batch_resume_out"
        # First run: process with limit 1
        ret1 = main([
            "batch",
            "-d",
            str(batch_input_dir),
            "-w",
            "1",
            "-o",
            str(out_dir),
            "--limit",
            "1",
        ])
        assert ret1 == 0

        # Second run: resume from checkpoint, remaining should be 1
        ret2 = main([
            "batch",
            "-d",
            str(batch_input_dir),
            "-w",
            "1",
            "-o",
            str(out_dir),
        ])
        assert ret2 == 0
        out2 = capsys.readouterr().out
        assert "Resumed from checkpoint: 1 plans already completed." in out2
        assert "Remaining Plans to Evaluate: 1" in out2

    def test_batch_worker_direct(self):
        """Direct unit test of evaluate_batch_plan_worker."""
        res = evaluate_batch_plan_worker(
            (str(FIXTURES_DIR / "demo_three_rooms_window.json"), {"tolerance": 0.001, "iterations": 5, "timeout": 5.0})
        )
        assert res["is_valid"] is True
        assert res["status"] in ("VALID_OPTIMIZED", "VALID_FALLBACK")
        assert res["dual_concordant"] is True

    def test_batch_worker_infeasible_fastpath(self):
        """Verify proven infeasible plan ID triggers fast-path."""
        res = evaluate_batch_plan_worker(
            (5460, {"tolerance": 0.001, "iterations": 5, "timeout": 5.0})
        )
        assert res["is_valid"] is False
        assert res["status"] == "PROVEN_INFEASIBLE"
        assert res["dual_concordant"] is True


# =============================================================================
# 4. `resplan validate` Subcommand Tests
# =============================================================================

class TestCLIValidate:
    """Test dual-oracle validation subcommand."""

    def test_validate_valid_solution(self, capsys):
        plan_file = FIXTURES_DIR / "demo_three_rooms_window.json"
        sol_file = DEMO_OUTPUTS_DIR / "demo_three_rooms_window_solution.json"

        ret = main([
            "validate",
            "-p",
            str(plan_file),
            "-s",
            str(sol_file),
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "RESPLAN DUAL-ORACLE VERIFIER - REVIEWER SCORECARD" in out
        assert "Certification Status:   CERTIFIED VALID" in out
        assert "CONCORDANT (100% Dual Agreement)" in out
        assert "FloorPlanChecker:     PASS" in out
        assert "IndependentVerifier:  PASS" in out

    def test_validate_json_mode(self, capsys):
        plan_file = FIXTURES_DIR / "demo_three_rooms_window.json"
        sol_file = DEMO_OUTPUTS_DIR / "demo_three_rooms_window_solution.json"

        ret = main([
            "validate",
            "-p",
            str(plan_file),
            "-s",
            str(sol_file),
            "--json",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["is_concordant"] is True
        assert data["official_valid"] is True
        assert data["independent_valid"] is True
        assert data["rules_concordant"] is True
        assert data["metrics_concordant"] is True

    def test_validate_invalid_tampered_solution_returns_nonzero(self, tmp_path, capsys):
        """Tamper solution: break the closed loop and verify rejection."""
        plan_file = FIXTURES_DIR / "demo_three_rooms_window.json"
        orig_sol = json.loads((DEMO_OUTPUTS_DIR / "demo_three_rooms_window_solution.json").read_text(encoding="utf-8"))

        tampered_sol = dict(orig_sol)
        # Empty route segments breaks Rule 4 (closed ring)
        tampered_sol["hubs"][0]["route_segments"] = []

        bad_sol_path = tmp_path / "broken_sol.json"
        with open(bad_sol_path, "w", encoding="utf-8") as f:
            json.dump(tampered_sol, f)

        ret = main([
            "validate",
            "-p",
            str(plan_file),
            "-s",
            str(bad_sol_path),
        ])
        assert ret != 0

    def test_validate_missing_files_returns_nonzero(self):
        ret1 = main(["validate", "-p", "non_existent_plan.json", "-s", "solution.json"])
        assert ret1 != 0

        ret2 = main(["validate", "-p", str(FIXTURES_DIR / "demo_three_rooms_window.json"), "-s", "non_existent_sol.json"])
        assert ret2 != 0


# =============================================================================
# 5. `resplan benchmark` Subcommand Tests
# =============================================================================

class TestCLIBenchmark:
    """Test benchmark suite execution subcommand."""

    def test_benchmark_smoke_plans(self, capsys):
        ret = main([
            "benchmark",
            "--plans",
            "0,24",
            "--workers",
            "1",
            "--timeout",
            "2.0",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        assert "HONEYWELL 5-WAY BENCHMARK SUITE - SUMMARY SCORECARD" in out
        assert "ALGORITHM PERFORMANCE COMPARISON:" in out
        assert "PortfolioHybrid" in out

    def test_benchmark_json_output(self, capsys):
        ret = main([
            "benchmark",
            "--plans",
            "0",
            "--workers",
            "1",
            "--timeout",
            "1.0",
            "--json",
        ])
        assert ret == 0
        out = capsys.readouterr().out
        # Filter stdout for JSON payload
        lines = [line for line in out.splitlines() if line.strip().startswith("{")]
        assert len(lines) > 0

    def test_benchmark_custom_yaml(self, tmp_path):
        yaml_path = tmp_path / "bench_cfg.yaml"
        cfg_doc = {
            "mode": "custom",
            "plan_ids": [0],
            "time_limit_seconds": 1.0,
            "max_workers": 1,
            "max_iterations": 5,
            "output_dir": str(tmp_path / "bench_results"),
        }
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg_doc, f)

        ret = main([
            "benchmark",
            "-c",
            str(yaml_path),
        ])
        assert ret == 0

    def test_benchmark_missing_yaml_returns_nonzero(self):
        ret = main(["benchmark", "-c", "non_existent_config.yaml"])
        assert ret != 0


# =============================================================================
# 6. `resplan inspect` Subcommand Tests
# =============================================================================

class TestCLIInspect:
    """Test plan and dataset inspection subcommand."""

    def test_inspect_plan_file(self, capsys):
        plan_file = FIXTURES_DIR / "demo_three_rooms_window.json"
        ret = main(["inspect", "-p", str(plan_file)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "RESPLAN FLOOR PLAN INSPECTION REPORT" in out
        assert "Plan ID:                demo_three_rooms_window" in out
        assert "Total Area:" in out
        assert "Fixed Nodes Count:      3 nodes" in out

    def test_inspect_plan_json(self, capsys):
        plan_file = FIXTURES_DIR / "demo_three_rooms_window.json"
        ret = main(["inspect", "-p", str(plan_file), "--json"])
        assert ret == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["plan_id"] == "demo_three_rooms_window"
        assert data["nodes_total"] == 3
        assert data["rooms_total"] == 4
        assert data["rooms_eligible"] == 3

    def test_inspect_dataset_summary(self, capsys):
        ret = main(["inspect", "--summary"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "RESPLAN DATASET REPOSITORY SUMMARY" in out
        assert "Indexed Floor Plans:" in out

    def test_inspect_dataset_json(self, capsys):
        ret = main(["inspect", "--summary", "--json"])
        assert ret == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "total_records" in data
        assert "is_ready" in data

    def test_inspect_missing_plan_returns_nonzero(self):
        ret = main(["inspect", "-p", "non_existent_plan.json"])
        assert ret != 0

    def test_inspect_dataset_plan_id(self, capsys):
        ret = main(["inspect", "-p", "0"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "RESPLAN FLOOR PLAN INSPECTION REPORT" in out
        assert "Plan ID:" in out


# =============================================================================
# 7. Direct Scorecard Unit Tests
# =============================================================================

class TestCLIScorecards:
    """Test direct scorecard rendering functions."""

    def test_print_scorecard_direct(self, capsys):
        from resplan_router.contracts import Point2D, RuleViolation
        dummy_eval = EvaluationReport(
            plan_id="test_plan",
            is_valid=True,
            violations=[],
            hub_count=2,
            hub_metrics=[],
            total_length_m=50.5,
            total_crossings=1,
            total_overlap_m=2.2,
            total_bends=12,
            total_cost_J=63.7,
            area_gap_m2=5.0,
            rules_passed={f"Rule {i}": True for i in range(1, 7)},
        )
        print_scorecard("test_plan", dummy_eval, elapsed_sec=0.123)
        out = capsys.readouterr().out
        assert "REVIEWER SCORECARD" in out
        assert "Total Cost J (Official): 63.70" in out
        assert "VALID (100% Rules Passed)" in out

    def test_print_scorecard_with_violations(self, capsys):
        from resplan_router.contracts import Point2D, RuleViolation
        dummy_eval = EvaluationReport(
            plan_id="invalid_plan",
            is_valid=False,
            violations=[
                RuleViolation(
                    rule_number=6,
                    rule_name="Hub Clearance",
                    hub_id="H1",
                    room_id="R01",
                    location=Point2D(x=1.0, y=1.0),
                    message="Hub clearance breach < 0.50m",
                )
            ],
            hub_count=1,
            hub_metrics=[],
            total_length_m=10.0,
            total_crossings=0,
            total_overlap_m=0.0,
            total_bends=4,
            total_cost_J=12.0,
            area_gap_m2=0.0,
            rules_passed={"Rule 1": True, "Rule 6": False},
        )
        print_scorecard("invalid_plan", dummy_eval, elapsed_sec=0.05)
        out = capsys.readouterr().out
        assert "INVALID (Violations Detected)" in out
        assert "RULE VIOLATIONS DETAIL:" in out
        assert "Hub clearance breach < 0.50m" in out
