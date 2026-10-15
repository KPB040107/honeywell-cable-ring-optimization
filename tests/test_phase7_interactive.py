"""Tests for Phase 7 Real-Time Incremental Re-Routing Engine & WebSockets Server.

Validates:
1. ClearanceHaloTracker 0.5 m wall-clearance checks (green #2ECC71 vs red #E6194B).
2. Sub-50 ms incremental node dragging and rectilinear closed-ring re-routing.
3. Sub-50 ms incremental hub dragging with departure/arrival polyline updates.
4. FastAPI WebSocket /ws/co-design bidirectional communication and state broadcast.
5. Live rule violation detection (Rule 5/6 failure and red halo on wall collisions).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from resplan_router.contracts import Point2D
from resplan_router.phase7_interactive.halo_tracker import ClearanceHaloTracker
from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine
from resplan_router.phase7_interactive.server import app, engine


def test_halo_clearance_tracker() -> None:
    """Test 1: ClearanceHaloTracker identifies safe distances and wall proximity."""
    eng = IncrementalCoDesignEngine()
    plan = eng.plan

    # 1. Point well inside living room with >= 0.5m clearance
    # Living room bounds: [3.0, 0.0] to [11.0, 5.0]
    safe_pt = Point2D(x=6.0, y=2.5)
    halo_safe = ClearanceHaloTracker.check_clearance(safe_pt, plan, required_clearance_m=0.50)
    assert halo_safe["is_clear"] is True
    assert halo_safe["distance_m"] >= 0.499
    assert halo_safe["color"] == "#2ECC71"
    assert "CLEAR" in halo_safe["status_text"]

    # 2. Point close to south wall / front door (y = 0.20m < 0.50m)
    close_pt = Point2D(x=6.0, y=0.20)
    halo_close = ClearanceHaloTracker.check_clearance(close_pt, plan, required_clearance_m=0.50)
    assert halo_close["is_clear"] is False
    assert halo_close["distance_m"] < 0.50
    assert halo_close["color"] == "#E6194B"
    assert "TOO CLOSE" in halo_close["status_text"] or "OUTSIDE" in halo_close["status_text"]

    # 3. Point outside eligible room (e.g. bathroom R07 at x=13.0, y=7.0)
    bathroom_pt = Point2D(x=13.0, y=7.0)
    halo_bath = ClearanceHaloTracker.check_clearance(bathroom_pt, plan, required_clearance_m=0.50)
    assert halo_bath["is_clear"] is False
    assert halo_bath["color"] == "#E6194B"


def test_incremental_node_drag_latency() -> None:
    """Test 2: Moving node N1 produces valid closed ring in strictly < 50 ms."""
    eng = IncrementalCoDesignEngine()

    test_positions = [
        (6.8, 1.4),
        (6.5, 2.0),
        (7.2, 3.2),
        (6.0, 3.0),
    ]

    for new_x, new_y in test_positions:
        state, latency_ms = eng.update_node_position("N1", new_x, new_y)

        # Re-routing latency must be strictly under 50 ms (target < 30 ms)
        assert latency_ms < 50.0, f"Latency {latency_ms:.2f} ms exceeded 50 ms budget"

        # Check returned payload format
        assert state["type"] == "state_update"
        assert "metrics" in state
        assert "rules_passed" in state
        assert state["metrics"]["H"] == 2
        assert state["metrics"]["L"] > 0.0

        # Ring must be closed and valid
        assert state["is_valid"] is True
        assert state["rules_passed"]["Rule 4"] is True  # Closed ring rule
        assert state["rules_passed"]["Rule 5"] is True  # Free space rule

        # N1 location in nodes list must match dragged coordinates
        n1_node = next(n for n in state["nodes"] if n["node_id"] == "N1")
        assert abs(n1_node["x"] - new_x) < 1e-4
        assert abs(n1_node["y"] - new_y) < 1e-4


def test_incremental_hub_drag_latency() -> None:
    """Test 3: Moving hub H1 updates departure/arrival routes in strictly < 50 ms."""
    eng = IncrementalCoDesignEngine()

    test_hub_positions = [
        (5.0, 3.8),
        (5.4, 3.4),
        (4.8, 3.5),
    ]

    for new_x, new_y in test_hub_positions:
        state, latency_ms = eng.update_hub_position("H1", new_x, new_y)

        assert latency_ms < 50.0, f"Latency {latency_ms:.2f} ms exceeded 50 ms budget"

        assert state["is_valid"] is True
        assert state["rules_passed"]["Rule 6"] is True  # Hub clearance rule
        assert state["rules_passed"]["Rule 4"] is True  # Closed ring return

        h1_hub = next(h for h in state["hubs"] if h["hub_id"] == "H1")
        assert abs(h1_hub["x"] - new_x) < 1e-4
        assert abs(h1_hub["y"] - new_y) < 1e-4


def test_websocket_roundtrip() -> None:
    """Test 4: FastAPI WebSocket /ws/co-design processes drag actions and streams state."""
    client = TestClient(app)

    with client.websocket_connect("/ws/co-design") as ws:
        # 1. Drag node N1
        ws.send_json({
            "action": "drag_node",
            "node_id": "N1",
            "x": 6.8,
            "y": 1.4,
        })
        resp = ws.receive_json()

        assert resp["type"] == "state_update"
        assert resp["latency_ms"] < 50.0
        assert "metrics" in resp
        assert "rules_passed" in resp
        assert "halos" in resp
        assert "N1" in resp["halos"]
        assert resp["halos"]["N1"]["is_clear"] is True

        # 2. Drag hub H1
        ws.send_json({
            "action": "drag_hub",
            "hub_id": "H1",
            "x": 5.0,
            "y": 3.8,
        })
        resp_hub = ws.receive_json()
        assert resp_hub["type"] == "state_update"
        assert "H1" in resp_hub["halos"]

        # 3. Slide door P01
        ws.send_json({
            "action": "slide_door",
            "door_id": "P01",
            "offset_m": 0.1,
        })
        resp_door = ws.receive_json()
        assert resp_door["type"] == "state_update"

        # 4. Reset
        ws.send_json({"action": "reset"})
        resp_reset = ws.receive_json()
        assert resp_reset["type"] == "state_update"
        assert resp_reset["is_valid"] is True


def test_rule_violation_live_detection() -> None:
    """Test 5: Dragging node into wall (0.1 m clearance) or outside room triggers red halo and rule failure."""
    eng = IncrementalCoDesignEngine()

    # Drag N1 to 0.1m clearance from living room west wall (wall at x=3.05, y=0..2.0)
    state, _ = eng.update_node_position("N1", 3.15, 1.0)

    assert "N1" in state["halos"]
    halo_n1 = state["halos"]["N1"]
    assert halo_n1["is_clear"] is False
    assert halo_n1["color"] == "#E6194B"
    assert halo_n1["distance_m"] < 0.50

    # Must indicate rule violation (Rule 5 or Rule 6)
    assert (state["rules_passed"]["Rule 6"] is False) or (state["rules_passed"]["Rule 5"] is False)
    assert state["is_valid"] is False
