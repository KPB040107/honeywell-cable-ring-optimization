"""Content-Addressed Cache Key Specification and Invalidation Suite.

Implements PRD Section 8 and Benchmarking Protocol (Agent 6):
Key = SHA256(canonical_json(plan_id, geometry_hash, checker_version, objective_weights, solver_id, solver_params))

Enforces strict provenance, deterministic invalidation of stale entries, and
integrity verification for baseline solutions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from resplan_router.contracts import MetricFloorPlan


CHECKER_VERSION = "FloorPlanChecker_v1.0_tol0.001"

CANONICAL_OBJECTIVE_WEIGHTS: Dict[str, float] = {
    "weight_length_L": 1.0,
    "weight_crossings_X": 5.0,
    "weight_overlap_O": 1.0,
    "weight_bends_B": 0.5,
    "tolerance_mm": 1.0,
}


def compute_geometry_hash(floor_plan: MetricFloorPlan) -> str:
    """Compute deterministic cryptographic SHA-256 hash of MetricFloorPlan geometry.

    Incorporates:
    - Plan ID and scale factor (drawing units per metre)
    - Total reported floor area
    - Inner enclosing polygon WKT
    - Routable free-space multi-polygon WKT
    - Obstacle union multi-polygon WKT
    - Canonical sorted rooms with category, eligibility, area, and polygon WKT
    - Canonical sorted fixed nodes with ID, room ID, and metric coordinates (0.1 mm precision)
    """
    sorted_rooms = []
    for r_id in sorted(floor_plan.rooms.keys()):
        r = floor_plan.rooms[r_id]
        sorted_rooms.append({
            "room_id": str(r.room_id),
            "category": str(r.category),
            "is_eligible": bool(r.is_eligible),
            "area_m2": round(float(r.area_m2), 4),
            "polygon_wkt": str(r.polygon_wkt),
        })

    sorted_nodes = []
    for n_id in sorted(floor_plan.nodes.keys()):
        n = floor_plan.nodes[n_id]
        sorted_nodes.append({
            "node_id": str(n.node_id),
            "room_id": str(n.room_id),
            "location": [round(float(n.location.x), 4), round(float(n.location.y), 4)],
        })

    payload = {
        "plan_id": str(floor_plan.plan_id),
        "scale_units_per_m": round(float(floor_plan.scale_units_per_m), 6),
        "total_area_m2": round(float(floor_plan.total_area_m2), 4),
        "inner_wkt": str(floor_plan.inner_wkt),
        "free_space_wkt": str(floor_plan.free_space_wkt),
        "obstacles_wkt": str(floor_plan.obstacles_wkt),
        "rooms": sorted_rooms,
        "nodes": sorted_nodes,
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_content_addressed_cache_key(
    plan_id: str | int,
    geometry_hash: str,
    solver_id: str,
    checker_version: str = CHECKER_VERSION,
    objective_weights: Optional[Dict[str, float]] = None,
    solver_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate SHA-256 content-addressed cache key.

    Formula:
    Key = SHA-256(JSON(plan_id, geometry_hash, checker_version, objective_weights, solver_id, solver_params))
    """
    weights = objective_weights or CANONICAL_OBJECTIVE_WEIGHTS
    params = solver_params or {}

    payload = {
        "checker_version": str(checker_version),
        "geometry_hash": str(geometry_hash),
        "objective_weights": {k: float(v) for k, v in sorted(weights.items())},
        "plan_id": str(plan_id),
        "solver_id": str(solver_id),
        "solver_params": {k: str(v) for k, v in sorted(params.items())},
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class CacheAuditReport:
    """Findings from auditing existing baseline SQLite cache."""
    db_path: str
    total_records: int = 0
    valid_records: int = 0
    negative_records: int = 0
    legacy_keys_count: int = 0
    stale_records_count: int = 0
    false_negative_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def audit_sqlite_cache(db_path: Path | str = "results/cache/baseline_cache.sqlite") -> CacheAuditReport:
    """Audit baseline cache SQLite file against content-addressed requirements."""
    db_file = Path(db_path)
    if not db_file.exists():
        return CacheAuditReport(db_path=str(db_path), details={"status": "FILE_NOT_FOUND"})

    conn = sqlite3.connect(str(db_file), timeout=10.0)
    try:
        cur = conn.cursor()

        # Check table schema
        cur.execute("PRAGMA table_info(baseline_cache);")
        columns = [row[1] for row in cur.fetchall()]

        cur.execute("SELECT count(*) FROM baseline_cache;")
        total = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM baseline_cache WHERE is_valid = 1;")
        valid = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM baseline_cache WHERE is_valid = 0;")
        negative = cur.fetchone()[0]

        # False negatives: plans marked is_valid=0 where failure_reason indicates solver succeeded
        cur.execute(
            """
            SELECT plan_id, failure_reason FROM baseline_cache 
            WHERE is_valid = 0 AND (failure_reason LIKE '%OPTIMAL%' OR failure_reason LIKE '%FEASIBLE%');
            """
        )
        false_negs = cur.fetchall()

        # Has content-addressed columns?
        has_content_key = "cache_key" in columns
        has_geom_hash = "geometry_hash" in columns

        legacy_keys = total if not has_content_key else 0

        # Check created_at timestamps (pre-freeze entries before 2026-10-23)
        cur.execute("SELECT count(*) FROM baseline_cache WHERE created_at < '2026-10-23T00:00:00';")
        stale_count = cur.fetchone()[0]

        return CacheAuditReport(
            db_path=str(db_path),
            total_records=total,
            valid_records=valid,
            negative_records=negative,
            legacy_keys_count=legacy_keys,
            stale_records_count=stale_count,
            false_negative_count=len(false_negs),
            details={
                "has_content_addressed_key": has_content_key,
                "has_geometry_hash": has_geom_hash,
                "schema_columns": columns,
                "sample_false_negatives": [list(x) for x in false_negs[:10]],
            },
        )
    finally:
        conn.close()


def invalidate_stale_cache_records(
    db_path: Path | str = "results/cache/baseline_cache.sqlite",
    cutoff_timestamp: str = "2026-10-23T00:00:00",
    purge_false_negatives_only: bool = False,
) -> int:
    """Invalidate or purge stale cache entries generated under older semantics.

    Args:
        db_path: SQLite cache database path
        cutoff_timestamp: ISO timestamp before which entries are considered stale
        purge_false_negatives_only: If True, only delete false negatives with 'OPTIMAL'/'FEASIBLE'

    Returns:
        Number of modified/deleted records.
    """
    db_file = Path(db_path)
    if not db_file.exists():
        return 0

    conn = sqlite3.connect(str(db_file), timeout=30.0)
    try:
        cur = conn.cursor()
        if purge_false_negatives_only:
            cur.execute(
                """
                DELETE FROM baseline_cache 
                WHERE is_valid = 0 AND (failure_reason LIKE '%OPTIMAL%' OR failure_reason LIKE '%FEASIBLE%');
                """
            )
        else:
            # Delete stale entries pre-dating official freeze cutoff
            cur.execute("DELETE FROM baseline_cache WHERE created_at < ?;", (cutoff_timestamp,))

        deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()
