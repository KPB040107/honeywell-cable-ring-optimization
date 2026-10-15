# Plan 3.2 Summary: Direction-State A*/Dijkstra Router & Pairwise Cost Tensor

## 1. Overview
Implemented the obstacle-aware direction-augmented state-space micro-routing engine (`astar_rectilinear.py`), the directional pairwise cost tensor and routing cache (`cost_matrix.py`), and the official Phase 3 verification suite (`test_phase3_micro_routing.py`).

The router strictly adheres to the official Slide 7 bend accounting rules by evaluating direction changes $(d_{\text{in}}, d_{\text{out}})$:
- $90^\circ$ turns: $B = 1$ ($+0.5$ cost penalty)
- $180^\circ$ reversals: $B = 1$ ($+0.5$ cost penalty)
- Straight passes: $B = 0$ ($+0.0$ cost penalty)
- Collinear segment compression removes intermediate vertices, preserving only genuine turns and reversals.

## 2. Tasks Completed

| Task | Name | Files Touched | Commit |
|------|------|---------------|--------|
| Task 1 | Direction-State Dijkstra / A* Router | `src/resplan_router/phase3_micro_routing/astar_rectilinear.py`, `rectilinear_grid.py`, `__init__.py` | `bd17258` |
| Task 2 | Pairwise Directional Cost Tensor & Cache | `src/resplan_router/phase3_micro_routing/cost_matrix.py`, `__init__.py` | `687985e` |
| Task 3 | Slide 7 Bends & Phase 3 Verification Suite | `src/resplan_router/phase3_micro_routing/astar_rectilinear.py`, `tests/test_phase3_micro_routing.py` | `4e9909e` |

## 3. Verification & Empirical Proof

All 34 tests across Phase 1, Phase 2, and Phase 3 pass cleanly:

```
============================= test session starts ==============================
rootdir: /Users/jahnaviakveti/Downloads/ps4
configfile: pyproject.toml
testpaths: tests
collected 34 items

tests/test_phase1_ingestion.py .....                                     [ 14%]
tests/test_phase1_scale.py ....                                          [ 26%]
tests/test_phase2_portals.py ........                                    [ 50%]
tests/test_phase2_topology.py ......                                     [ 67%]
tests/test_phase3_grid.py ......                                         [ 85%]
tests/test_phase3_micro_routing.py .....                                 [100%]

============================== 34 passed in 2.19s ==============================
```

### Verification Gate Breakdown (v2.md Section 5.3.3)
1. **Test 3.1 (`test_strict_horizontal_vertical_runs`)**: PASSED. Verified 100% of polyline segments have $|\Delta x| < 10^{-6}$ or $|\Delta y| < 10^{-6}$ (zero diagonal movements).
2. **Test 3.2 (`test_free_space_rule5_compliance`)**: PASSED. All routes lie strictly inside `inner_geom`, have zero intersection with obstacles, maintain $\ge 0.001\text{ m}$ obstacle clearance, and cross rooms strictly via portal openings.
3. **Test 3.3 (`test_slide7_bend_and_reversal_counting`)**: PASSED. Verified exact numerical values:
   - Straight pass: $B = 0, \Delta J = 0.0$
   - $90^\circ$ turn: $B = 1, \Delta J = 0.5$
   - $180^\circ$ reversal: $B = 1, \Delta J = 0.5$
4. **Test 3.4 (`test_slide5_schematic_routing_reachability`)**: PASSED. Precomputation completes in $< 400\text{ ms}$ (346.7 ms for full node tensor), and all intra-zone pairs are mutually reachable with exact $J = L + 0.5 B$. Inter-zone wall separation is strictly preserved.
5. **Test 3.5 (`test_phase1_phase2_phase3_regression`)**: PASSED. Scale units ($10.0\text{ units/m}$), portal connectivity, leaf room isolation, and multi-phase invariants verified together.

## 4. Key Architectural Decisions & Optimizations
- **$O(1)$ Hash Table Precomputation**: Optimized `SingleSourceResult` using coordinate-tuple hashing `(round(x, 4), round(y, 4))` mapped to direction dictionaries, reducing Dijkstra execution time to $\approx 3.3\text{ ms}$ per SSSP tree and keeping tensor build well within the 400 ms budget.
- **Multi-Zone Awareness**: Acknowledged Slide 5's physical structure (two independent functional zones separated by a solid wall) where routing between disconnected units correctly yields `None`, preventing invalid wall penetrations.
