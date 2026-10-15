---
phase: 5
plan: 1
wave: 1
status: completed
---

# Plan 5.1: Direction-Aware Exact Ring Sequencing & Contracts Summary

## Objective
Defined the Phase 5 ring sequencing data contracts (`RouteSegment`, `CompletedHubRing`, `SynthesizedRingsBundle`) and implemented the direction-aware Held-Karp dynamic programming ring solver (`DirectionalRingSolver` in `exact_ring_tsp.py`) that finds the optimal visiting sequence $H_k \to N_{\sigma(1)} \to \dots \to N_{\sigma(m)} \to H_k$ for each hub ($m \le 5$), pricing $90^\circ$ turns and $180^\circ$ reversals at nodes and the hub wrap-around connection.

## Completed Tasks

| Task | File(s) | Commit | Status |
|---|---|---|---|
| Phase 5 Ring Contracts | `src/resplan_router/contracts.py` | `888484c` | Done |
| Direction-Aware Held-Karp Ring TSP Solver | `src/resplan_router/phase5_ring_sequencing/__init__.py`, `exact_ring_tsp.py` | `f92cd2b` | Done |
| Ring TSP Unit Tests | `tests/test_phase5_tsp.py`, `src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py` | `14757ea` | Done |

## Key Implementations & Algorithmic Design

1. **Phase 5 Data Contracts (`src/resplan_router/contracts.py`):**
   - Added `RouteSegment`: Immutable model representing an individual directed leg between consecutive terminals, with axis-alignment polyline validator and length in metres.
   - Added `CompletedHubRing`: Encapsulates a complete closed ring route with `closed_sequence`, `route_segments`, `ring_length_m`, `ring_bends`, `ring_crossings`, `ring_overlap_m`, and `cost_J`. Includes validator enforcing Slide 3 Rule 4 closed ring invariants (starts and ends with `hub_id`, visits every assigned node exactly once).
   - Added `SynthesizedRingsBundle`: Plan-wide aggregate bundle storing all completed hub rings and composite metrics ($L$, $B$, $X$, $O$, $J$).

2. **Direction-Aware Held-Karp DP Solver (`exact_ring_tsp.py`):**
   - Implemented `DirectionalRingSolver`:
     - Bitmask dynamic programming state $(S, u, d_{\text{in}}, d_{\text{hub\_start}})$ over $m \le 5$ nodes.
     - Prices $+0.5$ bend cost at node $u$ whenever departure direction $d_{\text{out}} \neq d_{\text{in}}$ (both $90^\circ$ turns and $180^\circ$ reversals per Slide 7).
     - Prices $+0.5$ bend cost at hub $H_k$ whenever return arrival direction $d_{\text{hub\_end}} \neq d_{\text{hub\_start}}$.
     - Handles $m = 0$ base cases (`[H, H]`), $m = 1$ single-node loops (`[H, N1, H]`), and $m \ge 2$ general cases.
     - Canonicalizes symmetric equal-cost closed rings by prioritizing lower-indexed node visits first ($N_{\sigma(1)} \le N_{\sigma(m)}$).

3. **Slide 5 Benchmark Validation (`tests/test_phase5_tsp.py`):**
   - Validated Zone H1 visiting sequence matches `['H1', 'N1', 'N3', 'N2', 'H1']`.
   - Validated Zone H2 visiting sequence matches `['H2', 'N4', 'N5', 'N6', 'H2']`.
   - Verified single-node ring ($m=1$) turn count: 1 bend at node ($180^\circ$ reversal) and 1 bend at hub ($180^\circ$ reversal).
   - Execution performance: $< 2.7\text{ ms}$ per hub ring, well beneath the $< 5.0\text{ ms}$ threshold.

## Verification Evidence

- Contract import and validation:
  `python -c "from resplan_router.contracts import CompletedHubRing, RouteSegment, SynthesizedRingsBundle; print('Phase 5 contracts valid')"` -> `Phase 5 contracts valid`
- Solver import and validation:
  `python -c "from resplan_router.phase5_ring_sequencing.exact_ring_tsp import DirectionalRingSolver; print('exact_ring_tsp imported successfully')"` -> `exact_ring_tsp imported successfully`
- Test suite execution:
  `pytest tests/test_phase5_tsp.py -v` -> 6 passed in 1.36s
- Full regression across all phases:
  `pytest tests/` -> 49 passed in 7.36s (100% pass rate)

## Deviations & Adaptations

- **Ring Canonicalization (DEC-006)**: Since rectilinear Euclidean travel is symmetric, forward and reverse ring tours have identical length and bend counts. Added canonical tie-breaking ensuring the orientation with lower first-visited node index is selected, producing exact Slide 5 sequences `H1 -> N1 -> N3 -> N2 -> H1` and `H2 -> N4 -> N5 -> N6 -> H2`.

## Next Steps
Proceed to Plan 5.2 (Wave 2): Implement `loop_synthesizer.py` for physical rectilinear polyline synthesis with parallel doorway lane routing ($O = 0.0\text{ m}$) on single-door leaf rooms and Slide 5 benchmark validation ($L = 49.0\text{ m}, J = 59.0$).
