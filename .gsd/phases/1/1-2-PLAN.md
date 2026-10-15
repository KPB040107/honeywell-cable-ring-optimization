# Phase 1 Plan 2: Provisional Benchmark Fixture, ResPlan Parser, Scale Normalization & Deterministic Polylabel Node Placement

<objective>
Construct the synthetic 6-room benchmark fixture based on PRD Section 5.4.3 area and topology requirements, explicitly declaring all unverified coordinates as provisional. Implement the ResPlan geometry parser with explicit unit handling and targeted repair logging, the metric scale normalizer, and the deterministic polylabel device node placement engine with strict 0.20 m post-snap clearance enforcement. Verify all Phase 1 gates via automated pytest suite.
</objective>

<context>
@.gsd/SPEC.md
@prd_v1_control_panel_cable_routing_foundation.md
@src/resplan_router/contracts.py
@src/resplan_router/config.py
</context>

<tasks>
<task id="1">
<title>Synthesize provisional 6-room benchmark fixture and implement ResPlan parser with targeted repair</title>
<description>
Create `tests/fixtures/schematic_6room.json` representing the 6 eligible rooms (118 m² total area: Living 40 m², Bed 1 16 m², Bed 2 16 m², Bed 3 15 m², Kitchen 16 m², Storage 15 m²) and 1 excluded Bathroom, with doors matching the PRD adjacency topology.
Mark the fixture explicitly with top-level metadata `is_provisional: true` and document all unverified coordinate, wall dimension, and door placement assumptions in `tests/fixtures/README.md`.
Implement `src/resplan_router/phase1_ingestion/parser.py`:
- Support documented ResPlan WKT and coordinate dictionary schemas.
- Explicit Unit Handling: Reject inputs with ambiguous or missing unit definitions with `AmbiguousCoordinateUnitError`. Only apply unit conversions when explicitly declared in metadata (`coordinate_unit: "pixel"` or `"meter"`) or when raw geometries match a documented dataset convention.
- Targeted Geometry Repair: Do NOT blindly chain `make_valid()` and `buffer(0)` on all geometries. Test `geom.is_valid` first. If invalid, apply minimal repair, log the operation (original issue, before/after area delta), and verify `repaired_geom.is_valid`. If the area change exceeds a 1% threshold or topology degrades, reject with `GeometryRepairError`.
- Deterministic Polygon Decomposition: Break MultiPolygons into valid single Polygons, filter out slivers (< 0.5 m²), and sort deterministically by (min_x, min_y).
</description>
<verification>
.venv/bin/python -c "from resplan_router.phase1_ingestion.parser import parse_resplan_json; data = parse_resplan_json('tests/fixtures/schematic_6room.json'); assert len(data.rooms) == 7; print('Parser verified: found 7 rooms in provisional fixture')"
</verification>
</task>

<task id="2">
<title>Implement scale normalizer and safe polylabel device placement</title>
<description>
Create `src/resplan_router/phase1_ingestion/scale_normalizer.py`:
- Calibrate geometry metrics only when explicit scale metadata is provided or when explicit unit conversion is justified. Reject ambiguous units with descriptive errors rather than silently guessing.
Create `src/resplan_router/phase1_ingestion/node_placer.py`:
- Filter rooms by category: exactly 1 device node for `living`, `bedroom`, `kitchen`, `storage`; 0 for excluded rooms.
- Clearance Semantics Rationale: Candidate generation uses an initial buffer erosion distance of 0.25 m to provide a 0.05 m buffer margin before grid snapping. If centroid lies inside eroded safe polygon, use centroid; otherwise compute `polylabel(P_safe, tolerance=0.01)`.
- Grid Snapping & Post-Snap Invariant Enforcement: Snap coordinates to 0.05 m grid. The post-snap validator must independently enforce:
  1. Strict interior containment: `room_polygon.contains(Point(snapped)) == True`.
  2. Strict wall clearance: `snapped.distance(room_polygon.boundary) >= 0.20 m`.
  3. Obstacle clearance: `snapped.distance(obstacle_union) > 0`.
- Neighborhood Search & Infeasibility: If post-snap point violates clearance (e.g. falls < 0.20 m from boundary), evaluate 8 immediate grid neighbors (step 0.05 m). If none satisfy >= 0.20 m clearance, raise explicit `InfeasibleNodePlacementError`. Constraints are never weakened.
</description>
<verification>
.venv/bin/python -c "from resplan_router.phase1_ingestion.parser import parse_resplan_json; from resplan_router.phase1_ingestion.node_placer import place_nodes; plan = parse_resplan_json('tests/fixtures/schematic_6room.json'); nodes = place_nodes(plan.rooms, plan.obstacle_union); assert len(nodes) == 6; print('Node placement verified: exactly 6 nodes placed safely')"
</verification>
</task>

<task id="3">
<title>Create automated Phase 1 test suite</title>
<description>
Implement `tests/test_phase1_ingestion.py` covering:
1. `test_schema_validation_and_unit_rejection`: Validates valid JSON, asserts rejection of ambiguous unit metadata with `AmbiguousCoordinateUnitError`.
2. `test_provisional_fixture_ingestion`: Ingests `schematic_6room.json`, asserts exactly 7 rooms (6 eligible, 1 excluded), and verifies `is_provisional` metadata flag.
3. `test_targeted_repair_logging`: Asserts valid geometries are untouched and invalid geometries trigger logged repair with area drift checks.
4. `test_scale_normalization_explicit`: Confirms metric scaling when explicit metadata is provided and rejection when ambiguous.
5. `test_l_shaped_nonconvex_room_node_containment`: Asserts polylabel places node strictly inside non-convex room with boundary clearance >= 0.20 m.
6. `test_post_snap_clearance_and_recovery`: Verifies post-snap validator strictly enforces >= 0.20 m boundary clearance and tests neighborhood recovery.
7. `test_infeasible_narrow_room_rejection`: Asserts explicit `InfeasibleNodePlacementError` on a 0.30 m-wide corridor where 0.20 m clearance on both sides is mathematically impossible.
</description>
<verification>
.venv/bin/pytest tests/test_phase1_ingestion.py -v
</verification>
</task>
</tasks>

<success_criteria>
- `schematic_6room.json` created with `is_provisional: true` and documented in `tests/fixtures/README.md`.
- `parser.py`, `scale_normalizer.py`, and `node_placer.py` implemented with explicit unit handling and strict post-snap clearance validation.
- `tests/test_phase1_ingestion.py` passes 100% with pytest.
</success_criteria>
