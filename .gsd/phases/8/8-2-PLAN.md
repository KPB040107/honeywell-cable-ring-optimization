---
phase: 8
plan: 2
wave: 2
gap_closure: false
---

# Plan 8.2: Co-Pilot Studio HUD Integration, Ghost Overlays & One-Click Apply

## Objective
Integrate the Architectural Co-Pilot into the FastAPI WebSockets server (`server.py`, `incremental_router.py`) and the browser-based Co-Design Studio interface (`static/index.html`, `static/style.css`, `static/app.js`). Render interactive ghost doorway overlays on the canvas with floating savings badges (`Save 4.8m (ΔJ = -6.8)`), display ranked recommendation cards in the Reviewer HUD, and enable one-click "Apply Tweak" mutations that recalculate routes and update the scoreboard live.

## Context
- `v2.md` (Section 7, Phase 8)
- `.gsd/SPEC.md`
- `src/resplan_router/phase8_copilot/wall_optimizer.py`
- `src/resplan_router/phase7_interactive/server.py`
- `src/resplan_router/phase7_interactive/incremental_router.py`
- `src/resplan_router/phase7_interactive/static/index.html`
- `src/resplan_router/phase7_interactive/static/app.js`
- `src/resplan_router/phase7_interactive/static/style.css`
- `tests/test_phase8_studio_copilot.py`

## Tasks

<task type="auto">
  <name>Server Co-Pilot Endpoints & Incremental Engine Extension</name>
  <files>
    src/resplan_router/phase7_interactive/incremental_router.py
    src/resplan_router/phase7_interactive/server.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/incremental_router.py`:
       - Add method `get_copilot_recommendations(max_proposals: int = 5) -> CoPilotReport`:
         - Uses `ArchitecturalCoPilot.analyze_floor_plan()` on current in-memory plan and partition.
         - Caches active proposals.
       - Add method `apply_structural_proposal(proposal_id: str) -> tuple[dict, float]`:
         - Finds proposal by ID.
         - Invokes `ArchitecturalCoPilot.apply_proposal()` to mutate the in-memory floor plan.
         - Re-extracts portals and rebuilds local topology.
         - Solves updated partition and synthesized rings.
         - Evaluates with `FloorPlanChecker.evaluate()`.
         - Returns updated full state dictionary and elapsed latency.

    2. Update `src/resplan_router/phase7_interactive/server.py`:
       - Add REST endpoints:
         - `POST /api/copilot/suggest`: Returns `CoPilotReport` JSON containing ranked recommendations.
         - `POST /api/copilot/apply`: Accepts `{"proposal_id": "<id>"}`; applies recommendation and returns updated state.
       - Extend WebSocket `/ws/co-design` handler:
         - Action `copilot_suggest`: Computes and sends `{"type": "copilot_proposals", "report": ...}`.
         - Action `copilot_apply`: Applies proposal and broadcasts `{"type": "state_update", ...}`.

    AVOID: Recomputing recommendations synchronously on every pointer move; trigger only on demand or background idle.
    USE: Cached baseline state to quickly evaluate perturbation deltas.
  </action>
  <verify>
    python -c "from resplan_router.phase7_interactive.server import app; print('Server with Co-Pilot endpoints verified')"
  </verify>
</task>

<task type="auto">
  <name>Studio HUD Co-Pilot UI, Ghost Overlays & Integration Tests</name>
  <files>
    src/resplan_router/phase7_interactive/static/index.html
    src/resplan_router/phase7_interactive/static/style.css
    src/resplan_router/phase7_interactive/static/app.js
    tests/test_phase8_studio_copilot.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/static/index.html`:
       - Add "Suggest Floor-Plan Improvements" button with sparkle icon in toolbar.
       - Add "Co-Pilot Architectural Recommendations" drawer / card deck in the Reviewer HUD sidebar.
       - Add toggle button to show/hide ghost doorways on canvas.

    2. Update `src/resplan_router/phase7_interactive/static/style.css`:
       - Styles for Ghost Doorways on canvas:
         - Cyan accent outline (`#00D2FF`) with glowing dashed border.
         - Floating savings pills (`background: rgba(0, 210, 255, 0.9)`, `color: #0F172A`).
       - Styles for Recommendation Cards:
         - Compact architectural cards showing proposal title, room badges, metric impact chips (`ΔH: -1`, `ΔJ: -6.8`, `ΔL: -4.8m`), and rationale text.
         - Action button: "Apply Tweak" with emerald green highlight on hover.

    3. Update `src/resplan_router/phase7_interactive/static/app.js`:
       - Implement Co-Pilot client functionality:
         - On clicking "Suggest Floor-Plan Improvements", sends `POST /api/copilot/suggest` or WebSocket action.
         - Renders ghost doorways directly on the HTML5 floor-plan canvas at candidate locations with floating delta text.
         - Populates the recommendation cards in the sidebar.
         - On clicking "Apply Tweak", sends `POST /api/copilot/apply`, animates doorway creation, morphs cable polylines live, and refreshes the Reviewer Scoreboard!

    4. Implement `tests/test_phase8_studio_copilot.py`:
       - **Test 1 (`test_copilot_suggest_rest_api`):**
         - Calls `POST /api/copilot/suggest`.
         - Asserts response is 200 OK and contains valid `CoPilotReport` with proposals.
       - **Test 2 (`test_copilot_apply_rest_api`):**
         - Retrieves top proposal from suggest endpoint.
         - Calls `POST /api/copilot/apply` with proposal ID.
         - Asserts plan state updates, new door exists, and cost $J$ or hub count $H$ improves.
       - **Test 3 (`test_copilot_websocket_roundtrip`):**
         - Sends WebSocket message `{"action": "copilot_suggest"}` and verifies received `copilot_proposals` message.
         - Sends `{"action": "copilot_apply", "proposal_id": ...}` and verifies `state_update`.
  </action>
  <verify>
    pytest tests/test_phase8_studio_copilot.py tests/test_phase8_copilot.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase8_copilot.py tests/test_phase8_studio_copilot.py -v`.
- Re-run full test suite `pytest tests/ -v` to ensure zero regressions across all 8 phases.

## Success Criteria
- Co-Pilot recommendations accessible via REST API and WebSockets.
- Interactive canvas displays ghost doorway overlays with floating savings pills.
- One-click "Apply Tweak" automatically mutates floor plan, recalculates routes, and updates the scoreboard.
- 100% test pass rate across entire repository.
