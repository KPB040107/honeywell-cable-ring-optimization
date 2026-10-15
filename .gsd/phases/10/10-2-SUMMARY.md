# Plan 10.2 Summary: Synchronized 2D/3D BIM Split-Screen, WebGL Viewer & Live HUD

## Objective
Integrate the 3D Building Information Modeling (BIM) viewer into the browser-based Co-Design Studio (`static/index.html`, `static/style.css`, `static/app.js`). Implement a synchronized 2D/3D split-screen layout with Three.js WebGL rendering, orbit controls with camera presets, live real-time bidirectional synchronization, an enhanced Reviewer HUD detailing all metric components ($H, L, 5X, O, 0.5B, J, \Delta A_{\text{gap}}$), and one-click Wavefront OBJ architectural download.

## Changes Completed

### Task 1: Vendor 3D Assets & Server BIM Endpoints
- **Vendored Offline Assets:**
  - Placed self-contained `three.min.js` (r128) and `OrbitControls.js` (r128) into `src/resplan_router/phase7_interactive/static/vendor/` to eliminate external runtime CDN dependencies.
- **Incremental Router Integration (`incremental_router.py`):**
  - Added `get_bim_scene() -> BIM3DScene` with in-memory caching and invalidation across pointer drag, door slide, wall shift, wire-cut, and proposal mutations.
  - Added `export_obj() -> str` exporting complete Wavefront OBJ text.
  - Extended `get_state()` with `include_bim: bool = False` returning serialized `bim_scene` on demand.
- **FastAPI Server Endpoints (`server.py`):**
  - Added `GET /api/bim/scene` endpoint returning validated `BIM3DScene` JSON.
  - Added `GET /api/bim/export/obj` endpoint streaming downloadable `resplan_model.obj` attachment.
  - Added `action: "get_bim_scene"` handling to the `/ws/co-design` WebSocket loop.
- **Commit:** `9632710` (`feat(phase-10): vendor 3D assets and server BIM endpoints`)

### Task 2: Synchronized 2D/3D BIM Split-Screen & Enhanced Reviewer HUD
- **Studio Interface Enhancement (`index.html`):**
  - Added View Mode Switcher segmented control in top header: `[2D Floor Plan]`, `[Split 2D / 3D BIM]`, `[Full 3D BIM]`.
  - Added `.viewport-container` dividing 2D drafting canvas (`#studioCanvas`) and 3D WebGL viewport (`#bimViewport`).
  - Added 3D Camera Preset toolbar overlay: `[Isometric BIM]`, `[Top Down]`, `[Perspective]`, `[Reset View]`.
  - Added "Download 3D BIM (.OBJ)" button in actions footer.
  - Enhanced Competition Metrics Scoreboard with live equation breakdown pills for $J = L + 5X + O + 0.5B$ and individual penalty labels.
- **Modern Dark Architectural Styling (`style.css`):**
  - Implemented CSS flexbox layout for `.viewport-container`, `.canvas-pane`, and view mode transitions (`.view-mode-split`, `.view-mode-2d`, `.view-mode-3d`).
  - Added `#bimViewport` styling with dark architectural gradient, floating camera toolbar pills, and responsive resizing.
  - Styled live formula breakdown bar with distinct color accents for each penalty term ($L$ cyan, $5X$ purple, $1O$ coral, $0.5B$ amber, $J$ emerald).
- **Three.js BIM Engine (`app.js`):**
  - Implemented `initThreeEngine()` initializing WebGLRenderer, Scene, PerspectiveCamera, OrbitControls with damping, directional architectural sunlight with soft shadows, ambient lighting, and floor grid helper.
  - Implemented `rebuildBIMMeshes()` translating `BIM3DScene` elements into Three.js BufferGeometries (slabs, 2.70m walls, door lintels, semi-translucent windows, device pods, cabinets, elevated conduits).
  - Implemented real-time 2D $\leftrightarrow$ 3D synchronization: dragging nodes/hubs in 2D immediately translates their 3D meshes and refreshes elevated conduits.
  - Implemented 3D wire-cut simulation showing physical severed conduit gap with animated pulsating rings.
  - Implemented camera viewpoint presets and view mode switching.
  - Added one-click `.obj` file download trigger.
- **Integration Test Suite (`tests/test_phase10_studio_bim.py`):**
  - Verified REST APIs `GET /api/bim/scene` and `GET /api/bim/export/obj`.
  - Verified vendor offline asset serving (`/vendor/three.min.js`, `/vendor/OrbitControls.js`).
  - Verified split-screen and HUD DOM elements in `index.html`.
  - Verified cache invalidation and state integration in `IncrementalCoDesignEngine`.
- **Commit:** `f76fcb9` (`feat(phase-10): synchronized 2D/3D BIM split-screen and live HUD`)

## Verification Evidence
1. **Phase 10 Tests:**
   `pytest tests/test_phase10_studio_bim.py tests/test_phase10_bim.py -v`
   Result: 11 passed in 6.52s.
2. **Full Repository Regression Suite:**
   `pytest tests/ -v`
   Result: 107 passed across all 10 phases in 97.94s (0 failures, zero regressions).

## Deviations
- Used `BIMElementType.NODE` and `BIMElementType.HUB` conforming to canonical contract definitions in `contracts.py`.
- Formatted Wavefront OBJ group tokens with standard `g slab_`, `g wall_`, `g conduit_` prefix syntax.

## Next Steps
All Phase 10 execution tasks are complete. Proceed to phase verification with `gsd-verifier`.
