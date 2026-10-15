# Phase 1 Plan 2: Canonical Fixture, ResPlan Parser, Scale Normalization & Deterministic Polylabel Node Placement

<objective>
Construct the synthetic 6-room benchmark fixture matching Slide 5/8 (118 m²), explicitly labelling any unverified layout assumptions as provisional. Implement the geometry parser with strict schema validation and deterministic repair logging, metric scale normalizer, and robust polylabel device node placement engine with post-snap safety verification.
</objective>

<context>
@.gsd/SPEC.md
@prd_v1_control_panel_cable_routing_foundation.md
@src/resplan_router/contracts.py
@src/resplan_router/config.py
</context>

<tasks>
<task id="1">
<title>Synthesize provisional 6-room benchmark fixture and document assumptions</title>
<description>
Create `tests/fixtures/schematic_6room.json` representing Slide 5/8 ($118\text{ m}^2$ total eligible area, 6 eligible rooms: Living $40\text{ m}^2$, Bedroom 1 $16\text{ m}^2$, Bedroom 2 $16\text{ m}^2$, Bedroom 3 $15\text{ m}^2$, Kitchen $16\text{ m}^2$, Storage $15\text{ m}^2$, plus 1 excluded Bathroom, outer walls, interior doors, and front entrance door).
Explicitly mark this fixture with `"is_provisional": true` and record all geometric assumptions (room coordinates, wall thicknesses, door threshold placements) in an accompanying `tests/fixtures/README.md` and DECISIONS.md.
Implement `src/resplan_router/phase1_ingestion/parser.py` supporting documented ResPlan schema formats, rejecting unsupported/ambiguous structures with clear error messages, decomposing MultiPolygons deterministically, and logging all geometry repairs (`make_valid`, sliver removal).
</description>
<verification>
.venv/bin/python -c "from resplan_router.phase1_ingestion.parser import parse_resplan_json; data = parse_resplan_json('tests/fixtures/schematic_6room.json'); assert len(data.rooms) == 7; print('Parser verified, found 7 rooms')"
</verification>
</task>

<task id="2">
<title>Implement scale normalization and safe polylabel device placement</title>
<description>
Create `src/resplan_router/phase1_ingestion/scale_normalizer.py`:
- Compute area calibration factor $s = \sqrt{A_{\text{metadata}} / A_{\text{raw}}}$ if coordinate units are in pixels/unscaled CAD units.
Create `src/resplan_router/phase1_ingestion/node_placer.py`:
- Filter rooms by category (`living`, `bedroom`, `kitchen`, `storage` receive exactly 1 device; excluded rooms receive 0).
- Erode room polygon by safe wall clearance $d_{\text{clear}} = 0.25\text{ m}$.
- If centroid is safely inside $P_i^{\text{safe}}$, place candidate at centroid; otherwise use `shapely.ops.polylabel(P_i_safe, tolerance=0.01)` to ensure interior placement even in non-convex or L-shaped rooms.
- Grid snapping safety: Snap candidate coordinates to 0.05 m grid. Immediately verify post-snap containment: $P_i.\text{contains}(N_i) == \text{True}$, distance to room boundary $\ge 0.20\text{ m}$, and zero intersection with obstacle mask.
- If post-snap point violates safety constraints, deterministically test surrounding 0.05 m grid offsets. If all fail, raise an explicit InfeasibleNodePlacementError. Never weaken safety constraints.
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
5. `test_post_snap_safety_verification`: Verify snap-safety check rejects invalid points and raises error or finds valid neighbor.
Also verify full serialization/deserialization roundtrip of `FloorPlanModel`.
</description>
<verification>
.venv/bin/pytest tests/test_phase1_ingestion.py -v
</verification>
</task>
</tasks>

<success_criteria>
- `schematic_6room.json` provisional fixture generated with exact 118 m² eligible area and documented assumptions.
- `parser.py`, `scale_normalizer.py`, and `node_placer.py` implemented with strict post-snap validation.
- `pytest tests/test_phase1_ingestion.py` passes 100% of tests.
</success_criteria>
