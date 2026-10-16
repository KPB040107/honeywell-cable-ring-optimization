"""Integration tests for ResPlan Plan ID REST and WebSocket APIs."""

import pytest
from fastapi.testclient import TestClient
from resplan_router.phase7_interactive.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_get_dataset_status(client):
    res = client.get("/api/dataset/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is True
    assert data["record_count"] == 17000
    assert data["dataset"] == "ResPlan.pkl"


def test_load_dataset_plan_success(client):
    res = client.post("/api/dataset/plans/14433/load")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "metadata" in data
    meta = data["metadata"]
    assert meta["plan_id"] == "14433"
    assert meta["total_area_m2"] > 0
    assert meta["net_area_m2"] > 0
    assert meta["eligible_rooms_count"] > 0
    assert meta["device_count"] > 0

    assert "preview" in data
    assert "state" in data
    state = data["state"]
    assert state["preview_mode"] is True
    assert state["metrics"]["H"] == 0
    assert state["metrics"]["L"] == 0.0


def test_load_dataset_plan_not_found(client):
    res = client.post("/api/dataset/plans/99999999/load")
    assert res.status_code == 404
    data = res.json()
    assert data["status"] == "error"
    assert "not found" in data["error"].lower() or "not found" in data["message"].lower()


def test_load_dataset_plan_invalid_id(client):
    res = client.post("/api/dataset/plans/invalid_str/load")
    assert res.status_code == 400
    data = res.json()
    assert data["status"] == "error"

    res_neg = client.post("/api/dataset/plans/-42/load")
    assert res_neg.status_code == 400


def test_mutate_api_load_plan_id(client):
    res = client.post("/api/mutate", json={"action": "load_plan_id", "plan_id": 14433})
    assert res.status_code == 200
    data = res.json()
    assert data["preview_mode"] is True
    assert data["plan"]["plan_id"] == "14433"


def test_websocket_load_plan_id(client):
    with client.websocket_connect("/ws/co-design") as ws:
        ws.send_json({"action": "load_plan_id", "plan_id": 14433})
        resp = ws.receive_json()
        assert resp["type"] == "state_update"
        assert resp["preview_mode"] is True
        assert resp["plan"]["plan_id"] == "14433"
