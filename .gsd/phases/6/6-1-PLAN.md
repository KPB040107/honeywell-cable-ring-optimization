---
phase: 6
plan: 1
wave: 1
gap_closure: false
---

# Plan 6.1: Official 1 mm Evaluation Checker & Slide 7 Metric Suite

## Objective
Implement an independent evaluation checker (`checker.py`) that strictly verifies all 6 Hard Validity Rules from raw exported polylines and recomputes the exact competition metrics ($L, X, O, B, J, \Delta A_{\text{gap}}$) at $1\text{ mm}$ spatial tolerance ($10^{-3}\text{ m}$) as specified on Slides 3, 6, and 7, ignoring any self-reported metrics. Validate against all 4 metric examples from Slide 7 and synthetic negative rule violations.

## Context
- `v2.md` (Sections 2.1, 2.2, 5.6)
- `Cable Ring Challenge - Problem Statement4.pptx` (Slides 3, 6, 7, 8)
- `.gsd/SPEC.md`
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase6_output/checker.py`
- `tests/test_phase6_output.py`

## Tasks

<task type="auto">
  <name>Evaluation Checker Contracts & Rule Engine</name>
  <files>
    src/resplan_router/contracts.py
    src/resplan_router/phase6_output/__init__.py
    src/resplan_router/phase6_output/checker.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` with Phase 6 evaluation contracts:
       - `RuleViolation`:
         - `rule_number: int` (1 to 6).
         - `rule_name: str` (e.g. "Full Coverage", "Capacity", "Doorway Contiguity", "Closed Ring", "Free Space", "Hub Clearance").
         - `hub_id: str | None`.
         - `room_id: str | None`.
         - `location: Point2D | None`.
         - `message: str`.
       - `EvaluatedHubMetrics`:
         - `hub_id: str`.
         - `room_id: str`.
         - `location: Point2D`.
         - `assigned_rooms: list[str]`.
         - `assigned_nodes: list[str]`.
         - `closed_sequence: list[str]`.
         - `length_m: float`.
         - `crossings: int`.
         - `overlap_m: float`.
         - `bends: int`.
         - `cost_J: float`.
         - `zone_area_m2: float`.
       - `EvaluationReport`:
         - `plan_id: str`.
         - `is_valid: bool` (True iff 0 rule violations).
         - `violations: list[RuleViolation]`.
         - `hub_count: int`.
         - `hub_metrics: list[EvaluatedHubMetrics]`.
         - `total_length_m: float`.
         - `total_crossings: int`.
         - `total_overlap_m: float`.
         - `total_bends: int`.
         - `total_cost_J: float`.
         - `area_gap_m2: float`.
         - `rules_passed: dict[str, bool]` (keys: Rule 1..Rule 6).

    2. Create `src/resplan_router/phase6_output/__init__.py`.

    3. Implement `src/resplan_router/phase6_output/checker.py`:
       - Class `FloorPlanChecker`:
         - Static method `evaluate(plan: MetricFloorPlan, raw_submission: dict | SynthesizedRingsBundle, topology: MacroTopologyGraph | None = None, tolerance_m: float = 0.001) -> EvaluationReport`:
           - Extracts hubs and raw route segments from `raw_submission` (dict adhering to Slide 8 or `SynthesizedRingsBundle`).
           - **Hard Rule 1 (Full Coverage):**
             - Every eligible room in `plan.rooms` must appear in exactly one hub's `assigned_rooms`.
             - Every fixed node in `plan.nodes` must appear in exactly one hub's `assigned_nodes`.
             - Excluded rooms must NOT be assigned to any hub.
           - **Hard Rule 2 (Capacity):**
             - For each hub: assigned eligible room area sum $\le 100.0\text{ m}^2 + 10^{-4}\text{ m}^2$.
             - Assigned fixed node count $\le 5$ (or `config.max_nodes_per_hub`).
           - **Hard Rule 3 (Doorway / Open-Passage Contiguity):**
             - Hub's assigned rooms must form a connected subgraph in `topology` (or verify each room transition along route segments passes through a valid portal in `plan.portals` or shapely boundary door buffer).
           - **Hard Rule 4 (Closed Non-Branching Ring):**
             - For each hub, `closed_sequence` must begin and end at `hub_id`.
             - Sequence of `route_segments` must form a continuous chain connecting `closed_sequence` vertices: segment $i$'s endpoint must match segment $i+1$'s start point within `tolerance_m` ($1\text{ mm}$).
             - Last segment endpoint must match first segment start point (`hub.location`) within `tolerance_m`.
             - Intermediate assigned nodes must each be visited exactly once.
           - **Hard Rule 5 (Axis-Aligned Free Space):**
             - Every segment in every route must be strictly axis-aligned (horizontal or vertical within $10^{-6}\text{ rad}$).
             - Every segment line must lie entirely within free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$ within $1\text{ mm}$ tolerance (`obstacle.distance(segment) >= -0.001`).
           - **Hard Rule 6 (Hub Clearance):**
             - Each hub location must be inside its designated `room_id`.
             - `room_id` must be an eligible room assigned to that hub.
             - Distance from hub location to all walls and windows must be $\ge 0.50\text{ m} - 0.001\text{ m}$.
           - **Metric Recomputations (ignoring reported metrics):**
             - **Length $L$:** Exact sum of polyline segment lengths $\sum \|p_{j+1} - p_j\|_2$.
             - **Crossings $X$:** Collect all line segments across all hubs. For every pair of non-collinear segments, if they intersect in a single point, check whether that point is $> 1\text{ mm}$ away from ALL fixed nodes and hub locations. If so, record crossing. Deduplicate crossing points within $1\text{ mm}$.
             - **Overlap $O$:** Find parallel collinear segments within $\le 1\text{ mm}$ lateral distance. Project onto 1D axis, compute interval overlap length. Multiply by $(k - 1)$ where $k \ge 2$ is the number of overlapping segments. Parallel segments $> 1\text{ mm}$ apart yield $O = 0.0\text{ m}$.
             - **Bends $B$:** For each closed ring, traverse all directed segments in cycle order (including from segment $N_{\text{last}} \to H_k$ to segment $H_k \to N_{\text{first}}$). Ignore zero-length segments ($< 1\text{ mm}$). Increment $B$ by 1 whenever unit direction $\hat{d}_{i+1} \neq \hat{d}_i$ ($90^\circ$ turns and $180^\circ$ reversals).
             - **Cost $J$:** $J = L + 5X + O + 0.5B$.
             - **Area Gap:** $\Delta A_{\text{gap}} = \max_k A_k - \min_k A_k$.

    AVOID: Relying on reported fields in submission JSON; everything must be independently computed from raw polylines.
    USE: Shapely geometry operations with $1\text{ mm}$ buffer/distance tolerances matching official challenge requirements.
  </action>
  <verify>
    python -c "from resplan_router.phase6_output.checker import FloorPlanChecker; print('Checker imported successfully')"
  </verify>
</task>

<task type="auto">
  <name>Slide 7 Metric Tests & Negative Hard Rule Verification</name>
  <files>
    tests/test_phase6_output.py
  </files>
  <action>
    Create `tests/test_phase6_output.py` to rigorously test:
    1. **Slide 7 Metric Unit Tests:**
       - **Test Length $L$:**
         - Segments 4m + 2m + 3m + 3m $\implies L = 12.0\text{ m}$.
         - Out-and-back parallel segments 3m out + 3m back $\implies L = 6.0\text{ m}$.
       - **Test Crossings $X$:**
         - Two cables crossing orthogonally in the open $\implies X = 1$ ($5.0$ penalty).
         - Two legs meeting at a node or hub $\implies X = 0$ ($0.0$ penalty).
         - Two crossings at the same location deduplicated $\implies X = 1$.
       - **Test Overlap $O$:**
         - Two 2m segments sharing the exact same line ($\le 1\text{ mm}$) $\implies O = 2.0\text{ m}$.
         - Two 2m segments running parallel separated by $20\text{ mm}$ ($> 1\text{ mm}$) $\implies O = 0.0\text{ m}$.
       - **Test Bends $B$:**
         - Two $90^\circ$ turns along path $\implies B = 2$.
         - $180^\circ$ reversal at a node $\implies B = 1$.
         - Straight path through a node $\implies B = 0$.
         - Wrap-around bend at hub when return vector differs from departure vector $\implies B = 1$.
    2. **Slide 3 Negative Rule Violations:**
       - Synthetic test injecting Rule 1 violation (omitted node/room or excluded bathroom assigned).
       - Synthetic test injecting Rule 2 violation (assigned area $> 100\text{ m}^2$ or $> 5$ nodes).
       - Synthetic test injecting Rule 3 violation (zone disconnected across walls with no door).
       - Synthetic test injecting Rule 4 violation (open ring with $50\text{ mm}$ gap at hub return).
       - Synthetic test injecting Rule 5 violation (diagonal segment or segment intersecting wall).
       - Synthetic test injecting Rule 6 violation (hub within $0.3\text{ m}$ of exterior wall).
       - Assert that `checker.evaluate()` marks `is_valid == False` and reports the exact `RuleViolation`.
    3. **Slide 5 Baseline Validation:**
       - Evaluate Slide 5 synthesized bundle using `FloorPlanChecker.evaluate()` and assert:
         - `is_valid == True`
         - `violations == []`
         - `total_length_m == 49.0`
         - `total_bends == 20`
         - `total_crossings == 0`
         - `total_overlap_m == 0.0`
         - `total_cost_J == 59.0`
         - `hub_count == 2`
         - `rules_passed` all True.
  </action>
  <verify>
    pytest tests/test_phase6_output.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase6_output.py -v` to confirm checker correctness on Slide 7 metrics and negative rule tests.
- Re-run full regression suite `pytest tests/ -v`.

## Success Criteria
- Independent `FloorPlanChecker` accurately computes $L, X, O, B, J, \Delta A_{\text{gap}}$ at $1\text{ mm}$ tolerance.
- All 4 Slide 7 metric examples verified with exact values.
- All 6 Slide 3 Hard Rules fail gracefully when violated with descriptive `RuleViolation` diagnostics.
- Slide 5 benchmark verified with $J = 59.0$ and `is_valid == True`.
