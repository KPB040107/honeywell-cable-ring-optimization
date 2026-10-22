"""High-Performance Persistent Baseline Cache for CableRing RL (Priority 2).

Thread-safe and multi-process safe cache backed by SQLite WAL mode.
Stores validated PortfolioHybrid solutions, objective metrics, and negative-cache
records for infeasible/malformed floor plans, eliminating redundant baseline computation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, Optional, Tuple

from resplan_router.contracts import MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle
from resplan_router.optimizers.base import BaseFloorPlanOptimizer, OptimizerResult, create_optimizer_result
from resplan_router.phase6_output.checker import FloorPlanChecker
from benchmarks.framework.cache_keys import (
    CHECKER_VERSION,
    CANONICAL_OBJECTIVE_WEIGHTS,
    compute_content_addressed_cache_key,
    compute_geometry_hash,
)


DEFAULT_CACHE_DIR = Path("results/cache")
DEFAULT_DB_PATH = DEFAULT_CACHE_DIR / "baseline_cache.sqlite"
CONFIG_VERSION = "honeywell_cable_ring_v1.0"


class BaselineCache:
    """Thread-safe and process-safe baseline solution cache using SQLite WAL mode."""

    _instance: Optional[BaselineCache] = None

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def get_instance(cls, db_path: Path | str = DEFAULT_DB_PATH) -> BaselineCache:
        """Process-wide singleton instance."""
        if cls._instance is None:
            cls._instance = cls(db_path=db_path)
        return cls._instance

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and generous busy timeout."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self) -> None:
        """Initialize the cache table schema."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS baseline_cache (
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_valid ON baseline_cache(is_valid);")

    def get(self, plan_id: int | str) -> Optional[Tuple[bool, Optional[OptimizerResult], Optional[str]]]:
        """Query cache for plan_id.

        Returns:
            None: Cache miss.
            (True, OptimizerResult, None): Cached valid baseline.
            (False, None, failure_reason): Cached negative record (infeasible plan).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT is_valid, solver_status, h_count, cost_J, length_m, bends,
                       crossings, overlap_m, area_gap_m2, bundle_json, partition_json, failure_reason
                FROM baseline_cache
                WHERE plan_id = ?
                """,
                (str(plan_id),),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        (
            is_valid_int,
            solver_status,
            h_count,
            cost_J,
            length_m,
            bends,
            crossings,
            overlap_m,
            area_gap_m2,
            bundle_json,
            partition_json,
            failure_reason,
        ) = row

        is_valid = bool(is_valid_int)

        if not is_valid:
            # Negative cache hit: ignore false negatives poisoned by previous buggy solver runs
            if failure_reason and ("OPTIMAL" in failure_reason or "FEASIBLE" in failure_reason):
                return None
            return (False, None, failure_reason or "Cached infeasible floor plan")

        # Reconstruct OptimizerResult from JSON
        try:
            bundle = SynthesizedRingsBundle.model_validate_json(bundle_json) if bundle_json else None
            partition = HubZonePartition.model_validate_json(partition_json) if partition_json else None

            res = OptimizerResult(
                algorithm_name="PortfolioHybrid_Cached",
                plan_id=str(plan_id),
                h_count=h_count,
                total_length_m=length_m,
                total_bends=bends,
                total_crossings=crossings,
                total_overlap_m=overlap_m,
                total_cost_J=cost_J,
                area_gap_m2=area_gap_m2,
                is_valid=True,
                solver_status=solver_status,
                runtime_s=0.0001,
                bundle=bundle,
                partition=partition,
                violations=[],
                rules_passed={},
                diagnostic_info={"cached": True},
            )
            return (True, res, None)
        except Exception:
            # On parse failure, treat as cache miss
            return None

    def put_valid(
        self,
        plan_id: int | str,
        result: OptimizerResult,
        config_hash: str = CONFIG_VERSION,
    ) -> None:
        """Store a validated positive baseline solution atomically."""
        if not result.is_valid or result.bundle is None or result.partition is None:
            # Never cache an invalid result as valid
            self.put_negative(plan_id, reason="Attempted to cache invalid result as valid", config_hash=config_hash)
            return

        bundle_json = result.bundle.model_dump_json()
        partition_json = result.partition.model_dump_json()
        now_str = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO baseline_cache (
                    plan_id, is_valid, solver_status, h_count, cost_J, length_m,
                    bends, crossings, overlap_m, area_gap_m2, bundle_json,
                    partition_json, failure_reason, config_hash, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?);
                """,
                (
                    str(plan_id),
                    result.solver_status,
                    result.h_count,
                    result.total_cost_J,
                    result.total_length_m,
                    result.total_bends,
                    result.total_crossings,
                    result.total_overlap_m,
                    result.area_gap_m2,
                    bundle_json,
                    partition_json,
                    config_hash,
                    now_str,
                ),
            )

    def put_negative(
        self,
        plan_id: int | str,
        reason: str,
        config_hash: str = CONFIG_VERSION,
    ) -> None:
        """Store an immutable negative record for an infeasible or malformed plan."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO baseline_cache (
                    plan_id, is_valid, solver_status, h_count, cost_J, length_m,
                    bends, crossings, overlap_m, area_gap_m2, bundle_json,
                    partition_json, failure_reason, config_hash, created_at
                ) VALUES (?, 0, 'INFEASIBLE', 0, 9999.0, 0.0, 0, 0, 0.0, 0.0, NULL, NULL, ?, ?, ?);
                """,
                (str(plan_id), reason, config_hash, now_str),
            )

    def get_or_compute(
        self,
        plan: MetricFloorPlan,
        optimizer: BaseFloorPlanOptimizer,
    ) -> Tuple[bool, Optional[OptimizerResult], Optional[str]]:
        """Retrieve baseline from cache or compute it once, validate it, and cache it."""
        pid = str(plan.plan_id)

        # 1. Check cache
        cached = self.get(pid)
        if cached is not None:
            return cached

        # 2. Check room capacity preconditions before running optimizer
        for r_id, r in plan.rooms.items():
            if r.is_eligible and r.area_m2 > 100.001:
                reason = f"Eligible room '{r_id}' area {r.area_m2:.2f} m^2 exceeds 100.0 m^2 limit"
                self.put_negative(pid, reason=reason)
                return (False, None, reason)

        # 3. Compute baseline
        try:
            res = optimizer.optimize(plan, time_limit_seconds=2.0)
            if res.is_valid and res.bundle is not None:
                # Independent verification with FloorPlanChecker
                report = FloorPlanChecker.evaluate(plan, res.bundle)
                if report.is_valid:
                    self.put_valid(pid, res)
                    return (True, res, None)
                else:
                    reason = f"Checker failed: {report.rule_violations}"
                    self.put_negative(pid, reason=reason)
                    return (False, None, reason)
            else:
                reason = res.diagnostic_info.get("error", f"Solver status: {res.solver_status}")
                self.put_negative(pid, reason=str(reason))
                return (False, None, str(reason))
        except Exception as exc:
            reason = f"Optimizer exception: {exc}"
            self.put_negative(pid, reason=reason)
            return (False, None, reason)
