"""FastAPI WebSockets Server for Real-Time Floor-Plan Co-Design Canvas.

Provides sub-50 ms bidirectional WebSocket streaming (/ws/co-design) and REST APIs
for real-time floor plan mutations, Slide 8 JSON export, and state reset.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import matplotlib.pyplot as plt
from pydantic import BaseModel

from resplan_router.phase1_ingestion.validator import validate_floor_plan_json
from resplan_router.phase6_output.visualizer import FloorPlanVisualizer
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


class SuggestRequest(BaseModel):
    max_proposals: int = 5


class ProposalApplyRequest(BaseModel):
    proposal_id: str


class WireCutRequest(BaseModel):
    hub_id: Optional[str] = "H1"
    cut_x: float
    cut_y: float


@app.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint confirming engine availability."""
    return {
        "status": "ok",
        "engine": "IncrementalCoDesignEngine",
        "plan_id": engine.plan.plan_id if engine.plan else "none",
    }


@app.get("/api/state")
def get_current_state(include_bim: bool = False) -> Dict[str, Any]:
    """Fetch current floor plan state, metrics, halos, hubs, and routes."""
    return engine.get_state(include_bim=include_bim)


@app.get("/api/bim/scene")
def get_bim_scene_endpoint() -> Dict[str, Any]:
    """Fetch current 3D Building Information Modeling (BIM) scene for client-side Three.js rendering."""
    scene = engine.get_bim_scene()
    return scene.model_dump()


@app.get("/api/bim/export/obj")
def export_bim_obj_endpoint() -> Response:
    """Download formatted Wavefront .obj architectural model file."""
    obj_data = engine.export_obj()
    return Response(
        content=obj_data,
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="resplan_model.obj"'},
    )


@app.post("/api/reset")
def reset_to_baseline() -> Dict[str, Any]:
    """Reset floor plan to baseline Slide 5 benchmark schematic."""
    state, _ = engine.reset()
    return state


@app.post("/api/upload")
async def upload_floor_plan(
    request: Request,
    file: Optional[UploadFile] = File(None),
) -> Any:
    """Upload floor plan JSON via file or JSON body, validate structure, and load preview."""
    payload_to_validate: Any = None
    if file is not None and file.filename:
        try:
            payload_to_validate = await file.read()
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Failed to read file: {exc}", "message": str(exc)},
            )
    else:
        try:
            payload_to_validate = await request.body()
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Failed to read request body: {exc}", "message": str(exc)},
            )

    if not payload_to_validate:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "No file or JSON data uploaded", "message": "No file or JSON data uploaded"},
        )

    is_valid, err_msg, parsed_dict = validate_floor_plan_json(payload_to_validate)
    if not is_valid or parsed_dict is None:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": err_msg or "Invalid floor plan JSON", "message": err_msg},
        )

    try:
        state, _ = engine.load_plan_preview(parsed_dict)
        return {
            "status": "ok",
            "message": f"Floor plan '{engine.plan.plan_id}' loaded into preview mode",
            "state": state,
        }
    except Exception as exc:
        logger.exception("Failed to parse uploaded floor plan")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/generate")
def generate_cable_rings_endpoint() -> Any:
    """Run active floor plan through full routing pipeline and compute closed rings."""
    try:
        state, _ = engine.generate_cable_rings()
        return {
            "status": "ok",
            "message": "Cable rings generated successfully",
            "state": state,
        }
    except Exception as exc:
        logger.exception("Failed to generate cable rings")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/export")
def export_slide8_solution() -> Dict[str, Any]:
    """Export current floor plan routing solution to Slide 8 JSON format."""
    return engine.export_slide8()


@app.get("/api/export/json")
def download_slide8_json() -> Response:
    """Download official Slide 8 solution JSON file."""
    data = engine.export_slide8()
    plan_id = engine.plan.plan_id if engine.plan else "floor_plan"
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{plan_id}_solution.json"'},
    )


@app.get("/api/export/png")
def download_overlay_png() -> Response:
    """Download high-resolution Slide 5 schematic overlay PNG."""
    plan_id = engine.plan.plan_id if engine.plan else "floor_plan"
    if getattr(engine, "_preview_mode", False) or not engine.bundle or not engine.bundle.hub_rings:
        try:
            engine.generate_cable_rings()
        except Exception as exc:
            logger.warning("Could not auto-generate rings for PNG export: %s", exc)

    fig = FloorPlanVisualizer.render_overlay(
        plan=engine.plan,
        bundle=engine.bundle,
        evaluation=engine._last_report,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{plan_id}_overlay.png"'},
    )


@app.api_route("/api/demo/{demo_id}", methods=["GET", "POST"])
def load_demo_endpoint(demo_id: str, generate: bool = False) -> Any:
    """Load one of the demo benchmark floor plans."""
    try:
        state, _ = engine.load_demo_plan(demo_id, generate=generate)
        return {
            "status": "ok",
            "message": f"Loaded demo plan '{demo_id}'",
            "state": state,
        }
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except FileNotFoundError as exc:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(exc)})


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


@app.post("/api/copilot/suggest")
def copilot_suggest_http(req: Optional[SuggestRequest] = None) -> Any:
    """Return ranked floor-plan structural improvement proposals generated by Architectural Co-Pilot."""
    try:
        max_p = req.max_proposals if req is not None else 5
        report = engine.get_copilot_recommendations(max_proposals=max_p)
        return report.model_dump()
    except Exception as exc:
        logger.exception("Co-Pilot suggest failed")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/copilot/apply")
def copilot_apply_http(req: ProposalApplyRequest) -> Any:
    """Apply an architectural co-pilot recommendation and re-route floor plan."""
    try:
        state, _ = engine.apply_structural_proposal(req.proposal_id)
        return state
    except Exception as exc:
        logger.exception("Co-Pilot apply failed")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": str(exc), "message": str(exc)},
        )


@app.post("/api/survivability/cut")
def cut_wire_http(req: WireCutRequest) -> Dict[str, Any]:
    """Sever a cable leg and compute NFPA 72 Class A device survivability."""
    state, _ = engine.cut_wire(req.hub_id or "H1", req.cut_x, req.cut_y)
    if engine._active_cut is not None:
        return engine._active_cut.model_dump()
    return state


@app.post("/api/survivability/heal")
def heal_wire_http() -> Dict[str, Any]:
    """Restore severed wire cuts to normal closed loop."""
    state, _ = engine.heal_wire()
    return state


class MutationRequest(BaseModel):
    action: str
    node_id: Optional[str] = None
    hub_id: Optional[str] = None
    door_id: Optional[str] = None
    room_id: Optional[str] = None
    proposal_id: Optional[str] = None
    demo_id: Optional[str] = None
    plan_data: Optional[Dict[str, Any]] = None
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

    elif action == "copilot_apply":
        if not req.proposal_id:
            return JSONResponse(
                status_code=400,
                content={"error": "copilot_apply requires proposal_id"},
            )
        state, _ = engine.apply_structural_proposal(req.proposal_id)
        return state

    elif action == "upload":
        if not req.plan_data:
            return JSONResponse(
                status_code=400,
                content={"error": "upload action requires plan_data"},
            )
        is_val, err, valid_dict = validate_floor_plan_json(req.plan_data)
        if not is_val or valid_dict is None:
            return JSONResponse(
                status_code=400,
                content={"error": err or "Invalid floor plan JSON"},
            )
        state, _ = engine.load_plan_preview(valid_dict)
        return state

    elif action == "generate_rings":
        state, _ = engine.generate_cable_rings()
        return state

    elif action == "load_demo":
        d_id = req.demo_id or "slide5"
        state, _ = engine.load_demo_plan(d_id, generate=True)
        return state

    elif action == "reset":
        state, _ = engine.reset()
        return state

    elif action == "get_state":
        return engine.get_state()

    elif action == "wire_cut":
        cut_x = req.x if req.x is not None else 0.0
        cut_y = req.y if req.y is not None else 0.0
        state, _ = engine.cut_wire(req.hub_id or "H1", cut_x, cut_y)
        return state

    elif action == "wire_heal":
        state, _ = engine.heal_wire()
        return state

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
      - {"action": "copilot_suggest", "max_proposals": 5}
      - {"action": "copilot_apply", "proposal_id": "<id>"}
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

            elif action == "copilot_suggest":
                max_proposals = int(data.get("max_proposals", 5))
                try:
                    report = engine.get_copilot_recommendations(max_proposals=max_proposals)
                    await websocket.send_json({
                        "type": "copilot_proposals",
                        "report": report.model_dump(),
                    })
                except Exception as exc:
                    logger.exception("WebSocket copilot_suggest failed")
                    await websocket.send_json({
                        "type": "copilot_error",
                        "error": str(exc),
                        "message": str(exc),
                    })

            elif action == "copilot_apply":
                proposal_id = str(data.get("proposal_id", ""))
                try:
                    state, _ = engine.apply_structural_proposal(proposal_id)
                    await websocket.send_json(state)
                except Exception as exc:
                    logger.exception("WebSocket copilot_apply failed")
                    await websocket.send_json({
                        "type": "copilot_error",
                        "error": str(exc),
                        "message": str(exc),
                    })

            elif action == "reset":
                state, _ = engine.reset()
                await websocket.send_json(state)

            elif action == "get_state":
                include_bim = bool(data.get("include_bim", False))
                state = engine.get_state(include_bim=include_bim)
                await websocket.send_json(state)

            elif action == "get_bim_scene":
                scene = engine.get_bim_scene()
                await websocket.send_json({
                    "type": "bim_scene_update",
                    "scene": scene.model_dump(),
                })

            elif action == "wire_cut":
                hub_id = str(data.get("hub_id", ""))
                cut_x = float(data.get("cut_x", data.get("x", 0.0)))
                cut_y = float(data.get("cut_y", data.get("y", 0.0)))
                state, _ = engine.cut_wire(hub_id, cut_x, cut_y)
                report_dict = engine._active_cut.model_dump() if engine._active_cut else {}
                resp = dict(state)
                resp["type"] = "survivability_update"
                resp["report"] = report_dict
                await websocket.send_json(resp)

            elif action == "upload":
                plan_data = data.get("plan_data") or data.get("plan")
                if not plan_data:
                    await websocket.send_json({"type": "error", "message": "upload action requires plan_data"})
                else:
                    is_val, err, valid_dict = validate_floor_plan_json(plan_data)
                    if not is_val or valid_dict is None:
                        await websocket.send_json({"type": "error", "message": err or "Invalid floor plan JSON"})
                    else:
                        state, _ = engine.load_plan_preview(valid_dict)
                        await websocket.send_json(state)

            elif action == "generate_rings":
                try:
                    state, _ = engine.generate_cable_rings()
                    await websocket.send_json(state)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "load_demo":
                demo_id = str(data.get("demo_id", "slide5"))
                gen = bool(data.get("generate", False))
                try:
                    state, _ = engine.load_demo_plan(demo_id, generate=gen)
                    await websocket.send_json(state)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})

            elif action == "wire_heal":
                state, _ = engine.heal_wire()
                resp = dict(state)
                resp["type"] = "state_update"
                await websocket.send_json(resp)

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
