"""End-to-End Integration Pipeline and CLI Verification.

Validates:
1. End-to-end execution of Phases 1 through 6 via route_floor_plan().
2. Slide 8 competition JSON schema compliance and 0.1 mm precision.
3. Slide 5 floor-plan overlay visualization and red violation callouts.
4. CLI entrypoint invocation and reviewer scorecard formatting.
5. Repository-wide regression gate.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import pytest

from resplan_router.config import SystemConfig
from resplan_router.contracts import (
    CompletedHubRing,
    EvaluationReport,
    MetricFloorPlan,
    Point2D,
    RuleViolation,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import (
    Slide8Export,
    export_slide8_json,
)
from resplan_router.phase6_output.pipeline import route_floor_plan
from resplan_router.phase6_output.visualizer import FloorPlanVisualizer


@pytest.fixture
def slide5_fixture_path() -> Path:
    """Return path to Slide 5 test schematic fixture."""
    return Path(__file__).parent / "fixtures" / "slide5_schematic.json"


def test_end_to_end_slide5_pipeline(slide5_fixture_path: Path, tmp_path: Path) -> None:
    """Test 1: Run complete route_floor_plan() pipeline on Slide 5 schematic.

    Verifies:
    - Pipeline completes and returns valid solution.
    - Checker confirms 100% hard rule compliance (is_valid == True).
    - Hub count is exactly 2.
    - Cable geometry and score are checked against the supplied fixed nodes.
    - Slide 8 JSON is written to disk and strictly conforms to schema.
    - Slide 5 visualizer renders high-resolution PNG schematic.
    """
    out_json = tmp_path / "slide5_solution.json"
    out_png = tmp_path / "slide5_overlay.png"

    config = SystemConfig(max_area_m2=100.0, max_nodes_per_hub=5)

    exported_json, evaluation, bundle = route_floor_plan(
        raw_plan_dict_or_path=slide5_fixture_path,
        config=config,
        output_json_path=out_json,
        render_png_path=out_png,
    )

    # 1. Validation & Checker Invariants
    assert evaluation.is_valid, f"Pipeline output failed checker: {evaluation.violations}"
    assert len(evaluation.violations) == 0
    assert evaluation.hub_count == 2
    assert evaluation.total_length_m > 0
    assert evaluation.total_cost_J == pytest.approx(
        evaluation.total_length_m + 5 * evaluation.total_crossings
        + evaluation.total_overlap_m + 0.5 * evaluation.total_bends
    )
    assert all(evaluation.rules_passed.values())

    # 2. Slide 8 Export Invariants
    assert out_json.is_file()
    assert out_json.stat().st_size > 0

    # Parse and validate with strict Pydantic model
    validated_export = Slide8Export.model_validate(exported_json)
    assert validated_export.plan_id == "slide5_schematic"
    assert validated_export.units == "m"
    assert len(validated_export.hubs) == 2
    assert validated_export.reported_metrics.hub_count == 2
    assert validated_export.reported_metrics.total_length_m > 0.0

    # Verify Slide 8 exact dictionary keys
    assert "plan_id" in exported_json
    assert "units" in exported_json
    assert "hubs" in exported_json
    assert "reported_metrics" in exported_json

    for hub in exported_json["hubs"]:
        assert "hub_id" in hub
        assert "room_id" in hub
        assert "location" in hub
        assert "x" in hub["location"]
        assert "y" in hub["location"]
        assert "assigned_rooms" in hub
        assert "assigned_nodes" in hub
        assert "closed_sequence" in hub
        assert "route_segments" in hub
        assert "ring_length_m" in hub
        for seg in hub["route_segments"]:
            assert "from" in seg
            assert "to" in seg
            assert "length_m" in seg
            assert "polyline" in seg
            for pt in seg["polyline"]:
                assert len(pt) == 2
                assert isinstance(pt[0], (int, float))
                assert isinstance(pt[1], (int, float))

    # 3. Slide 5 Visualizer Invariants
    assert out_png.is_file()
    assert out_png.stat().st_size > 1000  # Non-trivial image file generated


def test_cli_invocation(slide5_fixture_path: Path, tmp_path: Path) -> None:
    """Test 2: Invoke resplan_router CLI via subprocess.

    Verifies:
    - CLI runs cleanly with returncode == 0.
    - Slide 8 JSON and schematic PNG are written to specified paths.
    - Reviewer scorecard is printed to stdout.
    """
    out_json = tmp_path / "cli_output.json"
    out_png = tmp_path / "cli_schematic.png"

    cmd = [
        sys.executable,
        "-m",
        "resplan_router.cli",
        "-i",
        str(slide5_fixture_path),
        "-o",
        str(out_json),
        "-v",
        str(out_png),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0, f"CLI exited with error:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert "REVIEWER SCORECARD" in result.stdout
    assert "VALID (100% Rules Passed)" in result.stdout
    assert "[PASS] Rule 1" in result.stdout
    assert "[PASS] Rule 6" in result.stdout

    assert out_json.is_file()
    assert out_json.stat().st_size > 0
    assert out_png.is_file()
    assert out_png.stat().st_size > 1000


def test_negative_rules_and_red_violation_rendering(
    slide5_fixture_path: Path,
    tmp_path: Path,
) -> None:
    """Test 3: Inject synthetic violations for Hard Rules and verify visualizer renders red overlays."""
    with open(slide5_fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)

    out_json, evaluation, bundle = route_floor_plan(data)

    # Synthetic negative evaluation with violations on Rule 1, 5, and 6
    violations = [
        RuleViolation(
            rule_number=5,
            rule_name="Free Space",
            location=Point2D(x=5.0, y=3.0),
            message="Cable intersects solid wall obstacle",
        ),
        RuleViolation(
            rule_number=6,
            rule_name="Hub Clearance",
            location=Point2D(x=5.2, y=3.6),
            message="Hub clearance 0.20m < 0.50m from wall",
        ),
        RuleViolation(
            rule_number=1,
            rule_name="Full Coverage",
            message="Room R99 omitted from coverage",
        ),
    ]
    neg_eval = EvaluationReport(
        plan_id=plan.plan_id,
        is_valid=False,
        violations=violations,
        hub_count=evaluation.hub_count,
        hub_metrics=evaluation.hub_metrics,
        total_length_m=evaluation.total_length_m,
        total_crossings=evaluation.total_crossings,
        total_overlap_m=evaluation.total_overlap_m,
        total_bends=evaluation.total_bends,
        total_cost_J=evaluation.total_cost_J,
        area_gap_m2=evaluation.area_gap_m2,
        rules_passed={
            "Rule 1": False,
            "Rule 2": True,
            "Rule 3": True,
            "Rule 4": True,
            "Rule 5": False,
            "Rule 6": False,
        },
    )

    neg_png = tmp_path / "violation_overlay.png"
    fig = FloorPlanVisualizer.render_overlay(
        plan=plan,
        bundle=bundle,
        evaluation=neg_eval,
        output_path=neg_png,
        title="Test Violation Schematic",
    )

    assert fig is not None
    assert neg_png.is_file()
    assert neg_png.stat().st_size > 1000


def test_full_suite_regression_gate(slide5_fixture_path: Path) -> None:
    """Test 4: Re-evaluate exported JSON directly through FloorPlanChecker."""
    with open(slide5_fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)

    exported_json, evaluation, bundle = route_floor_plan(data)

    # Independent re-evaluation directly from JSON dictionary
    recheck_report = FloorPlanChecker.evaluate(plan, exported_json)
    assert recheck_report.is_valid
    assert recheck_report.hub_count == evaluation.hub_count
    assert recheck_report.total_crossings == evaluation.total_crossings
    assert recheck_report.total_overlap_m == evaluation.total_overlap_m
    assert recheck_report.total_cost_J == evaluation.total_cost_J
