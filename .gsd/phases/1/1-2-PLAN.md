---
phase: 1
plan: 2
wave: 2
gap_closure: false
depends_on:
  - 1-1-PLAN.md
---

# Plan 1.2: Polygon Healing, Room/Obstacle Partitioning & Fixed Node Ingestion

## Objective
Implement geometry repair with Shapely `make_valid()`, room taxonomy classification (eligible vs. excluded), obstacle extraction ($\mathcal{O} = \text{wall} \cup \text{window} \cup \text{front\_door}$), routable free-space extraction ($\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$), fixed node coordinate ingestion with $\ge 0.5\text{ m}$ wall clearance verification, and deterministic `polylabel` fallback for unannotated plans, validated on the Slide 5 benchmark schematic.

## Context
- `v2.md` (Sections 2.1, 2.2, 5.1)
- `.gsd/SPEC.md`
- `.gsd/phases/1/RESEARCH.md`
- `.gsd/phases/1/1-1-PLAN.md`

## Tasks

<task type="auto">
  <name>Polygon Healing, Room Classification & Free-Space Extraction</name>
  <files>
    src/resplan_router/phase1_ingestion/parser.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase1_ingestion/parser.py`:
       - `parse_and_heal_polygon(raw_coords: list) -> Polygon | MultiPolygon`: converts vertex array to Shapely polygon and calls `shapely.make_valid()`. Strips slivers with area $< 2.0\text{ m}^2$ unless part of a valid wall/door feature.
       - `classify_room(category_str: str) -> tuple[bool, RoomCategory]`: checks against eligible types (`living`, `bedroom`, `kitchen`, `storage`) and excluded types (`bathroom`, `balcony`, `stair`, `garden`, `pool`, `parking`, `land`, `corridor`).
       - `build_obstacles_and_freespace(scaled_inner: Polygon, walls: list[Polygon], windows: list[Polygon], doors: list[Polygon]) -> tuple[Polygon, Polygon]`:
         - Obstacle union: $\mathcal{O} = \text{unary\_union}(\text{walls} \cup \text{windows} \cup \text{front\_door\_polys})$.
         - Free space: $\mathcal{F} = \text{scaled\_inner}.difference(\text{unary\_union}(\text{walls} \cup \text{windows}))$.
       - `parse_floor_plan(raw_json: dict) -> MetricFloorPlan`: orchestrates scale conversion, room extraction, obstacle generation, and node binding into the immutable Pydantic `MetricFloorPlan`.

    AVOID: Allowing self-intersecting polygons to enter downstream graph algorithms.
    USE: `shapely.make_valid()` and clean exterior/interior linear rings.
  </action>
  <verify>
    python -c "from resplan_router.phase1_ingestion.parser import classify_room; assert classify_room('living')[0] is True; assert classify_room('bathroom')[0] is False"
  </verify>
  <done>
    `parser.py` correctly heals raw polygons, filters slivers, and partitions eligible vs. excluded spaces.
  </done>
</task>

<task type="auto">
  <name>Fixed Node Loader & Deterministic Polylabel Fallback</name>
  <files>
    src/resplan_router/phase1_ingestion/node_loader.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase1_ingestion/node_loader.py`:
       - `load_fixed_nodes(raw_json: dict, scale: float, eligible_rooms: dict[str, Polygon], obstacles: Geometry) -> dict[str, FixedNode]`:
         - Inspects `raw_json` for existing node entries (`nodes` key or room-level point keys).
         - If present: transforms coordinates by `1.0 / scale`, asserts each node lies within its host room polygon, and verifies $\text{distance}(N_i, \text{obstacles}) \ge 0.50\text{ m} - 0.001\text{ m}$ ($1\text{ mm}$ tolerance).
         - If not present (e.g. raw ResPlan benchmark fixtures): computes safe room interior $P_i^{\text{safe}} = R_i.difference(\text{obstacles.buffer}(0.50\text{ m}))$ and computes deterministic pole of inaccessibility via `shapely.ops.polylabel(P_i_safe, tolerance=0.001)`.
       - Assigns canonical node IDs $N_1, N_2, \dots, N_n$ mapped 1:1 to eligible rooms.
       - Strictly assigns 0 nodes to excluded spaces (`bathroom`, `balcony`, `stair`, etc.).

    AVOID: Moving user/organizer supplied fixed node coordinates.
    USE: Verified immutability of input node coordinates when provided in the JSON.
  </action>
  <verify>
    python -c "from resplan_router.phase1_ingestion.node_loader import load_fixed_nodes; print('node_loader syntax valid')"
  </verify>
  <done>
    Node loader ingests pre-set nodes and falls back to deterministic $0.5\text{ m}$ safe polylabel.
  </done>
</task>

<task type="auto">
  <name>Slide 5 Benchmark Fixture & Comprehensive Phase 1 Test Suite</name>
  <files>
    tests/fixtures/slide5_schematic.json
    tests/test_phase1_ingestion.py
  </files>
  <action>
    1. Create `tests/fixtures/slide5_schematic.json` modeling the 6-room benchmark from Slide 5 ($118\text{ m}^2$, rooms: `Living`, `Bedroom 1`, `Bedroom 2`, `Bedroom 3`, `Kitchen`, `Storage`, plus excluded `Bathroom` and `Entrance`).
    2. Build `tests/test_phase1_ingestion.py` implementing the 4 official verification gates:
       - **Test 1.1 (`test_slide4_exact_scale_conversion`)**: scale factor $\approx 18.09068$, metric dimensions $11.055\text{ m} \times 9.950\text{ m}$.
       - **Test 1.2 (`test_net_area_ignored`)**: `net_area = 0` ignored, plan area used.
       - **Test 1.3 (`test_eligible_and_excluded_rooms`)**: 6 eligible rooms have exactly 1 node each, excluded `Bathroom` has 0 nodes.
       - **Test 1.4 (`test_fixed_node_immutability_and_clearance`)**: all nodes remain inside eligible rooms with clearance $\ge 0.50\text{ m} - 0.001\text{ m}$ from obstacles.
       - **Test 1.5 (`test_precision_and_contract_export`)**: assert `MetricFloorPlan` coordinates preserve $0.1\text{ mm}$ precision.

    AVOID: Writing tests that assert trivial truths.
    USE: Explicit numerical assertions against Slide 4 and Slide 5 specifications.
  </action>
  <verify>
    pytest tests/test_phase1_ingestion.py -v
  </verify>
  <done>
    All Phase 1 unit and integration tests pass cleanly with zero clearance violations.
  </done>
</task>

## Must-Haves
- [ ] Every eligible room has exactly 1 fixed node; excluded spaces have 0 nodes.
- [ ] Every fixed node satisfies $\ge 0.50\text{ m} - 0.001\text{ m}$ wall clearance.
- [ ] Free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$ is geometrically sound.
- [ ] Slide 5 fixture parses into `MetricFloorPlan` with 6 eligible rooms totaling $118\text{ m}^2$.

## Success Criteria
- [ ] `pytest tests/test_phase1_ingestion.py` executes and passes 100%.
- [ ] Invariant passes: 0 node mutations, 0 negative areas, 0 sliver polygons.
