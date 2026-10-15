---
phase: 5
plan: 1
wave: 1
gap_closure: false
---

# Plan 5.1: Direction-Aware Exact Ring Sequencing & Contracts

## Objective
Define the Phase 5 ring sequencing data contracts (`RouteSegment`, `CompletedHubRing`, `SynthesizedRingsBundle`) and implement the direction-aware Held-Karp dynamic programming ring solver (`exact_ring_tsp.py`) that finds the optimal visiting sequence $H_k \to N_{\sigma(1)} \to \dots \to N_{\sigma(m)} \to H_k$ for each hub ($m \le 5$), pricing $90^\circ$ turns and $180^\circ$ reversals at nodes and the hub wrap-around connection.

## Context
- `v2.md` (Sections 2.1, 2.2, 4, 5.5)
- `.gsd/SPEC.md`
- `.gsd/phases/5/RESEARCH.md`
- `src/resplan_router/contracts.py`

## Tasks

<task type="auto">
  <name>Phase 5 Ring Contracts</name>
  <files>
    src/resplan_router/contracts.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` to add Phase 5 contracts:
       - `RouteSegment`:
         - `from_node: str` (e.g. `H1` or `N1`).
         - `to_node: str`.
         - `length_m: float` (cable length in metres, ge=0.0).
         - `polyline: list[Point2D]` (ordered sequence of axis-aligned vertices).
       - `CompletedHubRing`:
         - `hub_id: str`.
         - `room_id: str`.
         - `location: Point2D`.
         - `assigned_rooms: list[str]`.
         - `assigned_nodes: list[str]`.
         - `closed_sequence: list[str]` (starts and ends with `hub_id`).
         - `route_segments: list[RouteSegment]`.
         - `ring_length_m: float`.
         - `ring_bends: int`.
         - `ring_crossings: int` (default 0).
         - `ring_overlap_m: float` (default 0.0).
         - `cost_J: float` ($J = L + 5X + O + 0.5B$).
       - `SynthesizedRingsBundle`:
         - `plan_id: str`.
         - `hub_rings: list[CompletedHubRing]`.
         - `total_length_m: float`.
         - `total_bends: int`.
         - `total_crossings: int`.
         - `total_overlap_m: float`.
         - `total_cost_J: float`.
    2. Add validators ensuring each `closed_sequence` starts and ends with `hub_id` and visits each assigned node exactly once.

    AVOID: Unvalidated loop representations.
    USE: Immutable Pydantic models enforcing Slide 3 Rule 4 closed ring invariants.
  </action>
  <verify>
    python -c "from resplan_router.contracts import CompletedHubRing, RouteSegment, SynthesizedRingsBundle; print('Phase 5 contracts valid')"
  </verify>
  <done>
    `CompletedHubRing` and `SynthesizedRingsBundle` models import and validate successfully.
  </done>
</task>

<task type="auto">
  <name>Direction-Aware Held-Karp Ring TSP Solver</name>
  <files>
    src/resplan_router/phase5_ring_sequencing/__init__.py
    src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py
  </files>
  <action>
    1. Create `src/resplan_router/phase5_ring_sequencing/__init__.py`.
    2. Implement `src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py`:
       - Class `DirectionalRingSolver`:
         - Takes hub assignment, assigned nodes, and `PairwiseCostTensor`.
         - Implements bitmask dynamic programming over state $(S, u, d_{\text{in}}, d_{\text{hub\_start}})$:
           - $S \subseteq \{1, \dots, m\}$: visited bitmask.
           - $u \in \{1, \dots, m\}$: current node.
           - $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$: arrival direction at $u$.
           - $d_{\text{hub\_start}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$: initial departure direction from hub.
         - Incorporates $+0.5$ bend cost at node $u$ if departure direction $d_{\text{out}} \neq d_{\text{in}}$.
         - Evaluates return leg from last node back to hub, adding $+0.5$ bend cost at hub if $d_{\text{hub\_end}} \neq d_{\text{hub\_start}}$.
         - Reconstructs optimal node sequence $[H_k, N_{\sigma(1)}, \dots, N_{\sigma(m)}, H_k]$.
         - Method `solve_hub_ring_sequence(hub: HubAssignment, nodes: list[FixedNode], cost_tensor: PairwiseCostTensor) -> tuple[list[str], list[tuple[Optional[CompassDirection], Optional[CompassDirection]]]]`.

    AVOID: Standard Euclidean TSP which ignores turn counts at nodes and hubs.
    USE: Directional state-space Held-Karp DP optimizing the true cost $J = L + 0.5B$.
  </action>
  <verify>
    python -c "from resplan_router.phase5_ring_sequencing.exact_ring_tsp import DirectionalRingSolver; print('exact_ring_tsp imported successfully')"
  </verify>
  <done>
    `exact_ring_tsp.py` solves optimal direction-aware ring sequence with node and hub turn accounting.
  </done>
</task>

<task type="auto">
  <name>Ring TSP Unit Tests</name>
  <files>
    tests/test_phase5_tsp.py
  </files>
  <action>
    1. Create `tests/test_phase5_tsp.py` testing:
       - Single-node hub ring ($m = 1$): verifies sequence `[H, N1, H]` and departure/return turn count.
       - Slide 5 Zone H1 nodes (`N1`, `N2`, `N3`): verifies sequence `[H1, N1, N3, N2, H1]`.
       - Slide 5 Zone H2 nodes (`N4`, `N5`, `N6`): verifies sequence `[H2, N4, N5, N6, H2]`.
       - Turn count verification: verifies that turns at nodes and hubs match expected values.

    AVOID: Skipping small base cases ($m=1$).
    USE: Explicit tests for $m=1, 2, 3$ with exact expected sequence matching Slide 5.
  </action>
  <verify>
    pytest tests/test_phase5_tsp.py -v
  </verify>
  <done>
    Ring TSP unit tests pass 100% with Slide 5 visiting sequences verified.
  </done>
</task>

## Must-Haves
- [ ] Ring sequence starts and ends with `hub_id`, visiting each assigned node exactly once.
- [ ] Node turns and hub wrap-around turns are accurately priced in dynamic program.
- [ ] Slide 5 visiting sequences match `H1 -> N1 -> N3 -> N2 -> H1` and `H2 -> N4 -> N5 -> N6 -> H2`.

## Success Criteria
- [ ] `pytest tests/test_phase5_tsp.py` passes 100%.
- [ ] Execution runtime $< 5\text{ ms}$ per hub ring.
