# Plan 8.1 Summary: Architectural Co-Pilot Perturbation Engine & Ranking Core

## Objective
Implement the "Architectural Co-Pilot" perturbation search engine (`wall_optimizer.py`), candidate perturbation generator (`candidate_generator.py`), and data contracts (`contracts.py`). The engine identifies candidate structural modifications—adding ghost open passages on unlinked shared interior walls and sliding existing doors along walls—evaluates the resulting topology with warm-started routing/partitioning, and ranks proposals lexicographically by hub reduction $\Delta H$ and composite cost reduction $\Delta J$.

## Changes Made

### 1. Data Contracts (`src/resplan_router/contracts.py`)
- Defined `ProposalType(str, Enum)` with values `ADD_GHOST_DOOR` and `SLIDE_DOOR`.
- Defined `StructuralProposal(BaseModel)` with geometry endpoints, door center, width, metric deltas ($\Delta H, \Delta J, \Delta L, \Delta B$), baseline and new composite costs, and human-readable architectural rationale.
- Defined `CoPilotReport(BaseModel)` summarizing baseline metrics, proposals evaluated, and ranked top recommendations.

### 2. Candidate Perturbation Generator (`src/resplan_router/phase8_copilot/candidate_generator.py`)
- Implemented `PerturbationCandidateGenerator`:
  - `find_shared_interior_walls`: computes 1D shared boundaries between adjacent eligible rooms via Shapely geometry intersections. Decomposes boundaries into straight rectilinear segments ($\ge 1.0\text{ m}$). Enforces $\ge 0.50\text{ m}$ corner clearance and strictly rejects exterior boundaries and window overlaps. Proposes ghost door centers at midpoint, 25%, and 75% intervals.
  - `find_sliding_door_candidates`: locates host walls for existing interior doors and explores lateral slides ($\pm 0.5\text{ m}, \pm 1.0\text{ m}$) preserving $\ge 0.50\text{ m}$ wall clearance.
  - `generate_all_candidates`: aggregates ghost doorway and sliding door candidate modifications.

### 3. Co-Pilot Optimization Engine (`src/resplan_router/phase8_copilot/wall_optimizer.py`)
- Implemented `ArchitecturalCoPilot`:
  - `analyze_floor_plan`: evaluates candidate perturbations using warm-started zone contiguity enumeration (`ZonePartitioner.enumerate_connected_zones`) and dead-end/detour routing shortcuts.
  - Generates clear architectural rationales explaining hub mergers, leaf room turnaround eliminations, or inter-room shortcuts.
  - Ranks proposals lexicographically: $\Delta H$ descending (hub drop highest priority), then $\Delta J$ descending.
  - `apply_proposal`: punches out the recommended doorway opening from obstacle geometries and merges into free space, allowing zero-collision routing and seamless checker compliance.

### 4. Unit & Regression Tests (`tests/test_phase8_copilot.py`)
- Test 1 (`test_shared_wall_candidate_generation`): verified interior wall detection on Slide 5 schematic and strict rejection of exterior walls and windows.
- Test 2 (`test_leaf_room_ghost_doorway_optimization`): verified Co-Pilot discovers ghost doorway shortcut for leaf room Bedroom 2 yielding $\Delta J > 0$.
- Test 3 (`test_hub_reduction_recommendation`): verified unlinked two-room plan (< 100 m^2) currently forcing $H=2$ receives doorway recommendation reducing hubs to $H=1$ ($\Delta H = 1$).
- Test 4 (`test_ranking_order`): verified strict lexicographic ranking ($\Delta H > 0$ strictly prioritised over $\Delta J > 0$).
- Test 5 (`test_apply_proposal_geometry`): verified `apply_proposal` modifies geometry and the updated plan passes `FloorPlanChecker.evaluate()` with 0 rule violations.

## Verification
- Unit test suite: `pytest tests/test_phase8_copilot.py -v` -> 5 passed in 7.21s.
- Full regression suite: `pytest tests/ -v` -> 83 passed, 0 failures across all phases in 48.70s.

## Commits
- `1ac6a93`: feat(phase-8): Architectural Co-Pilot Contracts & Candidate Perturbation Generator
- `dbe1808`: feat(phase-8): Co-Pilot Optimization Engine & Unit Tests
