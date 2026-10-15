"""FastAPI WebSockets Server for Real-Time Floor-Plan Co-Design Canvas.

Provides sub-50 ms bidirectional WebSocket streaming (/ws/co-design) and REST APIs
for real-time floor plan mutations, Slide 8 JSON export, and state reset.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine

logger = logging.getLogger(__name__)

# Global singleton engine instance
engine = IncrementalCoDesignEngine()

app = FastAPI(
    title="ResPlan Real-Time Co-Design Engine",
    description="Sub-50 ms incremental rectilinear re-routing and interactive reviewer HUD",
    version="2.0.0",
)

# Enable CORS for local web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NodeDragRequest(BaseModel):
    node_id: str
    x: float
    y: float


class HubDragRequest(BaseModel):
    hub_id: str
    x: float
    y: float


class DoorSlideRequest(BaseModel):
    door_id: str
    offset_m: float = 0.0


class WallShiftRequest(BaseModel):
    room_id: str
    delta_area_m2: float = 0.0


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint confirming engine availability."""
    return {
        "status": "ok",
        "engine": "IncrementalCoDesignEngine",
        "plan_id": engine.plan.plan_id if engine.plan else "none",
    }


@app.get("/api/state")
def get_current_state() -> Dict[str, Any]:
    """Fetch current floor plan state, metrics, halos, hubs, and routes."""
    return engine.get_state()


@app.post("/api/reset")
def reset_to_baseline() -> Dict[str, Any]:
    """Reset floor plan to baseline Slide 5 benchmark schematic."""
    state, _ = engine.reset()
    return state


@app.post("/api/export")
def export_slide8_solution() -> Dict[str, Any]:
    """Export current floor plan routing solution to Slide 8 JSON format."""
    return engine.export_slide8()


@app.post("/api/node/drag")
def drag_node_http(req: NodeDragRequest) -> Dict[str, Any]:
    """HTTP endpoint to move a device node."""
    state, _ = engine.update_node_position(req.node_id, req.x, req.y)
    return state


@app.post("/api/hub/drag")
def drag_hub_http(req: HubDragRequest) -> Dict[str, Any]:
    """HTTP endpoint to move a hub."""
    state, _ = engine.update_hub_position(req.hub_id, req.x, req.y)
    return state


class MutationRequest(BaseModel):
    action: str
    node_id: Optional[str] = None
    hub_id: Optional[str] = None
    door_id: Optional[str] = None
    room_id: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    offset_m: Optional[float] = 0.0
    delta_area_m2: Optional[float] = 0.0


@app.post("/api/mutate")
def mutate_http(req: MutationRequest) -> Any:
    """Execute programmatic floor plan mutation via HTTP REST."""
    action = req.action.lower()
    if action == "drag_node":
        if req.node_id is None or req.x is None or req.y is None:
            return JSONResponse(
                status_code=400,
                content={"error": "drag_node requires node_id, x, and y"},
            )
        state, _ = engine.update_node_position(req.node_id, req.x, req.y)
        return state

    elif action == "drag_hub":
        if req.hub_id is None or req.x is None or req.y is None:
            return JSONResponse(
                status_code=400,
                content={"error": "drag_hub requires hub_id, x, and y"},
            )
        state, _ = engine.update_hub_position(req.hub_id, req.x, req.y)
        return state

    elif action == "slide_door":
        if req.door_id is None:
            return JSONResponse(
                status_code=400,
                content={"error": "slide_door requires door_id"},
            )
        state, _ = engine.slide_door(req.door_id, req.offset_m or 0.0)
        return state

    elif action == "shift_wall":
        if req.room_id is None:
            return JSONResponse(
                status_code=400,
                content={"error": "shift_wall requires room_id"},
            )
        state, _ = engine.shift_room_boundary(req.room_id, req.delta_area_m2 or 0.0)
        return state

    elif action == "reset":
        state, _ = engine.reset()
        return state

    elif action == "get_state":
        return engine.get_state()

    else:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unsupported mutation action '{req.action}'"},
        )


@app.websocket("/ws/co-design")
async def websocket_co_design(websocket: WebSocket) -> None:
    """Sub-50 ms bidirectional WebSocket channel for live interactive co-design canvas.

    Accepts action payloads:
      - {"action": "drag_node", "node_id": "N1", "x": 6.8, "y": 1.4}
      - {"action": "drag_hub", "hub_id": "H1", "x": 5.0, "y": 3.8}
      - {"action": "slide_door", "door_id": "P01", "offset_m": 0.2}
      - {"action": "shift_wall", "room_id": "R01", "delta_area_m2": 5.0}
      - {"action": "reset"}
      - {"action": "get_state"}

    Broadcasts updated state payload:
      {
        "type": "state_update",
        "latency_ms": 18.4,
        "metrics": {"H": 2, "L": 45.3, "B": 18, "X": 0, "O": 0.0, "J": 54.3},
        "rules_passed": {"Rule 1": true, ...},
        "halos": {"N1": {"is_clear": true, "distance_m": 0.62, "color": "#2ECC71"}},
        "hubs": [...],
        "routes": [...]
      }
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")

            if action == "drag_node":
                node_id = str(data.get("node_id", ""))
                x = float(data.get("x", 0.0))
                y = float(data.get("y", 0.0))
                state, _ = engine.update_node_position(node_id, x, y)
                await websocket.send_json(state)

            elif action == "drag_hub":
                hub_id = str(data.get("hub_id", ""))
                x = float(data.get("x", 0.0))
                y = float(data.get("y", 0.0))
                state, _ = engine.update_hub_position(hub_id, x, y)
                await websocket.send_json(state)

            elif action == "slide_door":
                door_id = str(data.get("door_id", ""))
                offset_m = float(data.get("offset_m", 0.0))
                state, _ = engine.slide_door(door_id, offset_m)
                await websocket.send_json(state)

            elif action == "shift_wall":
                room_id = str(data.get("room_id", ""))
                delta_area_m2 = float(data.get("delta_area_m2", 0.0))
                state, _ = engine.shift_room_boundary(room_id, delta_area_m2)
                await websocket.send_json(state)

            elif action == "reset":
                state, _ = engine.reset()
                await websocket.send_json(state)

            elif action == "get_state":
                state = engine.get_state()
                await websocket.send_json(state)

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unsupported co-design action '{action}'",
                })

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/co-design")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)


# Mount static assets directory if available (for Wave 2 studio frontend)
static_dir = Path(__file__).parent / "static"
if static_dir.exists() and (static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
