"""Unit tests for Plan ID UI state lifecycle and scoreboard integration."""

import pytest
from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine


def test_engine_dataset_plan_lifecycle():
    engine = IncrementalCoDesignEngine()

    # 1. Load dataset plan preview
    state, lat = engine.load_dataset_plan(14433)
    assert state["preview_mode"] is True
    assert state["phase11_scoreboard"] is None
    assert state["plan"]["plan_id"] == "14433"
    assert len(state["plan"]["rooms"]) > 0
    assert len(state["nodes"]) > 0
    assert len(state["hubs"]) == 0
    assert len(state["routes"]) == 0

    # 2. Generate cable rings with Phase 11 optimizer
    routed_state, gen_lat = engine.generate_cable_rings()
    assert routed_state["preview_mode"] is False
    assert len(routed_state["hubs"]) > 0
    assert len(routed_state["routes"]) > 0
    assert routed_state["metrics"]["H"] > 0
    assert routed_state["metrics"]["L"] > 0
    assert routed_state["is_valid"] is True

    # Check Phase 11 scoreboard in state
    sc = routed_state["phase11_scoreboard"]
    assert sc is not None
    assert "greedy_metrics" in sc
    assert "optimized_metrics" in sc
    assert sc["hub_optimality_status"] in ("PROVEN OPTIMAL HUB COUNT", "FEASIBLE HUB COUNT")
    assert sc["improvement_percent"] >= 0.0
    assert sc["relaxed_j_lower_bound"] > 0.0
    assert sc["total_runtime_s"] > 0.0

    # 3. Slide 8 Export
    export_data = engine.export_slide8()
    assert export_data["plan_id"] == "14433"
    assert "hubs" in export_data
    assert len(export_data["hubs"]) > 0
