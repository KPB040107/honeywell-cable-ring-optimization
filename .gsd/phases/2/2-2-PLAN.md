---
phase: 2
plan: 2
wave: 2
gap_closure: false
depends_on:
  - 2-1-PLAN.md
---

# Plan 2.2: Dual Graph Construction, Parallel Lanes & Topology Verification Suite

## Objective
Implement `macro_graph.py` to construct the room-portal dual graph $G_{\text{macro}}$ in NetworkX, verify single connected component topology among eligible rooms, detect degree-1 single-door leaf rooms, validate that parallel doorway lanes maintain lateral separation $\ge 20\text{ mm}$ ($\gg 1\text{ mm}$ checker tolerance) inside free space, and build the full Phase 2 verification suite.

## Context
- `v2.md` (Sections 2.1, 2.2, 5.2)
- `.gsd/SPEC.md`
- `.gsd/phases/2/RESEARCH.md`
- `.gsd/phases/2/2-1-PLAN.md`
- `tests/fixtures/slide5_schematic.json`

## Tasks

<task type="auto">
  <name>Macro Graph & Parallel Lane Generation</name>
  <files>
    src/resplan_router/phase2_topology/macro_graph.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase2_topology/macro_graph.py`:
       - Class `MacroTopologyGraph`:
         - Builds `networkx.Graph` where nodes are room IDs and edges are `DoorOrPassagePortal` objects.
         - `build_topology(plan: MetricFloorPlan, portals: list[DoorOrPassagePortal]) -> PortalTopologyGraph`:
           - Populates graph nodes and edges.
           - Checks that the subgraph induced by eligible rooms (`is_eligible == True`) forms a single connected component.
           - If not connected, raises `TopologyError` detailing disconnected components.
           - Identifies `leaf_room_ids`: eligible rooms with degree 1 in the eligible subgraph (e.g. `Bedroom 2` and `Storage` on Slide 5).
           - Returns validated `PortalTopologyGraph` contract.
       - Method `get_portal_lanes(portal: DoorOrPassagePortal) -> tuple[Point2D, Point2D, Point2D]`: returns `(center, lane_a, lane_b)`.
       - Method `validate_parallel_lane_clearance(portal: DoorOrPassagePortal, free_space: Geometry) -> bool`: asserts `lane_a` and `lane_b` are separated by $\ge 0.020\text{ m}$ and lie strictly inside `free_space`.

    AVOID: Treating windows or exterior walls as graph edges.
    USE: NetworkX dual graph with portal attribute payloads and strict contiguity verification.
  </action>
  <verify>
    python -c "from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph; print('MacroTopologyGraph imported')"
  </verify>
  <done>
    `macro_graph.py` builds dual graph, identifies leaf rooms, and verifies parallel lane separation.
  </done>
</task>

<task type="auto">
  <name>Slide 5 Benchmark Topology & Phase 2 Test Suite</name>
  <files>
    tests/test_phase2_topology.py
  </files>
  <action>
    1. Build `tests/test_phase2_topology.py` implementing the 3 official verification gates from `v2.md` Section 5.2.3:
       - **Test 2.1 (`test_slide5_door_connectivity`)**:
         - Parse `tests/fixtures/slide5_schematic.json` through Phase 1 parser.
         - Run `extract_all_portals` and `MacroTopologyGraph.build_topology`.
         - Assert $G_{\text{macro}}$ connects:
           - `Living`–`Bedroom 3`
           - `Bedroom 3`–`Bedroom 1`
           - `Living`–`Bedroom 1`
           - `Bedroom 2`–`Kitchen`
           - `Kitchen`–`Storage`
         - Assert `Bedroom 2` and `Storage` are identified as degree-1 single-door leaf rooms.
       - **Test 2.2 (`test_window_wall_entrance_rejection`)**:
         - Verify that rooms sharing only solid wall or window have zero direct edges in $G_{\text{macro}}$.
         - Verify that the `Entrance` (`front_door`) has zero direct edges connecting interior rooms.
       - **Test 2.3 (`test_parallel_door_lanes_exceed_1mm_tolerance`)**:
         - For every extracted portal, verify `lane_a` and `lane_b` lie strictly inside `plan.free_space_geom`.
         - Assert Euclidean distance between `lane_a` and `lane_b` is $\ge 0.020\text{ m}$ ($20\text{ mm} = 20\times$ the $1\text{ mm}$ overlap tolerance).
       - **Test 2.4 (`test_phase1_phase2_regression`)**:
         - Re-verify Phase 1 scale conversion and node clearance invariants on the topological output.

    AVOID: Asserting trivial connectivity.
    USE: Exact topology matching Slide 5 schematic layout.
  </action>
  <verify>
    pytest tests/test_phase2_topology.py -v
  </verify>
  <done>
    All Phase 2 unit and integration tests pass with 100% compliance.
  </done>
</task>

## Must-Haves
- [ ] $G_{\text{macro}}$ connects rooms exclusively across doors and open passages.
- [ ] Degree-1 single-door leaf rooms (`Bedroom 2`, `Storage`) correctly identified.
- [ ] Parallel doorway lanes have lateral separation $\ge 20\text{ mm}$ and lie inside free space.
- [ ] Windows, solid walls, and front doors rejected from graph connectivity.

## Success Criteria
- [ ] `pytest tests/test_phase2_topology.py` passes 100%.
- [ ] Zero graph edges across walls or windows.
- [ ] Phase 1 + Phase 2 regression tests pass cleanly.
