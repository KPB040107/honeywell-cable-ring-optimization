# Plan 2.2 Summary: Dual Graph Construction, Parallel Lanes & Topology Verification Suite

## Overview
Implemented `src/resplan_router/phase2_topology/macro_graph.py` and comprehensive test suite `tests/test_phase2_topology.py` fulfilling Phase 2 Wave 2 requirements from `v2.md` Section 5.2.

The dual graph $G_{\text{macro}}$ models rooms as nodes and physical portals (interior doors and open passages $\ge 0.50\text{ m}$) as edges using NetworkX. It identifies degree-1 leaf rooms requiring out-and-back parallel doorway runs, validates that parallel doorway lanes maintain lateral separation $\ge 20\text{ mm}$ ($\gg 1\text{ mm}$ checker tolerance) strictly within free space, strictly rejects walls, windows, and front doors, and supports strict connected-component verification via `TopologyError`.

## Accomplishments
1. **`MacroTopologyGraph` (`src/resplan_router/phase2_topology/macro_graph.py`)**:
   - NetworkX dual graph with dual classmethod/instance method interface via `_BuildTopologyDispatcher`.
   - Node population from `plan.rooms` with category, eligibility, area, and polygon attributes.
   - Portal edge integration linking room pairs with `DoorOrPassagePortal` payload.
   - Eligible subgraph analysis detecting single-door degree-1 leaf rooms (e.g. `Bedroom 2` and `Storage` on Slide 5).
   - `get_portal_lanes(portal)` returning `(center, lane_a, lane_b)`.
   - `validate_parallel_lane_clearance(portal, free_space)` verifying $\ge 20\text{ mm}$ lateral distance and free space containment.
   - `TopologyError` raised on disconnected topologies when strict single component connectivity is required (`require_connected=True`).
2. **Phase 2 Verification Suite (`tests/test_phase2_topology.py`)**:
   - `test_slide5_door_connectivity`: verified all 5 interior door connections on Slide 5 (`Living`–`Bedroom 3`, `Bedroom 3`–`Bedroom 1`, `Living`–`Bedroom 1`, `Bedroom 2`–`Kitchen`, `Kitchen`–`Storage`) and identified `Bedroom 2` and `Storage` as degree-1 leaf rooms.
   - `test_window_wall_entrance_rejection`: proved zero edges across solid walls, window gaps, or building entrance (`front_door`).
   - `test_parallel_door_lanes_exceed_1mm_tolerance`: confirmed all portal lanes maintain $\ge 20\text{ mm}$ separation (actual: $200\text{ mm}$ on Slide 5) and lie inside free space.
   - `test_phase1_phase2_regression`: confirmed Phase 1 scale factor ($10.0\text{ units/m}$), area ($126\text{ m}^2$), and node clearance invariants hold.
   - `test_topology_error_handling`: verified `TopologyError` on disconnected floor plans under `require_connected=True`.
   - `test_parallel_lane_clearance_validation_errors`: verified `ValueError` on insufficient lane separation or out-of-bounds coordinates.

## Verification Evidence
```bash
$ pytest tests/test_phase2_topology.py -v
============================= test session starts ==============================
collected 6 items

tests/test_phase2_topology.py::test_slide5_door_connectivity PASSED      [ 16%]
tests/test_phase2_topology.py::test_window_wall_entrance_rejection PASSED [ 33%]
tests/test_phase2_topology.py::test_parallel_door_lanes_exceed_1mm_tolerance PASSED [ 50%]
tests/test_phase2_topology.py::test_phase1_phase2_regression PASSED      [ 66%]
tests/test_phase2_topology.py::test_topology_error_handling PASSED       [ 83%]
tests/test_phase2_topology.py::test_parallel_lane_clearance_validation_errors PASSED [100%]

============================== 6 passed in 0.15s ===============================

$ pytest
============================== 23 passed in 0.18s ==============================
```

## Commits
- `ad95aca`: `feat(phase-2): macro graph and parallel lane generation`
- `ea6d1da`: `feat(phase-2): slide 5 benchmark topology and phase 2 test suite`
