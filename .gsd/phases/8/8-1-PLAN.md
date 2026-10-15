---
phase: 8
plan: 1
wave: 1
gap_closure: false
---

# Plan 8.1: Architectural Co-Pilot Perturbation Engine & Ranking Core

## Objective
Implement the "Architectural Co-Pilot" perturbation search engine (`wall_optimizer.py`) and data contracts (`contracts.py`). The engine identifies candidate structural modifications—adding ghost open passages on unlinked shared interior walls and sliding existing doors along walls—evaluates the resulting topology with warm-started routing/partitioning, and ranks proposals lexicographically by hub reduction $\Delta H$ and cost reduction $\Delta J$.

## Context
- `v2.md` (Section 7, Phase 8)
- `.gsd/SPEC.md`
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase2_topology/portal_extractor.py`
- `src/resplan_router/phase2_topology/macro_graph.py`
- `src/resplan_router/phase4_partitioning/zone_milp.py`
- `src/resplan_router/phase5_ring_sequencing/loop_synthesizer.py`
- `src/resplan_router/phase7_interactive/incremental_router.py`

## Tasks

<task type="auto">
  <name>Architectural Co-Pilot Contracts & Candidate Perturbation Generator</name>
  <files>
    src/resplan_router/contracts.py
    src/resplan_router/phase8_copilot/__init__.py
    src/resplan_router/phase8_copilot/candidate_generator.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` to add Phase 8 contracts:
       - `ProposalType(str, Enum)`: `ADD_GHOST_DOOR = "add_ghost_door"`, `SLIDE_DOOR = "slide_door"`.
       - `StructuralProposal`:
         - `proposal_id: str`.
         - `proposal_type: ProposalType`.
         - `room_a_id: str`.
         - `room_b_id: str`.
         - `wall_segment: list[Point2D]` (endpoints of the shared wall).
         - `suggested_door_center: Point2D`.
         - `door_width_m: float` (default 0.90 m).
         - `delta_hubs: int` ($H_{\text{base}} - H_{\text{new}}$).
         - `delta_cost_J: float` ($J_{\text{base}} - J_{\text{new}}$).
         - `delta_length_m: float`.
         - `delta_bends: int`.
         - `original_cost_J: float`.
         - `new_cost_J: float`.
         - `rationale: str`.
       - `CoPilotReport`:
         - `plan_id: str`.
         - `baseline_hubs: int`.
         - `baseline_cost_J: float`.
         - `proposals_evaluated: int`.
         - `recommended_proposals: list[StructuralProposal]`.

    2. Create `src/resplan_router/phase8_copilot/__init__.py`.

    3. Implement `src/resplan_router/phase8_copilot/candidate_generator.py`:
       - Class `PerturbationCandidateGenerator`:
         - Method `find_shared_interior_walls(plan: MetricFloorPlan, topology: MacroTopologyGraph) -> list[dict]`:
           - For all pairs of adjacent eligible rooms $(R_a, R_b)$:
             - Compute intersection of polygons `R_a.polygon.intersection(R_b.polygon)`.
             - Extract shared 1D boundary line segments with length $\ge 1.0\text{ m}$.
             - Exclude segments overlapping exterior boundaries or windows.
             - Check if a direct portal already exists between $R_a$ and $R_b$.
             - If no direct portal exists (or room is a leaf room), sample candidate ghost door centers along the shared wall at intervals (e.g. midpoint, 25%, 75%), ensuring $\ge 0.5\text{ m}$ clearance to wall corners.
         - Method `find_sliding_door_candidates(plan: MetricFloorPlan, topology: MacroTopologyGraph) -> list[dict]`:
           - For each existing interior door portal:
             - Determine the host wall line segment.
             - Check if door can slide $\pm 0.5\text{ m}$, $\pm 1.0\text{ m}$ along the wall while preserving clearance to wall ends.
         - Returns clean list of candidate structural perturbations with geometry.

    AVOID: Blindly proposing doorways on exterior walls or across windows.
    USE: Exact Shapely intersection geometry on eligible room boundaries.
  </action>
  <verify>
    python -c "from resplan_router.phase8_copilot.candidate_generator import PerturbationCandidateGenerator; print('Candidate generator imported')"
  </verify>
</task>

<task type="auto">
  <name>Co-Pilot Optimization Engine & Unit Tests</name>
  <files>
    src/resplan_router/phase8_copilot/wall_optimizer.py
    tests/test_phase8_copilot.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase8_copilot/wall_optimizer.py`:
       - Class `ArchitecturalCoPilot`:
         - Method `analyze_floor_plan(plan: MetricFloorPlan, topology: MacroTopologyGraph, baseline_partition: HubZonePartition, baseline_bundle: SynthesizedRingsBundle, max_recommendations: int = 5) -> CoPilotReport`:
           - Computes baseline $H_{\text{base}}$ and $J_{\text{base}}$.
           - Generates candidate structural perturbations using `PerturbationCandidateGenerator`.
           - For each candidate perturbation:
             - Synthesizes temporary perturbed floor plan and portal topology.
             - Re-evaluates routing feasibility, zone partition, and closed-ring Held-Karp sequence.
             - Computes $\Delta H = H_{\text{base}} - H_{\text{new}}$ and $\Delta J = J_{\text{base}} - J_{\text{new}}$.
             - If $\Delta H > 0$ or $\Delta J > 0.1$, formats a `StructuralProposal` with detailed architectural rationale explaining the cable path shortcut or zone unification.
           - Ranks proposals lexicographically: $\Delta H$ descending (hub reduction highest priority), then $\Delta J$ descending.
           - Returns `CoPilotReport` with top $N$ recommendations.
         - Method `apply_proposal(proposal: StructuralProposal, plan: MetricFloorPlan) -> MetricFloorPlan`:
           - Applies the selected perturbation: adds the new doorway polygon to the plan's door list or updates the existing door position.

    2. Implement `tests/test_phase8_copilot.py`:
       - **Test 1 (`test_shared_wall_candidate_generation`):**
         - Verifies shared wall detection between adjacent rooms in Slide 5 schematic.
         - Asserts exterior walls and windows are strictly excluded.
       - **Test 2 (`test_leaf_room_ghost_doorway_optimization`):**
         - On a floor plan where a leaf room requires a long out-and-back detour (e.g. `Bedroom 2` or synthetic plan), asserts Co-Pilot finds a ghost doorway proposal that yields $\Delta J > 0$.
       - **Test 3 (`test_hub_reduction_recommendation`):**
         - Tests a floor plan where two eligible rooms totaling $< 100\text{ m}^2$ are separated by a solid wall with no door, currently forcing 2 separate hubs ($H=2$).
         - Asserts Co-Pilot proposes a doorway connecting them, reducing hubs to $H=1$ ($\Delta H = 1$).
       - **Test 4 (`test_ranking_order`):**
         - Asserts proposals with $\Delta H > 0$ are ranked above proposals with only $\Delta J > 0$.
       - **Test 5 (`test_apply_proposal_geometry`):**
         - Verifies that `apply_proposal` adds a valid portal to the plan and the updated plan passes `FloorPlanChecker.evaluate()`.
  </action>
  <verify>
    pytest tests/test_phase8_copilot.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase8_copilot.py -v`.
- Re-run full test suite `pytest tests/ -v` to ensure zero regressions across all phases.

## Success Criteria
- Co-Pilot accurately detects shared interior walls and movable doorways.
- Evaluates candidate modifications and ranks them by $\Delta H$ and $\Delta J$.
- Generates clear, actionable architectural rationales.
- All tests pass with 100% success rate.
