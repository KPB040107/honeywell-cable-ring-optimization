"""Unit tests for Content-Addressed Cache Key and Invalidation Suite (Agent 6)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import sqlite3

from benchmarks.framework.cache_keys import (
    CHECKER_VERSION,
    CANONICAL_OBJECTIVE_WEIGHTS,
    audit_sqlite_cache,
    compute_content_addressed_cache_key,
    compute_geometry_hash,
)
from resplan_router.contracts import FixedNode, MetricFloorPlan, Point2D, EligibleOrExcludedRoom


@pytest.fixture
def sample_metric_floor_plan() -> MetricFloorPlan:
    """Create a minimal synthetic MetricFloorPlan for deterministic hashing tests."""
    room1 = EligibleOrExcludedRoom(
        room_id="R01",
        category="living",
        is_eligible=True,
        area_m2=25.0,
        polygon_wkt="POLYGON ((0 0, 5 0, 5 5, 0 5, 0 0))",
    )
    room2 = EligibleOrExcludedRoom(
        room_id="R02",
        category="bedroom",
        is_eligible=True,
        area_m2=20.0,
        polygon_wkt="POLYGON ((5 0, 9 0, 9 5, 5 5, 5 0))",
    )
    node1 = FixedNode(
        node_id="N1",
        room_id="R01",
        location=Point2D(x=2.5, y=2.5),
    )
    node2 = FixedNode(
        node_id="N2",
        room_id="R02",
        location=Point2D(x=7.0, y=2.5),
    )
    return MetricFloorPlan(
        plan_id="TEST_001",
        scale_units_per_m=20.0,
        total_area_m2=45.0,
        inner_wkt="POLYGON ((0 0, 9 0, 9 5, 0 5, 0 0))",
        free_space_wkt="MULTIPOLYGON (((0 0, 9 0, 9 5, 0 5, 0 0)))",
        obstacles_wkt="MULTIPOLYGON (((5 0, 5 5, 5.2 5, 5.2 0, 5 0)))",
        rooms={"R01": room1, "R02": room2},
        nodes={"N1": node1, "N2": node2},
    )


def test_geometry_hash_deterministic(sample_metric_floor_plan: MetricFloorPlan):
    """Geometry hash must be strictly identical across multiple invocations."""
    h1 = compute_geometry_hash(sample_metric_floor_plan)
    h2 = compute_geometry_hash(sample_metric_floor_plan)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256


def test_geometry_hash_sensitivity_to_node_coords(sample_metric_floor_plan: MetricFloorPlan):
    """Moving a node by 1mm must alter the geometry hash."""
    h_orig = compute_geometry_hash(sample_metric_floor_plan)

    # Shift node N1 slightly
    moved_nodes = dict(sample_metric_floor_plan.nodes)
    moved_nodes["N1"] = FixedNode(
        node_id="N1",
        room_id="R01",
        location=Point2D(x=2.501, y=2.5),
    )
    modified_plan = sample_metric_floor_plan.model_copy(update={"nodes": moved_nodes})
    h_mod = compute_geometry_hash(modified_plan)

    assert h_orig != h_mod


def test_geometry_hash_sensitivity_to_scale(sample_metric_floor_plan: MetricFloorPlan):
    """Changing scale factor must alter the geometry hash."""
    h_orig = compute_geometry_hash(sample_metric_floor_plan)
    modified_plan = sample_metric_floor_plan.model_copy(update={"scale_units_per_m": 20.05})
    h_mod = compute_geometry_hash(modified_plan)

    assert h_orig != h_mod


def test_content_addressed_cache_key():
    """Verify composite cache key incorporates all 5 core elements."""
    geom_hash = "abc123def456"
    key1 = compute_content_addressed_cache_key(
        plan_id="TEST_001",
        geometry_hash=geom_hash,
        solver_id="PortfolioHybrid",
        solver_params={"time_limit": 5.0, "seed": 42},
    )
    assert len(key1) == 64

    # Different solver id must produce different key
    key2 = compute_content_addressed_cache_key(
        plan_id="TEST_001",
        geometry_hash=geom_hash,
        solver_id="ALNS_Bandit",
        solver_params={"time_limit": 5.0, "seed": 42},
    )
    assert key1 != key2

    # Different checker version must produce different key
    key3 = compute_content_addressed_cache_key(
        plan_id="TEST_001",
        geometry_hash=geom_hash,
        solver_id="PortfolioHybrid",
        checker_version="FloorPlanChecker_v2.0",
        solver_params={"time_limit": 5.0, "seed": 42},
    )
    assert key1 != key3


def test_audit_sqlite_cache_temporary_db(tmp_path: Path):
    """Verify audit_sqlite_cache detects schema, legacy keys, and false negatives."""
    db_file = tmp_path / "test_cache.sqlite"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE baseline_cache (
            plan_id TEXT PRIMARY KEY,
            is_valid INTEGER NOT NULL,
            solver_status TEXT NOT NULL,
            h_count INTEGER NOT NULL,
            cost_J REAL NOT NULL,
            length_m REAL NOT NULL,
            bends INTEGER NOT NULL,
            crossings INTEGER NOT NULL,
            overlap_m REAL NOT NULL,
            area_gap_m2 REAL NOT NULL,
            bundle_json TEXT,
            partition_json TEXT,
            failure_reason TEXT,
            config_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    # Insert 1 valid record, 1 false negative record, 1 true negative record
    cur.execute(
        "INSERT INTO baseline_cache VALUES ('1', 1, 'OPTIMAL', 1, 10.0, 10.0, 4, 0, 0.0, 0.0, NULL, NULL, NULL, 'v1', '2026-10-21T10:00:00');"
    )
    cur.execute(
        "INSERT INTO baseline_cache VALUES ('2', 0, 'OPTIMAL', 0, 9999.0, 0.0, 0, 0, 0.0, 0.0, NULL, NULL, 'Solver status: OPTIMAL', 'v1', '2026-10-21T10:00:00');"
    )
    cur.execute(
        "INSERT INTO baseline_cache VALUES ('3', 0, 'INFEASIBLE', 0, 9999.0, 0.0, 0, 0, 0.0, 0.0, NULL, NULL, 'Room > 100m2', 'v1', '2026-10-21T10:00:00');"
    )
    conn.commit()
    conn.close()

    report = audit_sqlite_cache(db_file)
    assert report.total_records == 3
    assert report.valid_records == 1
    assert report.negative_records == 2
    assert report.false_negative_count == 1
    assert report.legacy_keys_count == 3
    assert report.stale_records_count == 3
