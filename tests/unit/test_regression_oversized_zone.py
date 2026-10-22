"""Regression tests for oversized room / zone capacity handling (Priority 1 & 6).

Verifies that floor plans containing eligible rooms > 100.0 m² (such as the 325.94 m²
anomaly) do NOT crash with pydantic_core.ValidationError, but instead return explicit
solver_status="INFEASIBLE" and are safely quarantined by the environment and baseline cache.
"""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest
import shapely
from shapely.geometry import box

from resplan_router.contracts import (
    EligibleOrExcludedRoom,
    FixedNode,
    MetricFloorPlan,
    Point2D,
    RoomCategory,
)
from resplan_router.optimizers.greedy_baseline import GreedyBaselineOptimizer
from resplan_router.optimizers.portfolio_hybrid import PortfolioHybridOptimizer
from resplan_router.phase6_output.checker import FloorPlanChecker
from rl.baseline_cache import BaselineCache
from rl.environment import CableRingEnv


def create_synthetic_oversized_floor_plan(living_area: float = 325.9387) -> MetricFloorPlan:
    """Construct a synthetic floor plan with an oversized eligible room."""
    # Scale width & height to achieve exact desired area
    w = 10.0
    h = living_area / w
    living_poly = box(0.0, 0.0, w, h)
    bed_poly = box(w, 0.0, w + 4.0, 5.0)  # 20 m²
    inner_poly = shapely.ops.unary_union([living_poly, bed_poly])

    rooms = {
        "living_01": EligibleOrExcludedRoom(
            room_id="living_01",
            category=RoomCategory.LIVING.value,
            is_eligible=True,
            area_m2=round(living_area, 4),
            polygon_wkt=living_poly.wkt,
        ),
        "bedroom_01": EligibleOrExcludedRoom(
            room_id="bedroom_01",
            category=RoomCategory.BEDROOM.value,
            is_eligible=True,
            area_m2=20.0,
            polygon_wkt=bed_poly.wkt,
        ),
    }

    nodes = {
        "N1": FixedNode(node_id="N1", room_id="living_01", location=Point2D(x=2.0, y=2.0)),
        "N2": FixedNode(node_id="N2", room_id="bedroom_01", location=Point2D(x=w + 2.0, y=2.0)),
    }

    return MetricFloorPlan(
        plan_id="oversized_test_plan",
        scale_units_per_m=20.0,
        total_area_m2=round(living_area + 20.0, 4),
        inner_wkt=inner_poly.wkt,
        free_space_wkt=inner_poly.wkt,
        obstacles_wkt="GEOMETRYCOLLECTION EMPTY",
        rooms=rooms,
        nodes=nodes,
    )


class TestOversizedZoneRegression:
    """Exhaustive regression test suite ensuring zero uncaught ValidationErrors."""

    def test_greedy_baseline_handles_oversized_room_safely(self) -> None:
        """GreedyBaseline must return INFEASIBLE without throwing ValidationError."""
        plan = create_synthetic_oversized_floor_plan(living_area=325.9387)
        opt = GreedyBaselineOptimizer()

        # Must not raise pydantic_core.ValidationError
        res = opt.optimize(plan)

        assert not res.is_valid, "Oversized plan must not be marked valid"
        assert res.solver_status == "INFEASIBLE"
        assert res.bundle is None
        assert "100.0" in res.diagnostic_info.get("error", "")

    def test_portfolio_hybrid_handles_oversized_room_safely(self) -> None:
        """PortfolioHybrid must arbitrate to INFEASIBLE without crashing."""
        plan = create_synthetic_oversized_floor_plan(living_area=325.9387)
        opt = PortfolioHybridOptimizer()

        res = opt.optimize(plan)

        assert not res.is_valid
        assert res.solver_status == "INFEASIBLE"
        assert res.bundle is None

    def test_baseline_cache_negative_caches_oversized_plan(self, tmp_path) -> None:
        """BaselineCache must create a negative cache entry for oversized plans."""
        db_file = tmp_path / "test_reg_cache.sqlite"
        cache = BaselineCache(db_path=db_file)
        plan = create_synthetic_oversized_floor_plan(living_area=325.9387)
        opt = PortfolioHybridOptimizer()

        is_valid, res, reason = cache.get_or_compute(plan, opt)

        assert is_valid is False
        assert res is None
        assert "100.0" in reason

        # Subsequent query must hit negative cache instantly
        hit = cache.get(int(hash(plan.plan_id) % 1_000_000))
        # Note: query by plan_id
        # Let's test with numeric plan_id
        numeric_plan = MetricFloorPlan(
            plan_id="99999",
            scale_units_per_m=20.0,
            total_area_m2=plan.total_area_m2,
            inner_wkt=plan.inner_wkt,
            free_space_wkt=plan.free_space_wkt,
            obstacles_wkt=plan.obstacles_wkt,
            rooms=plan.rooms,
            nodes=plan.nodes,
        )
        is_v, _, _ = cache.get_or_compute(numeric_plan, opt)
        assert is_v is False
        cached_entry = cache.get(99999)
        assert cached_entry is not None
        assert cached_entry[0] is False  # Negative cache hit

    def test_cable_ring_env_reset_quarantines_and_falls_back(self) -> None:
        """Environment reset must not crash when encountering an oversized plan."""
        # Force env to attempt oversized plan
        env = CableRingEnv(
            plan_ids=[13354],  # Plan 13354 has living_01 = 364.8 m2
            max_steps_per_episode=10,
            random_seed=42,
        )

        # Reset must succeed via safe fallback without throwing ValidationError
        obs, info = env.reset(options={"plan_id": 13354})
        assert obs is not None
        assert len(obs) == 24
        assert env.incumbent_state is not None
        assert env.incumbent_state.is_valid is True
