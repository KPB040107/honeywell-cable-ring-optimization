---
phase: 10
plan: 1
wave: 1
gap_closure: false
---

# Plan 10.1: 3D BIM Geometry Generator, OBJ Exporter & BIM Data Contracts

## Objective
Implement the 3D Building Information Modeling (BIM) geometry generator (`bim_generator.py`) and data contracts (`contracts.py`). The generator extrudes 2D metric floor plans into full 3D architectural models—including floor slabs with pastel zone materials, 2.70 m vertical walls with doorway portal openings, glass windows, 3D sensor devices and hub cabinets, and elevated orthogonal ceiling conduit runs—with support for standard Wavefront `.obj` architectural export.

## Context
- `v2.md` (Section 7, Phase 10)
- `.gsd/SPEC.md`
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase1_ingestion/parser.py`
- `src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py`
- `src/resplan_router/phase6_output/checker.py`

## Tasks

<task type="auto">
  <name>BIM Data Contracts & 3D Extrusion Engine</name>
  <files>
    src/resplan_router/contracts.py
    src/resplan_router/phase10_bim/__init__.py
    src/resplan_router/phase10_bim/bim_generator.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` with Phase 10 contracts:
       - `BIMElementType(str, Enum)`: `SLAB = "slab"`, `WALL = "wall"`, `WINDOW = "window"`, `DOOR_LINTEL = "door_lintel"`, `NODE = "node"`, `HUB = "hub"`, `CONDUIT = "conduit"`.
       - `BIMElement3D`:
         - `element_id: str`.
         - `element_type: BIMElementType`.
         - `room_id: str | None` (default None).
         - `hub_id: str | None` (default None).
         - `vertices_3d: list[list[float]]` (list of [x, y, z] points in metres).
         - `faces: list[list[int]]` (0-indexed vertex indices).
         - `color_hex: str`.
         - `opacity: float` (default 1.0).
         - `metadata: dict` (e.g. area_m2, height_m, label).
       - `BIM3DScene`:
         - `plan_id: str`.
         - `units: str = "m"`.
         - `ceiling_height_m: float = 2.70`.
         - `conduit_elevation_m: float = 2.55`.
         - `elements: list[BIMElement3D]`.
         - `metrics_hud: dict`.

    2. Create `src/resplan_router/phase10_bim/__init__.py`.

    3. Implement `src/resplan_router/phase10_bim/bim_generator.py`:
       - Class `BIM3DGenerator`:
         - Method `generate_3d_scene(plan: MetricFloorPlan, partition: HubZonePartition | None = None, bundle: SynthesizedRingsBundle | None = None, ceiling_height_m: float = 2.70, conduit_elevation_m: float = 2.55) -> BIM3DScene`:
           - **Room Floor Slabs:** Triangulates each room polygon (using Shapely Delaunay/ear-clipping) at $z = 0.0\text{ m}$. Colors slabs with pastel zone fills (`#DCE8F8`, `#FDE5D2`) matching assigned hubs.
           - **Architectural Walls:** Extrudes walls vertically from $z = 0.0$ to $z = \text{ceiling\_height\_m}$ ($2.70\text{ m}$) with dark charcoal material (`#2B2B2B`).
           - **Doorway Portals:** For interior doors, creates door lintels/headers from $z = 2.10\text{ m}$ to $z = 2.70\text{ m}$ leaving an open $2.10\text{ m}$ high doorway threshold so 3D cables pass naturally through the opening.
           - **Windows:** Extrudes semi-translucent glass panels (`#A0C4DF`, opacity 0.5) from sill height $0.90\text{ m}$ to $2.10\text{ m}$.
           - **Sensor Devices (Nodes):** Renders ceiling-mounted sensor pods at $(x, y, \text{conduit\_elevation\_m})$ with green/white indicator spheres.
           - **Hub Equipment Cabinets:** Renders wall-mounted rectangular cabinets at $(x, y, 1.20\text{ m})$ with status indicators.
           - **3D Cable Conduits:**
             - Extrudes orthogonal cable polyline runs at ceiling height $z = \text{conduit\_elevation\_m}$ ($2.55\text{ m}$).
             - Adds vertical drop segments: from ceiling conduit down to hub cabinet ($2.55\text{ m} \to 1.50\text{ m}$) and to sensor nodes.
             - Colors conduits with high-contrast ring colors (`#00D2FF` for H1, `#FFA500` for H2).
         - Method `export_wavefront_obj(scene: BIM3DScene) -> str`:
           - Exports the entire 3D model into standard Wavefront OBJ format (`v x y z`, `f v1 v2 v3`, grouped by element type with material names), ready for import into Autodesk Revit, Blender, or AutoCAD.

    AVOID: Flat 2D projections; generate authentic 3D polyhedra with proper face normals and elevations.
    USE: Exact metric coordinate transformation with $z$-axis elevation.
  </action>
  <verify>
    python -c "from resplan_router.phase10_bim.bim_generator import BIM3DGenerator; print('BIM generator imported successfully')"
  </verify>
</task>

<task type="auto">
  <name>BIM Model Validation & Wavefront OBJ Export Tests</name>
  <files>
    tests/test_phase10_bim.py
  </files>
  <action>
    Implement `tests/test_phase10_bim.py`:
    1. **Test 1 (`test_slide5_3d_bim_scene_generation`):**
       - Generates 3D scene from Slide 5 schematic.
       - Asserts `len(scene.elements) > 0`.
       - Asserts element types include `SLAB`, `WALL`, `WINDOW`, `DOOR_LINTEL`, `NODE`, `HUB`, `CONDUIT`.
       - Verifies slab $z = 0.0$, wall height $z = 2.70$, conduit elevation $z = 2.55$.
    2. **Test 2 (`test_door_portal_clearance_in_3d`):**
       - Asserts doorway lintels are positioned strictly above $z = 2.10\text{ m}$, leaving walk-through clearance.
    3. **Test 3 (`test_3d_cable_conduit_vertical_drops`):**
       - Verifies that cable conduit elements contain both horizontal runs at $z = 2.55\text{ m}$ and vertical drops down to device nodes and hub cabinets.
    4. **Test 4 (`test_wavefront_obj_export_validity`):**
       - Exports Slide 5 scene to Wavefront OBJ string.
       - Verifies valid OBJ syntax: contains lines starting with `v `, `vn `, `f `, `g `, and `usemtl `.
       - Asserts vertex count $> 100$ and face count $> 50$.
    5. **Test 5 (`test_pydantic_contract_roundtrip`):**
       - Serializes `BIM3DScene` to JSON and validates roundtrip deserialization via `BIM3DScene.model_validate()`.
  </action>
  <verify>
    pytest tests/test_phase10_bim.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase10_bim.py -v`.
- Re-run full test suite `pytest tests/ -v`.

## Success Criteria
- 3D BIM scene generated accurately with realistic architectural slabs, walls, lintels, windows, and elevated cable conduits.
- Wavefront `.obj` architectural export format verified with valid syntax.
- All unit tests pass with 100% success rate.
