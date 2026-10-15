"""Phase 8 Architectural Co-Pilot Studio HUD, REST & WebSocket Integration Tests.

Validates:
1. REST API endpoint POST /api/copilot/suggest returns ranked CoPilotReport with proposals.
2. REST API endpoint POST /api/copilot/apply mutates floor-plan geometry, updates topology/routes, and improves cost J or hub count H.
3. WebSocket /ws/co-design bidirectional communication for copilot_suggest and copilot_apply actions.
4. Mutation via POST /api/mutate with action=copilot_apply.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from resplan_router.phase7_interactive.server import app, engine


@pytest.fixture(autouse=True)
def reset_engine_baseline():
    """Ensure engine is reset to baseline before each test."""
    engine.reset()
    yield
    engine.reset()


def test_copilot_suggest_rest_api() -> None:
    """Test 1: POST /api/copilot/suggest returns ranked proposals in valid CoPilotReport schema."""
    client = TestClient(app)

    # 1. Test with default parameters (empty body or specified max_proposals)
    resp = client.post("/api/copilot/suggest", json={"max_proposals": 5})
    assert resp.status_code == 200, f"Suggest endpoint failed: {resp.text}"

    data = resp.json()
    assert "plan_id" in data
    assert "baseline_hubs" in data
    assert "baseline_cost_J" in data
    assert "proposals_evaluated" in data
    assert data["proposals_evaluated"] > 0
    assert "recommended_proposals" in data

    proposals = data["recommended_proposals"]
    assert len(proposals) > 0, "Expected at least 1 recommended structural proposal"
    assert len(proposals) <= 5

    # Verify top proposal structure
    top = proposals[0]
    assert "proposal_id" in top
    assert "proposal_type" in top
    assert "room_a_id" in top
    assert "room_b_id" in top
    assert "wall_segment" in top
    assert len(top["wall_segment"]) == 2
    assert "suggested_door_center" in top
    assert "door_width_m" in top
    assert top["door_width_m"] >= 0.50
    assert "delta_hubs" in top
    assert "delta_cost_J" in top
    assert "delta_length_m" in top
    assert "delta_bends" in top
    assert "rationale" in top
    assert len(top["rationale"]) > 0

    # Lexicographic ordering check: top must have >= delta_hubs or >= delta_cost_J
    for i in range(len(proposals) - 1):
        p_curr = proposals[i]
        p_next = proposals[i + 1]
        assert (p_curr["delta_hubs"], p_curr["delta_cost_J"]) >= (
            p_next["delta_hubs"],
            p_next["delta_cost_J"],
        ), "Proposals must be ranked lexicographically by (delta_hubs, delta_cost_J)"


def test_copilot_apply_rest_api() -> None:
    """Test 2: POST /api/copilot/apply applies proposal, punches doorway, updates routes and metrics."""
    client = TestClient(app)

    # 1. Fetch suggestions to get a valid proposal
    s_resp = client.post("/api/copilot/suggest", json={"max_proposals": 5})
    assert s_resp.status_code == 200
    s_data = s_resp.json()
    assert len(s_data["recommended_proposals"]) > 0

    top_proposal = s_data["recommended_proposals"][0]
    proposal_id = top_proposal["proposal_id"]
    base_cost_J = s_data["baseline_cost_J"]
    base_hubs = s_data["baseline_hubs"]

    # 2. Apply proposal
    a_resp = client.post("/api/copilot/apply", json={"proposal_id": proposal_id})
    assert a_resp.status_code == 200, f"Apply endpoint failed: {a_resp.text}"

    state = a_resp.json()
    assert state["type"] == "state_update"
    assert state["is_valid"] is True
    assert "metrics" in state
    assert "routes" in state
    assert len(state["routes"]) > 0

    new_cost_J = state["metrics"]["J"]
    new_hubs = state["metrics"]["H"]

    # Cost or hubs must improve
    assert (
        new_cost_J < base_cost_J or new_hubs < base_hubs
    ), f"Expected cost improvement: baseline J={base_cost_J}, new J={new_cost_J}; baseline H={base_hubs}, new H={new_hubs}"

    # 3. Verify door or passage geometry now connects room_a and room_b in topology
    room_a = top_proposal["room_a_id"]
    room_b = top_proposal["room_b_id"]
    connected_rooms = set()
    for p in engine.topology.portals:
        pair = {p.room1_id, p.room2_id}
        if room_a in pair and room_b in pair:
            connected_rooms.add((room_a, room_b))

    assert len(connected_rooms) > 0, f"Expected portal connecting {room_a} and {room_b}"


def test_copilot_websocket_roundtrip() -> None:
    """Test 3: WebSocket actions copilot_suggest and copilot_apply stream real-time updates."""
    client = TestClient(app)

    with client.websocket_connect("/ws/co-design") as ws:
        # 1. Request Co-Pilot suggestions
        ws.send_json({"action": "copilot_suggest", "max_proposals": 3})
        resp_suggest = ws.receive_json()

        assert resp_suggest["type"] == "copilot_proposals"
        assert "report" in resp_suggest
        report = resp_suggest["report"]
        assert "recommended_proposals" in report
        assert len(report["recommended_proposals"]) > 0

        top_proposal = report["recommended_proposals"][0]
        prop_id = top_proposal["proposal_id"]

        # 2. Apply top proposal via WebSocket
        ws.send_json({"action": "copilot_apply", "proposal_id": prop_id})
        resp_state = ws.receive_json()

        assert resp_state["type"] == "state_update"
        assert resp_state["is_valid"] is True
        assert "metrics" in resp_state
        assert "routes" in resp_state
        assert resp_state["metrics"]["J"] > 0


def test_copilot_mutate_endpoint_fallback() -> None:
    """Test 4: POST /api/mutate with action=copilot_apply executes successfully."""
    client = TestClient(app)

    s_resp = client.post("/api/copilot/suggest", json={"max_proposals": 3})
    top_proposal = s_resp.json()["recommended_proposals"][0]
    prop_id = top_proposal["proposal_id"]

    m_resp = client.post("/api/mutate", json={"action": "copilot_apply", "proposal_id": prop_id})
    assert m_resp.status_code == 200
    state = m_resp.json()
    assert state["type"] == "state_update"
    assert state["is_valid"] is True
