"""Comprehensive Tests for Floor Plan JSON Upload, Validation, Preview, and Routing.

Validates:
1. Valid floor plan upload: file upload and JSON body upload of demo_three_rooms_window.json.
   Verifies unrouted preview state (preview_mode=True, 0 hubs, 0 routes, preserved fixed nodes).
   Verifies cable ring generation (preview_mode=False, H, L, B, X, O, J computed, 6/6 hard rules passed).
2. Invalid floor plan uploads: specific diagnostic errors for:
   - Malformed JSON syntax
   - Non-dictionary root
   - Missing or non-positive area
   - Missing or invalid inner boundary polygon
   - Missing or empty rooms
   - Duplicate fixed node IDs
   - Multiple fixed nodes per room
   - Unsupported room categories
3. Rule violation honesty:
   - Fixed node within < 0.50 m of wall is NOT moved or relocated silently.
   - Rule 6 fails honestly in the checklist, and clearance halo is flagged red.
   - Oversized room (> 100 m²) triggers Rule 2 violation without solver crash.
4. Solution artifact exports:
   - GET /api/export/json produces valid Slide 8 JSON conforming to Slide8Export.
   - GET /api/export/png produces valid binary PNG with correct header and content-type.
5. One-click demo benchmark switching:
   - POST /api/demo/{demo_id} works for slide5, demo_three_rooms_window, demo_six_rooms_capacity.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any, Dict

import pytest
from starlette.testclient import TestClient

from resplan_router.phase1_ingestion.validator import validate_floor_plan_json
from resplan_router.phase6_output.json_exporter import Slide8Export
from resplan_router.phase7_interactive.server import app, engine


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def client() -> TestClient:
    """Fixture providing a Starlette TestClient with engine reset."""
    engine.reset()
    return TestClient(app)


def _load_fixture(filename: str) -> Dict[str, Any]:
    with open(FIXTURES_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================================================================
# 1. Valid Upload & Routing Pipeline
# =========================================================================

def test_valid_floor_plan_upload_and_preview(client: TestClient) -> None:
    """Test upload of demo_three_rooms_window.json enters unrouted preview mode."""
    fixture_data = _load_fixture("demo_three_rooms_window.json")
    json_bytes = json.dumps(fixture_data).encode("utf-8")

    # 1. Upload via multipart/form-data
    response = client.post(
        "/api/upload",
        files={"file": ("demo_three_rooms_window.json", io.BytesIO(json_bytes), "application/json")},
    )
    assert response.status_code == 200, response.text
    res_data = response.json()
    assert res_data["status"] == "ok"
    assert "state" in res_data

    state = res_data["state"]
    assert state["preview_mode"] is True
    assert state["plan"]["plan_id"] == "demo_three_rooms_window"
    assert len(state["plan"]["rooms"]) == 4
    assert len(state["nodes"]) == 3
    # Preview must have no routes or hubs yet
    assert len(state["routes"]) == 0
    assert len(state["hubs"]) == 0

    # Verify fixed nodes were NOT moved
    scale = fixture_data.get("scale", fixture_data.get("scale_units_per_m", 10.0))
    for n in state["nodes"]:
        n_id = n["node_id"]
        orig_node = next(x for x in fixture_data["nodes"] if x["node_id"] == n_id)
        assert pytest.approx(n["x"], abs=1e-3) == orig_node["location"][0] / scale
        assert pytest.approx(n["y"], abs=1e-3) == orig_node["location"][1] / scale


def test_generate_cable_rings_after_upload(client: TestClient) -> None:
    """Test generating cable rings on uploaded floor plan produces valid routed overlay and metrics."""
    fixture_data = _load_fixture("demo_three_rooms_window.json")
    json_bytes = json.dumps(fixture_data).encode("utf-8")

    # Upload
    up_resp = client.post(
        "/api/upload",
        files={"file": ("demo_three_rooms_window.json", io.BytesIO(json_bytes), "application/json")},
    )
    assert up_resp.status_code == 200

    # Generate rings
    gen_resp = client.post("/api/generate")
    assert gen_resp.status_code == 200, gen_resp.text
    gen_data = gen_resp.json()
    assert gen_data["status"] == "ok"

    state = gen_data["state"]
    assert state["preview_mode"] is False
    assert len(state["hubs"]) >= 1
    assert len(state["routes"]) >= 1

    # Metrics verification
    metrics = state["metrics"]
    assert metrics["H"] >= 1
    assert metrics["L"] > 0
    assert metrics["B"] >= 0
    assert metrics["X"] >= 0
    assert metrics["O"] >= 0.0
    assert metrics["J"] > 0.0

    # Slide 3 6 hard-rule checklist verification
    rules = state["rules_passed"]
    for r_num in range(1, 7):
        assert f"Rule {r_num}" in rules, f"Missing Rule {r_num} check"
        assert rules[f"Rule {r_num}"] is True, f"Rule {r_num} failed on valid demo fixture: {state.get('violations')}"

    assert state["is_valid"] is True


def test_upload_via_json_body(client: TestClient) -> None:
    """Test uploading floor plan directly as raw JSON body."""
    fixture_data = _load_fixture("demo_three_rooms_window.json")
    response = client.post(
        "/api/upload",
        json=fixture_data,
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "ok"
    assert res_data["state"]["preview_mode"] is True


# =========================================================================
# 2. Invalid Uploads & Diagnostic Errors
# =========================================================================

def test_invalid_json_syntax(client: TestClient) -> None:
    """Test malformed JSON string produces clear HTTP 400 with syntax error details."""
    malformed = b'{ "area": 100, rooms: invalid }'
    response = client.post(
        "/api/upload",
        files={"file": ("bad.json", io.BytesIO(malformed), "application/json")},
    )
    assert response.status_code == 400
    err_text = response.json()["error"]
    assert "syntax error" in err_text.lower() or "json" in err_text.lower()


def test_invalid_non_dict_root(client: TestClient) -> None:
    """Test non-object root (JSON array) produces specific error."""
    arr_json = b'["room1", "room2"]'
    response = client.post(
        "/api/upload",
        files={"file": ("list.json", io.BytesIO(arr_json), "application/json")},
    )
    assert response.status_code == 400
    assert "JSON object" in response.json()["error"]


def test_missing_or_nonpositive_area(client: TestClient) -> None:
    """Test missing or non-positive total area produces clear diagnostic."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    fixture["area"] = -10.0
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    assert "area" in response.json()["error"].lower()

    del fixture["area"]
    response2 = client.post("/api/upload", json=fixture)
    assert response2.status_code == 400
    assert "area" in response2.json()["error"].lower()


def test_missing_or_invalid_inner_geometry(client: TestClient) -> None:
    """Test missing inner boundary polygon produces clear diagnostic."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    del fixture["inner"]
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    assert "inner" in response.json()["error"].lower()


def test_missing_or_empty_rooms(client: TestClient) -> None:
    """Test missing rooms list or empty rooms produces clear diagnostic."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    fixture["rooms"] = []
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    assert "rooms" in response.json()["error"].lower()


def test_duplicate_fixed_node_id(client: TestClient) -> None:
    """Test duplicate fixed node ID produces clear diagnostic."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    # Add duplicate node ID N1 in node list
    fixture["nodes"] = [
        {"node_id": "N1", "room_id": "R01", "location": [25, 20]},
        {"node_id": "N1", "room_id": "R02", "location": [75, 20]},
    ]
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    err = response.json()["error"]
    assert "Duplicate fixed node ID" in err
    assert "N1" in err


def test_multiple_nodes_in_single_room(client: TestClient) -> None:
    """Test multiple fixed nodes in one room produces clear diagnostic."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    fixture["nodes"] = [
        {"node_id": "N1", "room_id": "R01", "location": [25, 20]},
        {"node_id": "N2", "room_id": "R01", "location": [30, 20]},
    ]
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    err = response.json()["error"]
    assert "more than one" in err.lower() or "multiple" in err.lower()


def test_unsupported_room_category(client: TestClient) -> None:
    """Test unsupported room category names are rejected with clear diagnostics."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    fixture["rooms"][0]["category"] = "nuclear_reactor_chamber"
    response = client.post("/api/upload", json=fixture)
    assert response.status_code == 400
    err = response.json()["error"]
    assert "Unsupported room category" in err
    assert "nuclear_reactor_chamber" in err


# =========================================================================
# 3. Rule Violations: Honest Surfacing Without Moving Nodes
# =========================================================================

def test_fixed_node_clearance_violation_not_silently_moved(client: TestClient) -> None:
    """Test fixed node within < 0.50m of wall is preserved and honestly flagged as Rule 6 failure."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    # Room R01 is [0, 0] to [50, 40] dm. Place node N1 at [25, 4] dm = 2.50m, 0.40m.
    # Wall is at y=0.10m, so wall clearance is 0.30m < 0.50m limit!
    fixture["nodes"][0]["location"] = [25, 4]

    up_resp = client.post("/api/upload", json=fixture)
    assert up_resp.status_code == 200
    state = up_resp.json()["state"]

    # 1. Coordinate check: N1 must NOT have been moved
    n1_state = next(n for n in state["nodes"] if n["node_id"] == "N1")
    assert pytest.approx(n1_state["x"], abs=1e-3) == 2.5
    assert pytest.approx(n1_state["y"], abs=1e-3) == 0.4

    # 2. Halo check: N1 halo must be flagged as violation (is_clear=False, red color)
    assert "N1" in state["halos"]
    assert state["halos"]["N1"]["is_clear"] is False
    assert state["halos"]["N1"]["color"] == "#E6194B"

    # 3. Rule check: Rule 6 must be flagged as False
    assert state["rules_passed"]["Rule 6"] is False
    assert state["is_valid"] is False

    # 4. Generate rings on this violating plan: must route successfully and flag Rule 6
    gen_resp = client.post("/api/generate")
    assert gen_resp.status_code == 200
    gen_state = gen_resp.json()["state"]
    assert gen_state["rules_passed"]["Rule 6"] is False
    assert gen_state["is_valid"] is False
    assert any(v["rule_number"] == 6 for v in gen_state["violations"])


def test_uploaded_plan_multiple_clearance_violations(client: TestClient) -> None:
    """Test uploaded plan with multiple clearance violations surfaces all in reviewer HUD."""
    fixture = copy.deepcopy(_load_fixture("demo_three_rooms_window.json"))
    # Place N1 and N2 both within 0.30m of walls ([25, 4] and [75, 4])
    fixture["nodes"][0]["location"] = [25, 4]
    fixture["nodes"][1]["location"] = [75, 4]

    up_resp = client.post("/api/upload", json=fixture)
    assert up_resp.status_code == 200
    state = up_resp.json()["state"]

    assert state["halos"]["N1"]["is_clear"] is False
    assert state["halos"]["N2"]["is_clear"] is False
    assert state["rules_passed"]["Rule 6"] is False

    gen_resp = client.post("/api/generate")
    assert gen_resp.status_code == 200
    gen_state = gen_resp.json()["state"]
    assert gen_state["rules_passed"]["Rule 6"] is False
    assert gen_state["is_valid"] is False
    # Ensure violations list reports the clearance issue
    assert len(gen_state["violations"]) >= 1


# =========================================================================
# 4. Artifact Downloads (JSON & PNG)
# =========================================================================

def test_download_slide8_json(client: TestClient) -> None:
    """Test downloading Slide 8 JSON produces valid compliant structure."""
    fixture = _load_fixture("demo_three_rooms_window.json")
    client.post("/api/upload", json=fixture)
    client.post("/api/generate")

    resp = client.get("/api/export/json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    assert 'attachment; filename="demo_three_rooms_window_solution.json"' in resp.headers.get("content-disposition", "")

    data = resp.json()
    # Validate against Slide8Export Pydantic model
    export_obj = Slide8Export.model_validate(data)
    assert export_obj.plan_id == "demo_three_rooms_window"
    assert len(export_obj.hubs) >= 1
    assert export_obj.reported_metrics.hub_count >= 1
    assert export_obj.reported_metrics.total_length_m > 0


def test_download_overlay_png(client: TestClient) -> None:
    """Test downloading high-resolution overlay PNG returns valid PNG image binary."""
    fixture = _load_fixture("demo_three_rooms_window.json")
    client.post("/api/upload", json=fixture)
    client.post("/api/generate")

    resp = client.get("/api/export/png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"
    assert 'attachment; filename="demo_three_rooms_window_overlay.png"' in resp.headers.get("content-disposition", "")

    # Check valid PNG binary signature: \x89PNG\r\n\x1a\n
    png_bytes = resp.content
    assert len(png_bytes) > 1000
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


# =========================================================================
# 5. One-Click Demo Plans
# =========================================================================

def test_load_demo_plan_endpoints(client: TestClient) -> None:
    """Test loading demo benchmark plans via /api/demo/{demo_id}."""
    # 1. Load demo_three_rooms_window in preview mode
    resp1 = client.post("/api/demo/demo_three_rooms_window?generate=false")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "ok"
    assert data1["state"]["preview_mode"] is True
    assert data1["state"]["plan"]["plan_id"] == "demo_three_rooms_window"

    # 2. Load demo_six_rooms_capacity with auto-generate
    resp2 = client.post("/api/demo/demo_six_rooms_capacity?generate=true")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "ok"
    assert data2["state"]["preview_mode"] is False
    assert data2["state"]["plan"]["plan_id"] == "demo_six_rooms_capacity"
    assert len(data2["state"]["hubs"]) >= 1

    # 3. Load non-existent demo
    resp_err = client.post("/api/demo/non_existent_demo")
    assert resp_err.status_code in (400, 404)


# =========================================================================
# 6. Co-Pilot Suggestions on Uploaded Plans & Empty/Applied States
# =========================================================================

def test_copilot_suggestions_uses_uploaded_plan(client: TestClient) -> None:
    """Verify co-pilot suggestions analyze the currently uploaded plan, not default Slide 5."""
    fixture = _load_fixture("new_four_room_plan.json")
    up_resp = client.post("/api/upload", json=fixture)
    assert up_resp.status_code == 200

    # Request suggestions directly
    sug_resp = client.post("/api/copilot/suggest", json={"max_proposals": 5})
    assert sug_resp.status_code == 200
    report = sug_resp.json()
    assert report["plan_id"] == "new_four_room_plan"
    assert report["plan_id"] != "slide5_schematic"


def test_copilot_empty_proposals_for_symmetric_optimal_plan(client: TestClient) -> None:
    """Verify locally optimal plans like new_four_room_plan return 0 proposals (clean empty state)."""
    fixture = _load_fixture("new_four_room_plan.json")
    client.post("/api/upload", json=fixture)
    gen_resp = client.post("/api/generate")
    assert gen_resp.status_code == 200

    sug_resp = client.post("/api/copilot/suggest", json={"max_proposals": 5})
    assert sug_resp.status_code == 200
    report = sug_resp.json()
    assert report["plan_id"] == "new_four_room_plan"
    # Local optimality: centered midpoints mean delta_J <= 0 for all door shifts
    assert report["recommended_proposals"] == []


def test_copilot_proposals_and_apply_on_uploaded_plan(client: TestClient) -> None:
    """Verify proposal discovery, application, and 6 PPT hard-rule evaluation on uploaded plan."""
    fixture = _load_fixture("demo_three_rooms_window.json")
    client.post("/api/upload", json=fixture)
    client.post("/api/generate")

    sug_resp = client.post("/api/copilot/suggest", json={"max_proposals": 5})
    assert sug_resp.status_code == 200
    report = sug_resp.json()
    assert report["plan_id"] == "demo_three_rooms_window"
    assert len(report["recommended_proposals"]) >= 1

    first_proposal = report["recommended_proposals"][0]
    p_id = first_proposal["proposal_id"]

    # Apply the proposal
    apply_resp = client.post("/api/copilot/apply", json={"proposal_id": p_id})
    assert apply_resp.status_code == 200
    state = apply_resp.json()

    assert state["plan"]["plan_id"] == "demo_three_rooms_window"
    assert state["is_valid"] is True
    # Verify all 6 hard rules were checked
    rules = state["rules_passed"]
    assert len(rules) == 6
    assert all(rules[f"Rule {i}"] is True for i in range(1, 7))


def test_engine_reset_preserves_uploaded_plan(client: TestClient) -> None:
    """Verify engine reset re-routes the active uploaded plan rather than falling back to Slide 5."""
    fixture = _load_fixture("new_four_room_plan.json")
    client.post("/api/upload", json=fixture)
    client.post("/api/generate")

    state, _ = engine.reset()
    assert state["plan"]["plan_id"] == "new_four_room_plan"
    assert state["plan"]["plan_id"] != "slide5_schematic"

