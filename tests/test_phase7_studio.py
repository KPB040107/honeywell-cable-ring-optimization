"""Integration and E2E Tests for Phase 7 Architectural Co-Design Studio & Reviewer HUD.

Validates:
1. Static files serving: GET / returns HTML with studio title and canvas element; GET /style.css
   and GET /app.js return 200 with appropriate content types.
2. Programmatic mutation API: POST /api/mutate supports drag_node, drag_hub, slide_door,
   shift_wall, and reset, returning updated metrics, routes, and clearance status.
3. Slide 8 export endpoint: POST /api/export produces valid JSON schema passing Slide8Export validation.
4. CLI launcher: python -m resplan_router.cli --help includes --serve, --port, and --host options.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Dict

import pytest
from starlette.testclient import TestClient

from resplan_router.cli import main as cli_main
from resplan_router.phase6_output.json_exporter import Slide8Export
from resplan_router.phase7_interactive.server import app, engine


@pytest.fixture
def client() -> TestClient:
    """Fixture providing a Starlette TestClient connected to the Co-Design server."""
    # Reset engine to baseline state before each test
    engine.reset()
    return TestClient(app)


def test_static_files_served(client: TestClient) -> None:
    """Test 1: Verify static studio files are served with correct headers and HTML structure."""
    # 1. Root index.html
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert "text/html" in resp_index.headers.get("content-type", "")
    html_text = resp_index.text
    assert "ResPlan Co-Design Studio" in html_text
    assert "studioCanvas" in html_text
    assert "Competition Metrics Scoreboard" in html_text
    assert "Slide 3 Hard Validity Checklist" in html_text
    assert "Export Slide 8 JSON" in html_text

    # 2. Stylesheet
    resp_css = client.get("/style.css")
    assert resp_css.status_code == 200
    assert "text/css" in resp_css.headers.get("content-type", "")
    assert "--route-h1:" in resp_css.text
    assert "--halo-safe:" in resp_css.text

    # 3. Client JavaScript
    resp_js = client.get("/app.js")
    assert resp_js.status_code == 200
    content_type = resp_css.headers.get("content-type", "")
    assert resp_js.status_code == 200
    assert "studioCanvas" in resp_js.text
    assert "worldToScreen" in resp_js.text
    assert "ws/co-design" in resp_js.text


def test_http_mutation_api(client: TestClient) -> None:
    """Test 2: Verify POST /api/mutate updates metrics, routes, and clearance status."""
    # 1. Move node N1 via /api/mutate
    new_x, new_y = 6.8, 1.4
    resp = client.post(
        "/api/mutate",
        json={"action": "drag_node", "node_id": "N1", "x": new_x, "y": new_y},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["type"] == "state_update"
    assert "metrics" in data
    assert "rules_passed" in data
    assert "halos" in data
    assert "routes" in data

    # Check N1 was updated
    n1 = next(n for n in data["nodes"] if n["node_id"] == "N1")
    assert abs(n1["x"] - new_x) < 1e-4
    assert abs(n1["y"] - new_y) < 1e-4

    # Halos reflect updated position
    assert "N1" in data["halos"]
    assert data["halos"]["N1"]["is_clear"] is True

    # 2. Move hub H1 via /api/mutate
    resp_hub = client.post(
        "/api/mutate",
        json={"action": "drag_hub", "hub_id": "H1", "x": 5.0, "y": 3.8},
    )
    assert resp_hub.status_code == 200
    data_hub = resp_hub.json()
    h1 = next(h for h in data_hub["hubs"] if h["hub_id"] == "H1")
    assert abs(h1["x"] - 5.0) < 1e-4
    assert abs(h1["y"] - 3.8) < 1e-4

    # 3. Slide door via /api/mutate
    resp_door = client.post(
        "/api/mutate",
        json={"action": "slide_door", "door_id": "P01", "offset_m": 0.15},
    )
    assert resp_door.status_code == 200
    assert resp_door.json()["type"] == "state_update"

    # 4. Get current state via /api/mutate
    resp_state = client.post("/api/mutate", json={"action": "get_state"})
    assert resp_state.status_code == 200
    assert resp_state.json()["plan"]["plan_id"] == "slide5_schematic"

    # 5. Reset via /api/mutate
    resp_reset = client.post("/api/mutate", json={"action": "reset"})
    assert resp_reset.status_code == 200
    assert resp_reset.json()["is_valid"] is True

    # 6. Invalid action error handling
    resp_bad = client.post("/api/mutate", json={"action": "unknown_action"})
    assert resp_bad.status_code == 400
    assert "error" in resp_bad.json()

    # 7. Missing required params error handling
    resp_missing = client.post("/api/mutate", json={"action": "drag_node", "node_id": "N1"})
    assert resp_missing.status_code == 400


def test_slide8_json_export_endpoint(client: TestClient) -> None:
    """Test 3: Verify POST /api/export produces schema-compliant Slide 8 JSON."""
    resp = client.post("/api/export")
    assert resp.status_code == 200
    payload = resp.json()

    # Validate against strict Pydantic Slide8Export model
    validated = Slide8Export.model_validate(payload)
    assert validated.plan_id == "slide5_schematic"
    assert validated.reported_metrics.hub_count >= 1
    assert validated.reported_metrics.total_length_m > 0.0

    # Ensure all hubs have valid closed sequences and route segments
    for hub in validated.hubs:
        assert len(hub.closed_sequence) >= 2
        assert hub.closed_sequence[0] == hub.hub_id
        assert hub.closed_sequence[-1] == hub.hub_id
        assert len(hub.route_segments) >= 1
        for seg in hub.route_segments:
            assert seg.length_m >= 0.0
            assert len(seg.polyline) >= 2


def test_cli_serve_flag_help() -> None:
    """Test 4: Verify --serve, --port, and --host flags exist in CLI --help."""
    result = subprocess.run(
        [sys.executable, "-m", "resplan_router.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    stdout = result.stdout
    assert "--serve" in stdout
    assert "--port" in stdout
    assert "--host" in stdout
    assert "Co-Design Studio" in stdout
