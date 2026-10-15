# Plan 2.1 Summary: Door & Open-Passage Portal Extraction & Contracts

## Objective
Defined the Phase 2 data contracts (`PortalType`, `DoorOrPassagePortal`, `PortalTopologyGraph`) and implemented the geometric portal extraction engine (`portal_extractor.py`) that identifies interior doors and open passages ($\ge 0.50\text{ m}$ width) between adjacent rooms, strictly rejecting solid walls, windows, and the building entrance (`front_door`), and synthesizing zero-overlap parallel doorway lanes ($\ge 20\text{ mm}$ lateral separation) and orthogonal room step points.

## Changes Made
1. **Pydantic Data Contracts (`src/resplan_router/contracts.py`)**:
   - Added `PortalType(str, Enum)`: `DOOR` and `OPEN_PASSAGE`.
   - Added `DoorOrPassagePortal`: Immutable contract containing portal ID, connected room IDs, midpoint, WKT LineString threshold segment, doorway width, parallel lane waypoints (`lane_a_point`, `lane_b_point`), orthogonal step points (`step_room1`, `step_room2`), and lateral offset $\delta \ge 0.02\text{ m}$. Validated non-empty LineString geometry and minimum lateral offset.
   - Added `PortalTopologyGraph`: Immutable dual graph contract capturing room IDs, portals, adjacency dictionary, fully connected component flag, and single-door leaf rooms.

2. **Geometric Portal Extraction Engine (`src/resplan_router/phase2_topology/portal_extractor.py`)**:
   - Implemented `extract_door_portals`:
     - Filters out exterior doors within $0.05\text{ m}$ of `plan.inner` boundary or intersecting `raw_entrance`.
     - Automatically normalizes drawing units to metres if unscaled geometries are supplied.
     - Buffers doors by $0.05\text{ m}$ and queries `STRtree` over `plan.rooms` to identify intersecting room pairs.
     - Computes threshold LineString as shared boundary intersected with door polygon and clipped to free space $\mathcal{F}$.
     - Synthesizes parallel lane waypoints $p_A, p_B$ offset by $\delta = \min(0.10, w / 4) \ge 0.02\text{ m}$ along doorway tangent.
     - Computes orthogonal step waypoints inset $0.15\text{ m}$ into each room.
   - Implemented `extract_open_passages`:
     - Checks shared room boundaries clipped to $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$.
     - Subtracts existing door portal thresholds.
     - Registers `OPEN_PASSAGE` portals for remaining contiguous segments with width $w \ge 0.50\text{ m}$.
   - Implemented `extract_all_portals`:
     - Combines door and open passage portals with deterministic ordering and sequential canonical IDs (`P01`, `P02`, ...).

3. **Portal Extraction Unit Tests (`tests/test_phase2_portals.py`)**:
   - 8 unit tests covering:
     - Synthetic two-room door extraction with parallel lane separation.
     - Rejection of doors intersecting entrance (`front_door`) or exterior perimeter.
     - Rejection of solid continuous walls (0 portals).
     - Rejection of window openings in shared walls (0 portals).
     - Detection of open passages $\ge 0.50\text{ m}$.
     - Rejection of narrow openings $< 0.50\text{ m}$.
     - Complete Slide 5 6-room schematic portal extraction (6 doors, degree-1 leaf rooms).
     - Pydantic validation enforcement on threshold WKT, positive width, and $\ge 0.02\text{ m}$ lateral offset.

## Files Touched
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase2_topology/__init__.py`
- `src/resplan_router/phase2_topology/portal_extractor.py`
- `tests/test_phase2_portals.py`

## Verification Evidence
- `python -c "from resplan_router.contracts import DoorOrPassagePortal, PortalType; print('Contracts syntax valid')"` -> `Contracts syntax valid`
- `python -c "from resplan_router.phase2_topology.portal_extractor import extract_all_portals; print('portal_extractor imported successfully')"` -> `portal_extractor imported successfully`
- `pytest tests/test_phase2_portals.py -v` -> 8 passed in 0.09s
- `pytest` -> 17 passed in 0.10s (full test suite passing 100%)

## Commits
- `c4d8e4f` — `feat(phase-2): Phase 2 Pydantic Data Contracts`
- `481375b` — `feat(phase-2): Door & Open-Passage Portal Extractor`
- `2e62a25` — `feat(phase-2): Portal Extraction Unit Tests`

## Deviations
None.

## Next Steps
Proceed to Plan 2.2 (`2-2-PLAN.md`):
- Implement `macro_graph.py` (NetworkX dual graph, single connected component verification, degree-1 leaf room identification).
- Implement `test_phase2_topology.py` for full Phase 2 verification gate.
