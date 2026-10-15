"""Integration and E2E Tests for Phase 9 Interactive Wire-Cut Survivability Studio HUD.

Validates:
1. REST API endpoints POST /api/survivability/cut and POST /api/survivability/heal cycle.
2. WebSocket /ws/co-design bidirectional streaming of wire_cut and wire_heal actions.
3. Single-door leaf room fault isolation (Bedroom 2, Door Lane A) UI payload showing
   RETURN_TERMINAL power source and 100% device survivability.
4. Static frontend asset verification (HTML, CSS, JS) confirming Scissors Tool and
   NFPA 72 Class A HUD components are properly served.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from resplan_router.contracts import PowerSource
from resplan_router.phase7_interactive.server import app, engine


@pytest.fixture(autouse=True)
def reset_engine_baseline():
    """Ensure engine is reset to baseline Slide 5 schematic before and after each test."""
    engine.reset()
    yield
    engine.reset()


def test_rest_cut_and_heal_cycle() -> None:
    """Test 1: REST endpoints POST /api/survivability/cut and heal verify 100% Class A survivability."""
    client = TestClient(app)

    # 1. Perform cut on Hub 1 cable route
    cut_payload = {
        "hub_id": "H1",
        "cut_x": 6.8,
        "cut_y": 1.2,
    }
    resp_cut = client.post("/api/survivability/cut", json=cut_payload)
    assert resp_cut.status_code == 200, f"Wire cut failed: {resp_cut.text}"

    cut_data = resp_cut.json()
    assert cut_data["is_severed"] is True
    assert cut_data["survivability_pct"] == 100.0
    assert cut_data["nfpa72_class_a_compliant"] is True
    assert cut_data["hub_id"] == "H1"
    assert len(cut_data["offline_nodes"]) == 0
    assert cut_data["online_nodes"] == 3
    assert len(cut_data["node_statuses"]) == 3

    # 2. Verify state endpoint now contains active cut report
    resp_state = client.get("/api/state")
    assert resp_state.status_code == 200
    state_data = resp_state.json()
    assert state_data["survivability_report"] is not None
    assert state_data["survivability_report"]["is_severed"] is True

    # 3. Heal wire and restore closed loop
    resp_heal = client.post("/api/survivability/heal")
    assert resp_heal.status_code == 200
    heal_data = resp_heal.json()
    assert heal_data["survivability_report"] is None

    # 4. Confirm state returns to normal
    resp_state_healed = client.get("/api/state")
    assert resp_state_healed.status_code == 200
    assert resp_state_healed.json()["survivability_report"] is None


def test_websocket_wire_cut_and_heal() -> None:
    """Test 2: WebSocket actions wire_cut and wire_heal stream survivability_update and state_update."""
    client = TestClient(app)

    with client.websocket_connect("/ws/co-design") as ws:
        # 1. Send wire_cut action
        ws.send_json({
            "action": "wire_cut",
            "hub_id": "H1",
            "cut_x": 6.8,
            "cut_y": 1.2,
        })
        resp_cut = ws.receive_json()

        assert resp_cut["type"] == "survivability_update"
        assert "report" in resp_cut
        report = resp_cut["report"]
        assert report["is_severed"] is True
        assert report["survivability_pct"] == 100.0
        assert report["nfpa72_class_a_compliant"] is True

        # Node statuses must be complete
        node_stats = report["node_statuses"]
        assert set(node_stats.keys()) == {"N1", "N2", "N3"}
        for nid, stat in node_stats.items():
            assert stat["is_online"] is True
            assert stat["power_source"] in ("primary_outbound", "return_terminal")

        # 2. Send wire_heal action
        ws.send_json({"action": "wire_heal"})
        resp_heal = ws.receive_json()

        assert resp_heal["type"] == "state_update"
        assert resp_heal.get("survivability_report") is None


def test_single_door_leaf_cut_ui_payload() -> None:
    """Test 3: Single-door leaf room fault isolation (Bedroom 2, Door Lane A) UI payload."""
    client = TestClient(app)

    # Cut entering leaf room Bedroom 2 (Leg 0: H2 -> N4) at (8.0, 6.95)
    resp = client.post("/api/survivability/cut", json={
        "hub_id": "H2",
        "cut_x": 8.0,
        "cut_y": 6.95,
    })
    assert resp.status_code == 200
    report = resp.json()

    assert report["is_severed"] is True
    assert report["survivability_pct"] == 100.0
    assert report["nfpa72_class_a_compliant"] is True
    assert report["cut_location"]["from_node"] == "H2"
    assert report["cut_location"]["to_node"] == "N4"

    # In reverse direction, downstream nodes receive power from Return Terminal
    statuses = report["node_statuses"]
    assert "N6" in statuses
    assert "N4" in statuses
    assert statuses["N6"]["power_source"] in ("return_terminal", PowerSource.RETURN_TERMINAL)
    assert statuses["N6"]["is_online"] is True
    assert statuses["N4"]["power_source"] in ("return_terminal", PowerSource.RETURN_TERMINAL)
    assert statuses["N4"]["is_online"] is True
    assert report["return_branch_nodes"] == ["N6", "N5", "N4"]


def test_studio_static_assets_contain_survivability_elements() -> None:
    """Test 4: Verify static HTML, CSS, and JS serve Scissors Tool and Survivability Monitor elements."""
    client = TestClient(app)

    # 1. Index HTML structure
    resp_html = client.get("/")
    assert resp_html.status_code == 200
    html = resp_html.text
    assert "btnScissorsTool" in html
    assert "Cut Wire Tool" in html
    assert "survivabilityCard" in html
    assert "Wire-Cut Survivability" in html
    assert "btnHealCircuit" in html
    assert "NFPA 72 CLASS A" in html

    # 2. Stylesheet rules
    resp_css = client.get("/style.css")
    assert resp_css.status_code == 200
    css = resp_css.text
    assert ".scissors-btn" in css
    assert ".survivability-card" in css
    assert ".btn-heal-circuit" in css
    assert ".legend-dot.pulse-outbound" in css
    assert ".legend-dot.pulse-return" in css

    # 3. JavaScript engine
    resp_js = client.get("/app.js")
    assert resp_js.status_code == 200
    js = resp_js.text
    assert "findNearestCablePoint" in js
    assert "wire_cut" in js
    assert "wire_heal" in js
    assert "updateSurvivabilityHUD" in js
    assert "drawCutMarker" in js
    assert "drawScissorsCursor" in js
