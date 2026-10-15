---
phase: 7
plan: 2
wave: 2
gap_closure: false
---

# Plan 7.2: Interactive Browser Co-Design Studio & Live Reviewer HUD

## Objective
Build the browser-based Architectural Co-Design Studio interface (`static/index.html`, `static/app.js`, `static/style.css`), featuring an interactive floor-plan canvas with draggable nodes and hubs, dynamic $0.5\text{ m}$ wall-clearance halos (green/red), real-time cable morphing, a live scoreboard HUD with Slide 3 checklist, one-click Slide 8 JSON export, and a `--serve` CLI launcher.

## Context
- `v2.md` (Section 7, Phase 7)
- `.gsd/SPEC.md`
- `src/resplan_router/phase7_interactive/server.py`
- `src/resplan_router/phase7_interactive/incremental_router.py`
- `src/resplan_router/cli.py`
- `tests/test_phase7_studio.py`

## Tasks

<task type="auto">
  <name>Interactive Browser Co-Design Studio Frontend</name>
  <files>
    src/resplan_router/phase7_interactive/static/index.html
    src/resplan_router/phase7_interactive/static/style.css
    src/resplan_router/phase7_interactive/static/app.js
  </files>
  <action>
    1. Create directory `src/resplan_router/phase7_interactive/static`.
    2. Implement `src/resplan_router/phase7_interactive/static/index.html`:
       - Modern, responsive two-column architectural studio layout matching Slide 5 styling:
         - **Left Viewport (Canvas Container):**
           - Interactive HTML5 Canvas / SVG rendering the floor plan.
           - Controls toolbar: Zoom in/out, fit to screen, reset view, toggle halo visibility, auto-route.
         - **Right Sidebar (Live Reviewer HUD):**
           - Header with live connection status pill (`Connected: 60 FPS`, `Latency: < 25 ms`).
           - **Competition Metrics Scoreboard:**
             - Large live readouts for $H$ (Hubs), $L$ (Cable Length in metres), $B$ (Bends), $X$ (Crossings), $O$ (Overlap in metres), and Total Cost $J = L + 5X + O + 0.5B$.
             - Area Gap $\Delta A_{\text{gap}}$ tie-breaker badge.
           - **Slide 3 Hard Validity Checklist:**
             - Interactive cards for Rules 1–6 showing real-time `[PASS]` (green) or `[FAIL]` (crimson).
             - Expands with specific diagnostic callout if a rule is violated (e.g. `Hub H1 within 0.32m of wall!`).
           - **Per-Hub Summary Cards:**
             - Zone area, assigned nodes, ring length, bends, cost $J$ per hub.
           - **Actions:**
             - "Export Slide 8 JSON" button (downloads JSON file instantly).
             - "Reset to Baseline" button.
             - "Run Auto-Optimizer" button.

    3. Implement `src/resplan_router/phase7_interactive/static/style.css`:
       - Clean architectural aesthetic:
         - Dark slate UI accents (`#1E293B`, `#0F172A`).
         - Pastel room fills matching Slide 5 (`#DCE8F8`, `#FDE5D2`, `#E8F5E9`).
         - High-contrast route lines (`#1F77B4`, `#E6550D`).
         - Halo glows: emerald green (`rgba(46, 204, 113, 0.35)`) and crimson alert (`rgba(230, 25, 75, 0.45)`).
         - High DPI retina display support.

    4. Implement `src/resplan_router/phase7_interactive/static/app.js`:
       - Robust WebSocket client with auto-reconnection and HTTP fallback.
       - Interactive pointer interaction:
         - Drag-and-drop for fixed nodes (`N1`..`Nn`) and hubs (`H1`..`Hk`).
         - Real-time throttling via `requestAnimationFrame` for 60 FPS fluidity.
         - Live $0.5\text{ m}$ wall-clearance halo rendered around dragged elements:
           - Green halo when safe ($\ge 0.5\text{ m}$).
           - Pulsing red halo when violating Rule 6 ($< 0.5\text{ m}$).
         - Cable polylines update instantly with directional arrows and numbered badges.
         - Door drag handles to slide interior doors along walls.
       - One-click JSON export triggering browser file download of `Slide 8` format.
  </action>
  <verify>
    python -c "import os; assert os.path.exists('src/resplan_router/phase7_interactive/static/index.html'); print('Static files verified')"
  </verify>
</task>

<task type="auto">
  <name>Studio Integration Tests & CLI Launcher</name>
  <files>
    src/resplan_router/phase7_interactive/server.py
    src/resplan_router/cli.py
    tests/test_phase7_studio.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/server.py`:
       - Mount `StaticFiles` on `/` serving the interactive studio frontend at `http://localhost:8000/`.
       - Add REST endpoint `POST /api/mutate` allowing HTTP-based programmatic mutations.

    2. Update `src/resplan_router/cli.py`:
       - Add `--serve` flag and `--port` argument (default `8000`).
       - When `--serve` is specified, launches the FastAPI server with `uvicorn` and prints a clickable local URL (`http://localhost:8000`).

    3. Implement `tests/test_phase7_studio.py`:
       - **Test 1 (`test_static_files_served`):**
         - Verifies `GET /` returns HTML containing the studio title and canvas element.
         - Verifies `GET /style.css` and `GET /app.js` return 200 OK with correct content types.
       - **Test 2 (`test_http_mutation_api`):**
         - Sends `POST /api/mutate` moving node `N1`.
         - Asserts response contains updated metrics, routes, and clearance status.
       - **Test 3 (`test_slide8_json_export_endpoint`):**
         - Calls `POST /api/export` and verifies downloaded JSON matches Slide 8 schema and passes `Slide8Export.model_validate()`.
       - **Test 4 (`test_cli_serve_flag_help`):**
         - Asserts `--serve` option is present in `python -m resplan_router.cli --help`.
  </action>
  <verify>
    pytest tests/test_phase7_studio.py tests/test_phase7_interactive.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase7_interactive.py tests/test_phase7_studio.py -v`.
- Re-run full regression suite `pytest tests/ -v` to ensure zero regressions across Phases 1 through 7.
- Verify CLI `--serve` options work as expected.

## Success Criteria
- Browser-based studio renders interactive floor-plan canvas matching Slide 5 styling.
- Dragging nodes and hubs dynamically updates polylines, metrics, and clearance halos in real-time.
- One-click Slide 8 JSON export works from browser and REST API.
- All unit and integration tests pass with 100% success rate.
