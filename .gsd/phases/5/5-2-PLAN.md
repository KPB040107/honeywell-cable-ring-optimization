---
phase: 5
plan: 2
wave: 2
gap_closure: false
depends_on:
  - 5-1-PLAN.md
---

# Plan 5.2: Sequential Conflict-Free Polyline Synthesis & Slide 5 Validation

## Objective
Implement the physical rectilinear polyline synthesis engine (`loop_synthesizer.py`) that routes closed rings sequentially, routing single-door leaf rooms through Parallel Door Lanes A and B separated by $\ge 20\text{ mm}$ to guarantee zero overlap ($O = 0.0\text{ m}$) and zero crossings ($X = 0$), merging collinear segments, and validating exact Slide 5 benchmark metrics ($L = 49.0\text{ m}, B = 20, X = 0, O = 0.0\text{ m}, J = 59.0$).

## Context
- `v2.md` (Sections 2.1, 2.2, 2.3, 5.5)
- `.gsd/SPEC.md`
- `.gsd/phases/5/RESEARCH.md`
- `.gsd/phases/5/5-1-PLAN.md`
- `tests/fixtures/slide5_schematic.json`

## Tasks

<task type="auto">
  <name>Sequential Conflict-Free Loop Synthesizer</name>
  <files>
    src/resplan_router/phase5_ring_sequencing/loop_synthesizer.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase5_ring_sequencing/loop_synthesizer.py`:
       - Class `LoopSynthesizer`:
         - Method `synthesize_ring(hub: HubAssignment, sequence: list[str], plan: MetricFloorPlan, topology: PortalTopologyGraph, router: DirectionStateRouter, grid: HananRoutingGrid) -> CompletedHubRing`:
           - Extracts sequence vertices: $[v_0 (= H_k), v_1, \dots, v_m, v_{m+1} (= H_k)]$.
           - Tracks single-door leaf rooms from `topology.leaf_room_ids` (e.g. `Bedroom 2` and `Storage` on Slide 5).
           - When entering a single-door room: routes the incoming leg through the portal's **Lane A** waypoint ($p_A$) and room step point.
           - When leaving the single-door room: routes the outgoing leg through the portal's **Lane B** waypoint ($p_B$) and room step point.
           - Because $\|p_A - p_B\|_2 \ge 20\text{ mm} > 1\text{ mm}$, the incoming and outgoing legs run on separate lines, guaranteeing **Overlap $O = 0.0\text{ m}$** per Slide 7!
           - Dynamic usage marking: after synthesizing each leg, adds overlap penalty ($+1.0/\text{m}$) and crossing penalty ($+5.0$) to traversed grid edges to ensure subsequent legs avoid colliding or crossing.
           - Merges consecutive collinear polyline segments so internal vertices represent true $90^\circ$ turns.
           - Computes exact segment lengths, total ring length $L$, bend count $B$, overlap $O$, crossings $X$, and cost $J = L + 5X + O + 0.5B$.
         - Method `synthesize_all_rings(partition: HubZonePartition, plan: MetricFloorPlan, topology: PortalTopologyGraph, router: DirectionStateRouter, grid: HananRoutingGrid) -> SynthesizedRingsBundle`:
           - Iterates over all hubs in the partition.
           - Compiles `SynthesizedRingsBundle` with plan-wide totals.

    AVOID: Retracing the exact doorway centerline into single-door rooms.
    USE: Parallel doorway lanes (Lane A in, Lane B out) separated by $\ge 20\text{ mm}$.
  </action>
  <verify>
    python -c "from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer; print('loop_synthesizer imported successfully')"
  </verify>
  <done>
    `LoopSynthesizer` generates closed conflict-free polylines with zero overlap on single-door leaf rooms.
  </done>
</task>

<task type="auto">
  <name>Slide 5 Benchmark Validation & Phase 5 Test Suite</name>
  <files>
    tests/test_phase5_ring_sequencing.py
  </files>
  <action>
    1. Build `tests/test_phase5_ring_sequencing.py` implementing the official verification gates from `v2.md` Section 5.5.3:
       - **Test 5.1 (`test_slide5_ring_metrics`)**:
         - Run full pipeline through Phase 5 on Slide 5 schematic.
         - Assert `H1` visits `H1 -> N1 -> N3 -> N2 -> H1` with $L \approx 23.8\text{ m}$, $B = 8$, $O = 0.0\text{ m}$, $X = 0$, $J_1 = 27.8$.
         - Assert `H2` visits `H2 -> N4 -> N5 -> N6 -> H2` with $L \approx 25.2\text{ m}$, $B = 12$, $O = 0.0\text{ m}$, $X = 0$, $J_2 = 31.2$.
         - Assert total plan cable length $L = 49.0\text{ m} \pm 0.5\text{ m}$, total bends $B = 20$, total crossings $X = 0$, total overlap $O = 0.0\text{ m}$, and total cost $J = 59.0 \pm 0.5$.
       - **Test 5.2 (`test_single_door_parallel_runs_zero_overlap`)**:
         - Verify that on single-door leaf rooms (`Bedroom 2`, `Storage`), incoming and outgoing polylines stay $> 1\text{ mm}$ apart everywhere except at the fixed node point itself, yielding $O = 0.0\text{ m}$ and $X = 0$.
       - **Test 5.3 (`test_node_and_hub_turn_counting`)**:
         - Verify that turns at fixed nodes and at the hub wrap-around connection are counted in total ring bends $B$.
       - **Test 5.4 (`test_phase1_to_phase5_regression`)**:
         - Re-verify cross-phase pipeline integrity from Phase 1 through Phase 5.

    AVOID: Allowing overlap on single-door rooms.
    USE: Strict lateral separation assertions ensuring $O = 0.0\text{ m}$.
  </action>
  <verify>
    pytest tests/test_phase5_ring_sequencing.py -v
  </verify>
  <done>
    Phase 5 test suite passes 100%, exactly replicating Slide 5 ring metrics ($L=49.0\text{ m}, J=59.0$).
  </done>
</task>

## Must-Haves
- [ ] Closed ring visits every assigned node once and returns to the originating hub without branches.
- [ ] Single-door rooms use Parallel Door Lanes A and B, achieving $O = 0.0\text{ m}$ and $X = 0$.
- [ ] Slide 5 achieves total cable length $L = 49.0\text{ m}$ and total cost $J = 59.0$.
- [ ] Turns at nodes and hubs are accurately counted in $B$.

## Success Criteria
- [ ] `pytest tests/test_phase5_ring_sequencing.py` passes 100%.
- [ ] Zero overlap ($O = 0.0\text{ m}$) and zero crossings ($X = 0$) on standard layouts.
- [ ] End-to-end Phase 1–5 regression passes cleanly.
