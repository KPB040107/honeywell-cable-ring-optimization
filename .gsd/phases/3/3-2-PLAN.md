---
phase: 3
plan: 2
wave: 2
gap_closure: false
depends_on:
  - 3-1-PLAN.md
---

# Plan 3.2: Direction-State A*/Dijkstra Router & Pairwise Cost Tensor

## Objective
Implement the direction-augmented state-space $(v, d_{\text{in}})$ pathfinding engine (`astar_rectilinear.py`) that strictly adheres to Slide 7 rules by pricing $90^\circ$ turns at $+0.5$, $180^\circ$ reversals at $+0.5$, and straight passes at $0.0$, compute and cache all-pairs directional cost tensors (`cost_matrix.py`), and build the comprehensive Phase 3 verification suite.

## Context
- `v2.md` (Sections 2.1, 2.2, 2.3, 5.3)
- `.gsd/SPEC.md`
- `.gsd/phases/3/RESEARCH.md`
- `.gsd/phases/3/3-1-PLAN.md`
- `tests/fixtures/slide5_schematic.json`

## Tasks

<task type="auto">
  <name>Direction-State Dijkstra / A* Router</name>
  <files>
    src/resplan_router/phase3_micro_routing/astar_rectilinear.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase3_micro_routing/astar_rectilinear.py`:
       - Class `DirectionStateRouter`:
         - State representation: `(vertex_idx, direction)` where direction is `Optional[CompassDirection]`.
         - Priority queue: `(cost_J, current_length, current_bends, state, path)`.
         - Transition logic from `(u, d_in)` to neighbor `v` along edge `(u, v)`:
           - Edge direction: `d_out = CompassDirection.from_delta(v.x - u.x, v.y - u.y)`.
           - Edge length: `dist = abs(v.x - u.x) + abs(v.y - u.y)`.
           - Turn count: `is_turn = (d_in is not None and d_out != d_in)`.
           - Bend penalty: `+0.5` if `is_turn` else `0.0` (prices both $90^\circ$ turns and $180^\circ$ reversals identically!).
           - Dynamic edge penalties: add overlap cost (`+1.0/m`) and crossing cost (`+5.0`) if edge/vertex is flagged as occupied.
         - Polyline compression: merges consecutive collinear segments so output vertices represent true $90^\circ$ turns.
       - Method `find_shortest_path(grid: HananRoutingGrid, start: Point2D, end: Point2D, start_dir: Optional[CompassDirection] = None, end_dir: Optional[CompassDirection] = None, allowed_rooms: Optional[set[str]] = None) -> PairRouteMetric`.

    AVOID: Ignoring arrival/departure directions during node transitions.
    USE: Directional state-space search with exact Slide 7 bend penalty accounting.
  </action>
  <verify>
    python -c "from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter; print('astar_rectilinear imported successfully')"
  </verify>
  <done>
    `DirectionStateRouter` calculates optimal rectilinear paths with accurate bend and reversal pricing.
  </done>
</task>

<task type="auto">
  <name>Pairwise Directional Cost Tensor & Cache</name>
  <files>
    src/resplan_router/phase3_micro_routing/cost_matrix.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase3_micro_routing/cost_matrix.py`:
       - Class `PairwiseCostTensor`:
         - Precomputes all-pairs shortest paths between:
           - All fixed nodes $N_1, \dots, N_n$.
           - All portal waypoints ($p_0, p_A, p_B$).
           - Candidate hub sites.
         - Evaluates all combinations of start and end directions $(d_{\text{start}}, d_{\text{end}}) \in \{\text{N}, \text{S}, \text{E}, \text{W}\}^2$.
         - Caches results in `RectilinearRoutingIndex` model.
         - Method `get_metric(u_id: str, v_id: str, d_start: Optional[CompassDirection] = None, d_end: Optional[CompassDirection] = None) -> PairRouteMetric`.
         - Execution runtime constraint: precomputation completes in $< 400\text{ ms}$ for typical plans.

    AVOID: Re-running Dijkstra repeatedly during zone partitioning and TSP solving.
    USE: Precomputed directional cost tensor indexed for $O(1)$ lookup.
  </action>
  <verify>
    python -c "from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor; print('cost_matrix imported successfully')"
  </verify>
  <done>
    `PairwiseCostTensor` computes and indexes directional pairwise costs efficiently.
  </done>
</task>

<task type="auto">
  <name>Slide 7 Bends & Phase 3 Verification Suite</name>
  <files>
    tests/test_phase3_micro_routing.py
  </files>
  <action>
    1. Build `tests/test_phase3_micro_routing.py` implementing the official verification gates from `v2.md` Section 5.3.3:
       - **Test 3.1 (`test_strict_horizontal_vertical_runs`)**: Assert that every polyline segment has $|\Delta x| < 10^{-6}$ or $|\Delta y| < 10^{-6}$. Zero diagonal movements.
       - **Test 3.2 (`test_free_space_rule5_compliance`)**: Assert that every polyline lies inside `inner`, outside `wall` and `window`, and passes between rooms exclusively through door/open-passage gaps.
       - **Test 3.3 (`test_slide7_bend_and_reversal_counting`)**:
         - Straight pass through a node: asserts $B = 0$ ($+0.0$ cost).
         - $90^\circ$ turn at a vertex or node: asserts $B = 1$ ($+0.5$ cost).
         - $180^\circ$ reversal at a node: asserts $B = 1$ ($+0.5$ cost).
       - **Test 3.4 (`test_slide5_schematic_routing_reachability`)**: Verify pairwise rectilinear paths between all connected rooms on Slide 5 schematic.
       - **Test 3.5 (`test_phase1_phase2_phase3_regression`)**: Re-verify scale, topology, and routing invariants together.

    AVOID: Skipping edge cases like $180^\circ$ reversals.
    USE: Explicit test fixtures for $90^\circ$ turns, straight lines, and reversals matching Slide 7 drawings.
  </action>
  <verify>
    pytest tests/test_phase3_micro_routing.py -v
  </verify>
  <done>
    All Phase 3 routing tests pass with 100% compliance and $< 400\text{ ms}$ runtime.
  </done>
</task>

## Must-Haves
- [ ] Router state is $(v, d_{\text{in}})$, pricing $90^\circ$ turns and $180^\circ$ reversals at $+0.5$ and straight passes at $0.0$.
- [ ] 100% of route polylines are axis-aligned ($\Delta x = 0$ or $\Delta y = 0$) inside free space.
- [ ] Inter-room transit passes exclusively through door/passage portal waypoints.
- [ ] All-pairs cost tensor is cached and serializable into `RectilinearRoutingIndex`.

## Success Criteria
- [ ] `pytest tests/test_phase3_micro_routing.py` passes 100%.
- [ ] Slide 7 bend counting tests pass with exact numerical values ($B=0$ for straight, $B=1$ for turn, $B=1$ for reversal).
- [ ] Cross-phase regression with Phase 1 and Phase 2 passes cleanly.
