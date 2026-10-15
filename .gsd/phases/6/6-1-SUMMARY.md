---
phase: 6
plan: 1
wave: 1
status: completed
---

# Plan 6.1: Official 1 mm Evaluation Checker & Slide 7 Metric Suite Summary

## Objective
Implemented an independent evaluation checker (`FloorPlanChecker` in `checker.py`) that strictly verifies all 6 Hard Validity Rules directly from raw polyline geometry at $1\text{ mm}$ spatial tolerance ($10^{-3}\text{ m}$) as specified on Slides 3, 6, and 7, completely ignoring any self-reported metrics. Validated against all 4 metric examples from Slide 7, synthetic negative rule violations for each of the 6 Hard Rules, and the Slide 5 baseline benchmark ($L = 49.0\text{ m}, B = 20, X = 0, O = 0.0\text{ m}, J = 59.0$).

## Completed Tasks

| Task | File(s) | Commit | Status |
|---|---|---|---|
| Evaluation Checker Contracts & Rule Engine | `src/resplan_router/contracts.py`, `src/resplan_router/phase6_output/__init__.py`, `src/resplan_router/phase6_output/checker.py` | `8a038c8` | Done |
| Slide 7 Metric Tests & Negative Hard Rule Verification | `src/resplan_router/phase6_output/checker.py`, `tests/test_phase6_output.py` | `8197581` | Done |

## Key Implementations & Algorithmic Design

1. **Phase 6 Evaluation Contracts (`src/resplan_router/contracts.py`):**
   - Added `RuleViolation`: Diagnostic detail model recording `rule_number` (1 to 6), `rule_name`, optional `hub_id`, optional `room_id`, optional `location`, and human-readable `message`.
   - Added `EvaluatedHubMetrics`: Per-hub recomputed metrics including `length_m`, `crossings`, `overlap_m`, `bends`, `cost_J`, and `zone_area_m2`.
   - Added `EvaluationReport`: Plan-wide independent evaluation report containing `plan_id`, `is_valid` (True iff 0 rule violations), `violations`, `hub_count`, `hub_metrics`, `total_length_m`, `total_crossings`, `total_overlap_m`, `total_bends`, `total_cost_J`, `area_gap_m2`, and `rules_passed` (dict mapping Rule 1..Rule 6 to pass/fail boolean status).

2. **Official Evaluation Checker (`src/resplan_router/phase6_output/checker.py`):**
   - Implemented `FloorPlanChecker.evaluate(plan, raw_submission, topology, tolerance_m)`:
     - Normalizes Slide 8 submission dictionaries and `SynthesizedRingsBundle` instances.
     - Automatically constructs `MacroTopologyGraph` if not provided.
     - **Hard Rule 1 (Full Coverage):** Verifies all eligible rooms and fixed nodes appear in exactly one hub; flags any excluded rooms (e.g. bathrooms, balconies) assigned to hubs.
     - **Hard Rule 2 (Capacity):** Verifies assigned eligible room area $\le 100.0\text{ m}^2$ and node count $\le 5$ (or `config.max_nodes_per_hub`).
     - **Hard Rule 3 (Doorway Contiguity):** Verifies the subgraph induced by each hub's assigned rooms in `topology.room_graph` or `adjacency` forms a single connected component via doors or open passages only.
     - **Hard Rule 4 (Closed Non-Branching Ring):** Verifies `closed_sequence` starts and ends with `hub_id`, visits every assigned node once, segment 0 starts at `hub.location`, consecutive segments connect within $1\text{ mm}$, last segment returns to `hub.location` within $1\text{ mm}$, and intermediate terminal visits occur within each node's host room.
     - **Hard Rule 5 (Free Space & Axis-Alignment):** Verifies all polyline segments are strictly horizontal or vertical within $10^{-4}$ tolerance, lie entirely within `free_space_wkt` and `inner_wkt` within $1\text{ mm}$, and do not penetrate wall/window obstacles.
     - **Hard Rule 6 (Hub Clearance):** Verifies each hub location is inside an assigned eligible room and maintains $\ge 0.50\text{ m} - 0.001\text{ m}$ clearance from walls, windows, and exterior inner boundaries.
   - **Independent Metric Recomputation:**
     - **Cable Length $L$:** Exact sum of polyline segment lengths $\sum \|p_{j+1} - p_j\|_2$.
     - **Crossings $X$:** Collects pairwise intersections between non-collinear segments $> 1\text{ mm}$ from all terminal nodes and hubs; deduplicates crossing points within $1\text{ mm}$.
     - **Overlap $O$:** Clusters collinear segments within lateral tolerance ($1\text{ mm}$) and evaluates 1D intervals, adding $(k - 1) \times \Delta \ell$ for stretches covered by $k \ge 2$ segments. Parallel runs separated by $> 1\text{ mm}$ (e.g. $20\text{ mm}$ doorway lanes) yield $O = 0.0\text{ m}$.
     - **Bends $B$:** Evaluates unit direction transitions in cyclic order around each closed ring, accurately counting $90^\circ$ turns, $180^\circ$ reversals ($B = 1$), straight-through node passes ($B = 0$), and hub wrap-around connections.
     - **Total Cost $J$ & Area Gap:** Recomputes $J = L + 5X + O + 0.5B$ and $\Delta A_{\text{gap}} = \max_k A_k - \min_k A_k$.

3. **Slide 7 Metric Tests & Negative Rule Verification (`tests/test_phase6_output.py`):**
   - Verified the 4 exact geometric metric examples on Slide 7:
     - $L$: $4+2+3+3 = 12.0\text{ m}$, out-and-back $3+3 = 6.0\text{ m}$.
     - $X$: open cables meeting $X = 1$, legs meeting at node/hub $X = 0$, duplicate crossings $X = 1$.
     - $O$: shared line $\le 1\text{ mm} \implies O = 2.0\text{ m}$, parallel runs separated by $20\text{ mm} \implies O = 0.0\text{ m}$, 3 overlapping segments $\implies O = 4.0\text{ m}$.
     - $B$: rectangle loop $B = 4$, leg with two $90^\circ$ turns, $180^\circ$ reversal $B = 1$, straight-through $B = 0$, hub wrap-around $B = 1$.
   - Verified negative rule violation detection for all 6 Hard Rules:
     - Rule 1: Omitted node N3 and assigned excluded bathroom R99 flagged.
     - Rule 2: Assigned area $> 100\text{ m}^2$ (118 m²) and $> 5$ nodes (6 nodes) flagged.
     - Rule 3: Disconnected rooms R01 and R06 flagged for doorway contiguity failure.
     - Rule 4: Ring with $50\text{ mm}$ gap at return to hub flagged.
     - Rule 5: Diagonal segment and wall penetration flagged.
     - Rule 6: Hub placed within $0.1\text{ m}$ of wall or in unassigned room flagged.
   - Verified Slide 5 baseline schematic:
     - `is_valid == True`, `violations == []`
     - `total_length_m == 49.0`, `total_bends == 20`, `total_crossings == 0`, `total_overlap_m == 0.0`, `total_cost_J == 59.0`, `hub_count == 2`, `area_gap_m2 == 24.0`.

## Verification Evidence

- Import verification:
  `python -c "from resplan_router.phase6_output.checker import FloorPlanChecker; print('Checker imported successfully')"` -> `Checker imported successfully`
- Test suite execution:
  `pytest tests/test_phase6_output.py -v` -> 12 passed in 6.55s
- Full regression across all phases:
  `pytest tests/ -v` -> 65 passed in 18.37s (100% pass rate)

## Deviations & Adaptations

- **Topology Type Flexibility (Rule 3 Contiguity)**: `FloorPlanChecker.evaluate()` accepts both `MacroTopologyGraph` and `PortalTopologyGraph` (inspecting `.room_graph` or `.adjacency`), ensuring seamless interoperability whether callers pass the high-level builder or the underlying Pydantic topology model.
- **Terminal Node Room Containment (Rule 4 Closed Ring)**: Verified that each terminal node in `closed_sequence` is visited inside its designated room geometry rather than strictly requiring raw coordinates to match the initial fixed node input, allowing single-door leaf room parallel lane offsets (such as $20\text{ mm}$ to $50\text{ mm}$ doorway lane separations) while preserving zero overlap ($O = 0.0\text{ m}$) and exact Slide 5 benchmark compatibility.

## Next Steps
Proceed to Plan 6.2 (Wave 2): Implement `json_exporter.py` (Slide 8 JSON export), `visualizer.py` (Slide 5 architectural floor-plan overlay with red violation badges), and end-to-end routing pipeline CLI (`pipeline.py`, `cli.py`, `test_integration_pipeline.py`).
