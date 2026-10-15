---
phase: 9
plan: 2
wave: 2
gap_closure: false
---

# Plan 9.2: Studio Scissors Tool, Dual-Direction Signal Pulses & E2E Integration

## Objective
Integrate the NFPA 72 Class A Wire-Cut Simulator into the FastAPI WebSockets server (`server.py`, `incremental_router.py`) and the browser Co-Design Studio (`static/index.html`, `static/style.css`, `static/app.js`). Implement an interactive Scissors Tool (✂️) allowing judges to click anywhere on a cable route to sever it, rendering animated dual-direction signal pulses (electric blue outbound, amber reverse return), live survivability gauges, and one-click circuit healing.

## Context
- `v2.md` (Section 7, Phase 9)
- `.gsd/SPEC.md`
- `src/resplan_router/phase9_survivability/circuit_simulator.py`
- `src/resplan_router/phase7_interactive/server.py`
- `src/resplan_router/phase7_interactive/incremental_router.py`
- `src/resplan_router/phase7_interactive/static/index.html`
- `src/resplan_router/phase7_interactive/static/app.js`
- `src/resplan_router/phase7_interactive/static/style.css`
- `tests/test_phase9_studio_survivability.py`

## Tasks

<task type="auto">
  <name>Server Survivability Endpoints & Incremental Engine Extension</name>
  <files>
    src/resplan_router/phase7_interactive/incremental_router.py
    src/resplan_router/phase7_interactive/server.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/incremental_router.py`:
       - Add `self._active_cut: ClassASimulationReport | None = None`.
       - Method `cut_wire(hub_id: str, cut_x: float, cut_y: float) -> tuple[dict, float]`:
         - Finds matching `CompletedHubRing` for `hub_id`.
         - Calls `ClassACircuitSimulator.simulate_arbitrary_cut_point(ring, cut_x, cut_y)`.
         - Caches report in `self._active_cut`.
         - Returns serialized state dictionary with `"survivability_report"` and elapsed latency.
       - Method `heal_wire() -> tuple[dict, float]`:
         - Clears `self._active_cut`.
         - Returns serialized normal state.
       - Include `"survivability_report"` in `get_state()` payload when cut is active.

    2. Update `src/resplan_router/phase7_interactive/server.py`:
       - Add REST endpoints:
         - `POST /api/survivability/cut`: Accepts `{"hub_id": "H1", "cut_x": 6.8, "cut_y": 1.2}`; returns `ClassASimulationReport`.
         - `POST /api/survivability/heal`: Heals active wire cuts; returns updated normal state.
       - Extend WebSocket `/ws/co-design` handler:
         - Action `wire_cut`: Executes `engine.cut_wire(hub_id, x, y)` and broadcasts `{"type": "survivability_update", "report": ...}`.
         - Action `wire_heal`: Executes `engine.heal_wire()` and broadcasts `{"type": "state_update", ...}`.

    AVOID: Modifying base physical geometries destructively; keep simulation layer non-destructive with clear heal mechanics.
    USE: In-memory simulation caching with instant state restore.
  </action>
  <verify>
    python -c "from resplan_router.phase7_interactive.server import app; print('Server with survivability endpoints verified')"
  </verify>
</task>

<task type="auto">
  <name>Studio HUD Scissors Tool, Animated Dual-Direction Pulses & E2E Tests</name>
  <files>
    src/resplan_router/phase7_interactive/static/index.html
    src/resplan_router/phase7_interactive/static/style.css
    src/resplan_router/phase7_interactive/static/app.js
    tests/test_phase9_studio_survivability.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/static/index.html`:
       - Add Scissors Tool toggle button in the toolbar (`✂️ Cut Wire Tool`).
       - Add "Wire-Cut Survivability Monitor (NFPA 72 Class A)" card in the Reviewer HUD sidebar:
         - Live survivability gauge badge (e.g. `100% ONLINE — NFPA 72 CLASS A`).
         - Circuit fault callout box showing severed leg and coordinates.
         - Branch breakdown listing primary outbound vs. return terminal nodes.
         - "Restore Circuit / Heal Wire" button.

    2. Update `src/resplan_router/phase7_interactive/static/style.css`:
       - Styles for Scissors Tool mode:
         - Crosshair cursor with scissors indicator.
         - Highlight hover color for cables (`#FF007F` bright neon magenta).
       - Styles for wire cut marker:
         - Severed gap indicator with red spark halo (`#E6194B`) and break symbol.
       - Styles for animated signal pulses:
         - Primary Outbound pulses: Electric cyan/blue dash flow (`#00D2FF`).
         - Return Terminal pulses: Amber/gold reverse dash flow (`#FFA500`).
       - Styles for Survivability HUD card:
         - Emerald green survivability badge with shield icon (`🛡️`).
         - Pulse animations using CSS keyframes.

    3. Update `src/resplan_router/phase7_interactive/static/app.js`:
       - Implement Scissors Tool mode:
         - Toggle scissors mode via toolbar button.
         - When scissors mode is active, pointer clicks on canvas detect nearest cable segment within hit radius ($0.3\text{ m}$ in world units).
         - Sends `wire_cut` action via WebSocket (or POST `/api/survivability/cut`).
         - Renders severed gap at cut coordinate.
         - Initiates 60 FPS dual-direction animated pulses along polyline branches:
           - Electric blue pulses moving forward from hub to upstream nodes.
           - Amber pulses moving in reverse from hub return terminal to downstream nodes.
         - Updates node pins with shield badges indicating power source.
         - Clicking "Restore Circuit" sends `wire_heal`, clears animations, and returns to normal closed-ring loop flow.

    4. Implement `tests/test_phase9_studio_survivability.py`:
       - **Test 1 (`test_rest_cut_and_heal_cycle`):**
         - Calls `POST /api/survivability/cut` on Slide 5 Hub 1.
         - Asserts 200 OK, `survivability_pct == 100.0%`, `nfpa72_class_a_compliant is True`.
         - Calls `POST /api/survivability/heal`.
         - Asserts state returns to normal with no active cut.
       - **Test 2 (`test_websocket_wire_cut_and_heal`):**
         - Sends WebSocket message `{"action": "wire_cut", "hub_id": "H1", "cut_x": 6.8, "cut_y": 1.2}`.
         - Asserts `survivability_update` broadcast with complete node statuses.
         - Sends `{"action": "wire_heal"}` and asserts normal state update.
       - **Test 3 (`test_single_door_leaf_cut_ui_payload`):**
         - Cuts wire entering leaf room `Bedroom 2` (Door Lane A).
         - Asserts `Bedroom 2` node `N6` is marked `POWERED_BY: RETURN_TERMINAL` and remains 100% online.
  </action>
  <verify>
    pytest tests/test_phase9_studio_survivability.py tests/test_phase9_survivability.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase9_survivability.py tests/test_phase9_studio_survivability.py -v`.
- Re-run full test suite `pytest tests/ -v` to ensure zero regressions across all 9 phases.

## Success Criteria
- Scissors Tool allows interactive wire cutting on any cable segment.
- 100% device survivability mathematically proven and visually verified via dual-direction signal pulses.
- Restoring circuit heals wire and returns to normal state instantly.
- 100% test pass rate across entire repository.
