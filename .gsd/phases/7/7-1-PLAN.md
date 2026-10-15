---
phase: 7
plan: 1
wave: 1
gap_closure: false
---

# Plan 7.1: Real-Time Incremental Re-Routing Engine & WebSockets Server

## Objective
Implement a sub-50 ms incremental re-routing engine (`incremental_router.py`), a live $0.5\text{ m}$ wall-clearance halo tracker (`halo_tracker.py`), and a high-performance FastAPI WebSockets server (`server.py`) that processes real-time floor plan mutations (node dragging, hub repositioning, door sliding, and wall boundary shifts) with instantaneous metric recalculation.

## Context
- `v2.md` (Section 7, Phase 7)
- `.gsd/SPEC.md`
- `src/resplan_router/phase1_ingestion/parser.py`
- `src/resplan_router/phase2_topology/portal_extractor.py`
- `src/resplan_router/phase3_micro_routing/astar_rectilinear.py`
- `src/resplan_router/phase4_partitioning/zone_milp.py`
- `src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py`
- `src/resplan_router/phase6_output/checker.py`

## Tasks

<task type="auto">
  <name>Incremental Router & Wall-Clearance Halo Tracker</name>
  <files>
    src/resplan_router/phase7_interactive/__init__.py
    src/resplan_router/phase7_interactive/halo_tracker.py
    src/resplan_router/phase7_interactive/incremental_router.py
  </files>
  <action>
    1. Create `src/resplan_router/phase7_interactive/__init__.py`.
    2. Implement `src/resplan_router/phase7_interactive/halo_tracker.py`:
       - Class `ClearanceHaloTracker`:
         - Method `check_clearance(point: Point2D, plan: MetricFloorPlan, required_clearance_m: float = 0.50) -> dict`:
           - Computes exact distance from `point` to obstacle geometry (`plan.walls_and_windows`).
           - Determines if `point` is strictly inside an eligible room.
           - Returns dictionary:
             ```python
             {
                 "is_clear": bool,          # True iff distance >= required_clearance_m - 0.001
                 "distance_m": float,       # distance to nearest wall/window in metres
                 "required_clearance_m": 0.50,
                 "halo_radius_m": 0.50,
                 "color": "#2ECC71" if is_clear else "#E6194B",  # Green if valid, crimson if violation
                 "status_text": "CLEAR (>= 0.5m)" if is_clear else f"TOO CLOSE ({distance_m:.2f}m < 0.50m)"
             }
             ```
    3. Implement `src/resplan_router/phase7_interactive/incremental_router.py`:
       - Class `IncrementalCoDesignEngine`:
         - Maintains state of the active plan (`MetricFloorPlan`), topology (`MacroTopologyGraph`), partition (`HubZonePartition`), and route bundle (`SynthesizedRingsBundle`).
         - Method `update_node_position(node_id: str, new_x: float, new_y: float) -> tuple[dict, float]`:
           - Moves fixed node `node_id` to `(new_x, new_y)`.
           - Updates local Hanan grid lines and only re-computes pairwise routes involving `node_id`.
           - Re-runs direction-aware Held-Karp dynamic programming for the affected hub ring ($m \le 5$).
           - Evaluates with `FloorPlanChecker.evaluate()` to ensure live compliance.
           - Measures execution wall-clock time and guarantees $< 50\text{ ms}$ (target $< 30\text{ ms}$).
         - Method `update_hub_position(hub_id: str, new_x: float, new_y: float) -> tuple[dict, float]`:
           - Moves hub `hub_id` to `(new_x, new_y)`.
           - Recalculates departure/arrival routes and wrap-around turn accounting in $< 35\text{ ms}$.
         - Method `slide_door(door_id: str, offset_m: float) -> tuple[dict, float]`:
           - Shifts door along wall, updating portal lane coordinates and connected room paths.
         - Method `shift_room_boundary(room_id: str, delta_area_m2: float) -> tuple[dict, float]`:
           - Perturbs room polygon; if area crosses $100\text{ m}^2$, triggers fast CP-SAT zone re-partitioning.
         - Returns serializable state payload with updated polylines, metrics, rules checklist, and elapsed `latency_ms`.

    AVOID: Full cold-start pipeline recomputation from raw files on every pointer move.
    USE: In-memory cache of graph topology and warm-start incremental micro-routing.
  </action>
  <verify>
    python -c "from resplan_router.phase7_interactive.incremental_router import IncrementalCoDesignEngine; print('Incremental engine imported')"
  </verify>
</task>

<task type="auto">
  <name>FastAPI WebSockets Server & Latency Benchmark Tests</name>
  <files>
    src/resplan_router/phase7_interactive/server.py
    tests/test_phase7_interactive.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase7_interactive/server.py`:
       - FastAPI application with WebSocket endpoint `/ws/co-design` and REST endpoints:
         - `GET /health`: Returns `{"status": "ok", "engine": "IncrementalCoDesignEngine"}`.
         - `GET /api/state`: Returns current full state (plan, hubs, nodes, polylines, metrics, rules, halos).
         - `POST /api/reset`: Resets plan to baseline Slide 5 schematic.
         - `POST /api/export`: Returns official Slide 8 JSON export.
         - `WebSocket /ws/co-design`:
           - Accepts client JSON messages:
             - `{"action": "drag_node", "node_id": "N1", "x": 6.8, "y": 1.4}`
             - `{"action": "drag_hub", "hub_id": "H1", "x": 5.0, "y": 3.8}`
             - `{"action": "slide_door", "door_id": "P01", "offset_m": 0.2}`
             - `{"action": "shift_wall", "room_id": "R01", "delta_area_m2": 5.0}`
             - `{"action": "reset"}`
           - Dispatches to `IncrementalCoDesignEngine`, measures latency, and broadcasts state update:
             ```json
             {
               "type": "state_update",
               "latency_ms": 18.4,
               "metrics": {"H": 2, "L": 45.3, "B": 18, "X": 0, "O": 0.0, "J": 54.3},
               "rules_passed": {"Rule 1": true, "Rule 2": true, "Rule 3": true, "Rule 4": true, "Rule 5": true, "Rule 6": true},
               "halos": {"N1": {"is_clear": true, "distance_m": 0.62, "color": "#2ECC71"}},
               "hubs": [...],
               "routes": [...]
             }
             ```
    2. Implement `tests/test_phase7_interactive.py`:
       - **Test 1 (`test_halo_clearance_tracker`):**
         - Verifies point with distance $\ge 0.50\text{ m}$ produces `is_clear == True` and green `#2ECC71`.
         - Verifies point with distance $< 0.50\text{ m}$ (e.g. $0.30\text{ m}$) produces `is_clear == False` and red `#E6194B`.
       - **Test 2 (`test_incremental_node_drag_latency`):**
         - Drags `N1` to multiple coordinates inside `Living Room`.
         - Asserts re-routing produces valid closed ring and execution latency is strictly $< 50\text{ ms}$ (empirically $< 35\text{ ms}$).
       - **Test 3 (`test_incremental_hub_drag_latency`):**
         - Drags `H1` within $0.5\text{ m}$ safe zone.
         - Asserts arrival/departure polylines update and latency is $< 50\text{ ms}$.
       - **Test 4 (`test_websocket_roundtrip`):**
         - Uses `starlette.testclient.TestClient` WebSocket test client to send `drag_node` and assert `state_update` response format.
       - **Test 5 (`test_rule_violation_live_detection`):**
         - Drags node into wall ($0.1\text{ m}$ clearance) or outside room.
         - Asserts `state_update` marks Rule 6 or Rule 5 violation with red halo.
  </action>
  <verify>
    pytest tests/test_phase7_interactive.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase7_interactive.py -v`.
- Benchmark execution latency: verify average node/hub drag recalculation takes $< 50\text{ ms}$.
- Run full regression suite `pytest tests/ -v`.

## Success Criteria
- Sub-50 ms incremental re-routing latency achieved on node/hub drag events.
- Live $0.5\text{ m}$ wall-clearance halo correctly transitions between green (`#2ECC71`) and crimson (`#E6194B`).
- FastAPI WebSockets server dispatches live mutations and broadcasts verified `FloorPlanChecker` metrics.
