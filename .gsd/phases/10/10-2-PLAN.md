---
phase: 10
plan: 2
wave: 2
gap_closure: false
---

# Plan 10.2: Synchronized 2D/3D BIM Split-Screen, WebGL Viewer & Live HUD

## Objective
Integrate the 3D Building Information Modeling (BIM) viewer into the browser-based Co-Design Studio (`static/index.html`, `static/style.css`, `static/app.js`). Implement a synchronized 2D/3D split-screen layout with Three.js WebGL rendering, orbit controls with camera presets, live real-time bidirectional synchronization, an enhanced Reviewer HUD detailing all metric components ($H, L, 5X, O, 0.5B, J, \Delta A_{\text{gap}}$), and one-click Wavefront OBJ architectural download.

## Context
- `v2.md` (Section 7, Phase 10)
- `.gsd/SPEC.md`
- `src/resplan_router/phase10_bim/bim_generator.py`
- `src/resplan_router/phase7_interactive/server.py`
- `src/resplan_router/phase7_interactive/incremental_router.py`
- `src/resplan_router/phase7_interactive/static/index.html`
- `src/resplan_router/phase7_interactive/static/app.js`
- `src/resplan_router/phase7_interactive/static/style.css`
- `tests/test_phase10_studio_bim.py`

## Tasks

<task type="auto">
  <name>Vendor 3D Assets & Server BIM Endpoints</name>
  <files>
    src/resplan_router/phase7_interactive/static/vendor/three.min.js
    src/resplan_router/phase7_interactive/static/vendor/OrbitControls.js
    src/resplan_router/phase7_interactive/incremental_router.py
    src/resplan_router/phase7_interactive/server.py
  </files>
  <action>
    1. Create directory `src/resplan_router/phase7_interactive/static/vendor`.
    2. Download/save self-contained vendor libraries `three.min.js` and `OrbitControls.js` into `static/vendor/` to guarantee offline/sandboxed operation.
    3. Update `src/resplan_router/phase7_interactive/incremental_router.py`:
       - Add method `get_bim_scene() -> BIM3DScene`:
         - Uses `BIM3DGenerator.generate_3d_scene(self.plan, self.partition, self.bundle)`.
       - Add method `export_obj() -> str`:
         - Uses `BIM3DGenerator.export_wavefront_obj()`.
    4. Update `src/resplan_router/phase7_interactive/server.py`:
       - Add REST endpoints:
         - `GET /api/bim/scene`: Returns `BIM3DScene` JSON for client-side Three.js rendering.
         - `GET /api/bim/export/obj`: Downloads formatted Wavefront `.obj` architectural model file.
       - Include `bim_scene` in full state updates when requested.

    AVOID: External network dependencies during testing; bundle vendor scripts locally.
    USE: In-memory scene generation with cached geometry.
  </action>
  <verify>
    python -c "from resplan_router.phase7_interactive.server import app; print('Server BIM endpoints verified')"
  </verify>
</task>

<task type="auto">
  <name>Synchronized 2D/3D BIM Split-Screen & Enhanced Reviewer HUD</name>
  <files>
    src/resplan_router/phase7_interactive/static/index.html
    src/resplan_router/phase7_interactive/static/style.css
    src/resplan_router/phase7_interactive/static/app.js
    tests/test_phase10_studio_bim.py
  </files>
  <action>
    1. Update `src/resplan_router/phase7_interactive/static/index.html`:
       - Add View Switcher segmented control in top toolbar: `[2D Floor Plan]`, `[Split 2D / 3D BIM]`, `[Full 3D BIM]`.
       - Add 3D container `#bimViewport` alongside `#studioCanvas`.
       - Add 3D Camera Preset toolbar: `Isometric BIM`, `Top Down`, `Perspective`, `Reset View`.
       - Add "Download 3D BIM (.OBJ)" button in actions footer.
       - Enhance Competition Metrics Scoreboard:
         - Display the full cost breakdown equation: $J = L + 5X + O + 0.5B$.
         - Individual metric pills for Cable Length ($L$), Crossings penalty ($5 \times X$), Overlap penalty ($1 \times O$), and Bends penalty ($0.5 \times B$).

    2. Update `src/resplan_router/phase7_interactive/static/style.css`:
       - Styles for split-screen layout:
         - `.viewport-container`: CSS flexbox dividing 2D and 3D canvases seamlessly.
         - `.view-mode-split`, `.view-mode-2d`, `.view-mode-3d`.
         - `#bimViewport`: 3D canvas container with dark architectural gradient background.
         - 3D overlay controls: floating pill buttons for camera viewpoints.
       - Styles for enhanced HUD scoreboard:
         - Detailed formula breakdown cards with distinct color accents for each penalty term.

    3. Update `src/resplan_router/phase7_interactive/static/app.js`:
       - Implement Three.js BIM Engine:
         - Initializes WebGLRenderer, Scene, PerspectiveCamera, OrbitControls.
         - Directional architectural sunlight, ambient light, floor grid helper.
         - Builds 3D meshes from `BIM3DScene` elements (slabs, walls, door lintels, windows, devices, cabinets, conduits).
         - Real-time 2D $\leftrightarrow$ 3D synchronization:
           - Dragging a node or hub in the 2D canvas smoothly translates its 3D mesh and updates the elevated 3D cable conduits in real-time!
           - Wire-cut simulation (Phase 9) displays the physical severed conduit gap in 3D with animated 3D pulse streams!
           - Structural tweaks (Phase 8) immediately punch out new 3D doorway portals and extrude the modified walls!
       - Button handler for "Download 3D BIM (.OBJ)" downloading `resplan_model.obj`.

    4. Implement `tests/test_phase10_studio_bim.py`:
       - **Test 1 (`test_bim_scene_rest_api`):**
         - Calls `GET /api/bim/scene`.
         - Asserts 200 OK, `len(scene["elements"]) > 0`, and valid `BIM3DScene` payload.
       - **Test 2 (`test_bim_obj_export_rest_api`):**
         - Calls `GET /api/bim/export/obj`.
         - Asserts 200 OK, `text/plain` or `application/octet-stream`, contains valid Wavefront OBJ syntax.
       - **Test 3 (`test_vendor_assets_served`):**
         - Verifies `GET /vendor/three.min.js` and `GET /vendor/OrbitControls.js` return 200 OK.
       - **Test 4 (`test_studio_static_contains_split_screen_elements`):**
         - Verifies `index.html` contains `#bimViewport`, view mode switcher, and 3D camera controls.
  </action>
  <verify>
    pytest tests/test_phase10_studio_bim.py tests/test_phase10_bim.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase10_bim.py tests/test_phase10_studio_bim.py -v`.
- Re-run full test suite `pytest tests/ -v` to ensure zero regressions across all 10 phases.

## Success Criteria
- Synchronized 2D/3D BIM split-screen operates smoothly at 60 FPS.
- Interactive Three.js scene accurately renders extruded architectural floor plan and elevated cable conduits.
- Live HUD displays complete metric breakdown equation ($H, L, 5X, O, 0.5B, J, \Delta A_{\text{gap}}$).
- One-click Wavefront OBJ architectural download verified.
- 100% test pass rate across entire repository.
