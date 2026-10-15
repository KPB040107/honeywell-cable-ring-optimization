# Phase 1 Plan 2: Canonical Fixture, ResPlan Parser, Scale Normalization & Deterministic Polylabel Node Placement

<objective>
Construct the authoritative synthetic 6-room benchmark fixture matching Slide 5/8 (118 m²), implement the geometry parser, metric scale normalizer, and deterministic polylabel device node placement engine, and verify Phase 1 gates via automated pytest suite.
</objective>

<context>
@.gsd/SPEC.md
@prd_v1_control_panel_cable_routing_foundation.md
@src/resplan_router/contracts.py
@src/resplan_router/config.py
</context>

<tasks>
<task id="1">
<title>Synthesize canonical 6-room benchmark fixture and ResPlan importer</title>
<description>
Create `tests/fixtures/schematic_6room.json` representing Slide 5/8 ($118\text{ m}^2$ total eligible area, 6 eligible rooms: Living $40\text{ m}^2$, Bedroom 1 $16\text{ m}^2$, Bedroom 2 $16\text{ m}^2$, Bedroom 3 $15\text{ m}^2$, Kitchen $16\text{ m}^2$, Storage $15\text{ m}^2$, plus 1 excluded Bathroom, outer walls, interior doors, and front entrance door).
Implement `src/resplan_router/phase1_ingestion/parser.py` supporting both standard ResPlan semantic keys and coordinate/WKT layouts, decomposing MultiPolygons into valid single Polygons, sorting them deterministically, healing self-intersections with `shapely.make_valid()`, and assembling the obstacle mask (`wall` $\cup$ `window` $\cup$ `front_door`).
</description>
<verification>
.venv/bin/python -c "from resplan_router.phase1_ingestion.parser import parse_resplan_json; data = parse_resplan_json('tests/fixtures/schematic_6room.json'); assert len(data.rooms) == 7; print('Parser verified, found 7 rooms')"
</verification>
</task>

<task id="2">
<title>Implement scale normalization and polylabel device placement</title>
<description>
Create `src/resplan_router/phase1_ingestion/scale_normalizer.py`:
- Compute area calibration factor $s = \sqrt{A_{\text{metadata}} / A_{\text{raw}}}$ if coordinate units are in pixels/unscaled CAD units.
Create `src/resplan_router/phase1_ingestion/node_placer.py`:
- Filter rooms by category (`living`, `bedroom`, `kitchen`, `storage` receive exactly 1 device; excluded rooms receive 0).
- Erode room polygon by safe wall clearance $d_{\text{clear}} = 0.25\text{ m}$.
- If centroid is safely inside $P_i^{\text{safe}}$, place node at centroid; otherwise use `shapely.ops.polylabel(P_i_safe, tolerance=0.01)` to ensure interior placement even in non-convex or L-shaped rooms.
- Snap coordinates to 0.05 m grid while preserving $P_i.\text{contains}(N_i) == \text{True}$.
- Exclude nodes from intersecting obstacle mask.
</description>
<verification>
.venv/bin/python -c "from resplan_router.phase1_ingestion.parser import parse_resplan_json; from resplan_router.phase1_ingestion.node_placer import place_nodes; plan = parse_resplan_json('tests/fixtures/schematic_6room.json'); nodes = place_nodes(plan.rooms, plan.obstacle_union); assert len(nodes) == 6; print('Node placement verified: exactly 6 nodes')"
</verification>
</task>

<task id="3">
<title>Create automated Phase 1 test suite</title>
<description>
Implement `tests/test_phase1_ingestion.py` verifying all 4 Phase 1 verification gates from PRD Section 5.1.3:
1. `test_multipolygon_decomposition`: Exactly 7 rooms on 6-room fixture (6 eligible, 1 excluded).
2. `test_eligible_vs_excluded_filtering`: Exactly 6 nodes placed, zero in bathroom or balcony.
3. `test_l_shaped_nonconvex_room_node_containment`: Synthetic non-convex room asserts node is strictly inside with wall clearance >= 0.20 m.
4. `test_obstacle_mask_integrity`: Zero device nodes intersect obstacles; all nodes strictly inside inner polygon.
Also verify full serialization/deserialization roundtrip of `FloorPlanModel`.
</description>
<verification>
.venv/bin/pytest tests/test_phase1_ingestion.py -v
</verification>
</task>
</tasks>

<success_criteria>
- `schematic_6room.json` fixture generated with exact 118 m² eligible area.
- `parser.py`, `scale_normalizer.py`, and `node_placer.py` implemented.
- `pytest tests/test_phase1_ingestion.py` passes 100% of tests.
</success_criteria>
