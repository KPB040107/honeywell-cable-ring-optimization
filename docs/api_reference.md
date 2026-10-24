# Python API Reference Manual

This manual provides exhaustive reference documentation for the public classes, functions, contracts, and configuration options of the **Honeywell Residential Cable Ring Optimization Engine** (`resplan_router`).

---

## Table of Contents

1. [High-Level Public API (`resplan_router`)](#1-high-level-public-api-resplan_router)
   - [`optimize_plan`](#optimize_plan)
   - [`optimize_batch`](#optimize_batch)
   - [`validate_solution`](#validate_solution)
2. [Execution Configurations](#2-execution-configurations)
   - [`OptimizationConfig`](#optimizationconfig)
   - [`SystemConfig`](#systemconfig)
3. [Result Structures & Data Contracts](#3-result-structures--data-contracts)
   - [`OptimizationResult`](#optimizationresult)
   - [`BatchResult`](#batchresult)
   - [`ValidationResult`](#validationresult)
   - [`MetricFloorPlan`](#metricfloorplan)
   - [`EvaluationReport`](#evaluationreport)
4. [Master 6-Phase Pipeline Orchestration](#4-master-6-phase-pipeline-orchestration)
   - [`route_floor_plan`](#route_floor_plan)
5. [Verification Oracle (`resplan_router.phase6_output.checker`)](#5-verification-oracle-resplan_routerphase6_outputchecker)
   - [`FloorPlanChecker`](#floorplanchecker)
6. [Production Optimizers (`resplan_router.optimizers`)](#6-production-optimizers-resplan_routeroptimizers)
   - [`ProductionFallbackOptimizer`](#productionfallbackoptimizer)
   - [`LinUCB_ALNS_V2Optimizer`](#linucb_alns_v2optimizer)
   - [`BitmaskDPOptimizer`](#bitmaskdpoptimizer)
7. [Geometry Healing & Recovery](#7-geometry-healing--recovery)
   - [`recover_and_certify_plan`](#recover_and_certify_plan)

---

## 1. High-Level Public API (`resplan_router`)

The primary public API functions are exported directly from `resplan_router`:

```python
from resplan_router import optimize_plan, optimize_batch, validate_solution
```

### `optimize_plan`

Executes end-to-end hub placement and closed-ring cable optimization for a single architectural floor plan.

```python
def optimize_plan(
    plan: Union[MetricFloorPlan, Dict[str, Any], str, Path, int],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
) -> OptimizationResult:
```

#### Parameters
- **`plan`** (`Union[MetricFloorPlan, dict, str, Path, int]`): Floor plan input. Supports:
  - Path (`str` or `Path`) pointing to a floor plan JSON file.
  - In-memory `dict` matching the ResPlan schematic schema.
  - An integer ID indexing a plan within `ResPlan.pkl`.
  - A pre-parsed `MetricFloorPlan` instance.
- **`config`** (`Optional[Union[OptimizationConfig, SystemConfig, dict]]`): Execution settings, algorithm choice, time budgets, or parameter overrides.

#### Returns
- **`OptimizationResult`**: Complete result containing `is_valid`, `hub_count`, `total_cost_J`, `solution`, `rules_passed`, `violations`, and Slide 8 submission export.

#### Example
```python
from resplan_router import optimize_plan

result = optimize_plan("examples/sample_floor_plan.json")
if result.is_valid:
    print(f"Optimal plan routed with {result.hub_count} hubs and cost J = {result.total_cost_J:.2f}")
    result.save_slide8("output/solution.json")
```

---

### `optimize_batch`

Executes concurrent batch optimization across a collection of floor plans with atomic checkpointing and worker error isolation.

```python
def optimize_batch(
    plans: Sequence[Union[MetricFloorPlan, Dict[str, Any], str, Path, int]],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
    max_workers: int = 4,
    checkpoint_path: Optional[Union[str, Path]] = None,
) -> BatchResult:
```

#### Parameters
- **`plans`** (`Sequence`): Iterable sequence of floor plan inputs (file paths, dictionaries, or integer IDs).
- **`config`** (`Optional[OptimizationConfig]`): Common configuration applied to all plans.
- **`max_workers`** (`int`, default=`4`): Concurrency level for worker thread pool. Set to 1 for deterministic sequential execution.
- **`checkpoint_path`** (`Optional[Union[str, Path]]`): Path to persist intermediate progress JSON for crash recovery.

#### Returns
- **`BatchResult`**: Aggregated container with `results` list, `successful` count, `total_cost_J`, `wall_clock_s`, and statistical summaries.

#### Example
```python
from resplan_router import optimize_batch

plans = ["examples/sample_floor_plan.json", "tests/fixtures/slide5_schematic.json"]
batch = optimize_batch(plans, max_workers=2, checkpoint_path="output/checkpoint.json")

print(f"Batch completed: {batch.successful}/{batch.total_plans} valid.")
```

---

### `validate_solution`

Adversarially validates an existing routing solution against Honeywell's 6 Hard Rules at a strict **1.0 mm ($0.001\text{ m}$) tolerance**.

```python
def validate_solution(
    plan: Union[MetricFloorPlan, Dict[str, Any], str, Path, int],
    solution: Union[SynthesizedRingsBundle, OptimizationResult, Dict[str, Any], str, Path],
    config: Optional[Union[OptimizationConfig, SystemConfig, Dict[str, Any]]] = None,
) -> ValidationResult:
```

#### Parameters
- **`plan`**: Reference floor plan input.
- **`solution`**: The candidate solution (an `OptimizationResult`, `SynthesizedRingsBundle`, Slide 8 JSON dictionary, or file path).
- **`config`**: Optional configuration specifying tolerances and capacity limits.

#### Returns
- **`ValidationResult`**: Detailed verification report detailing pass/fail status for all 6 Hard Rules.

---

## 2. Execution Configurations

### `OptimizationConfig`

Dataclass/Pydantic model governing solver dispatch, iteration limits, timeouts, and fallback modes:

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `algorithm` | `str` | `"production"` | Algorithm engine: `"production"`, `"v2"`, `"v1"`, `"pipeline"`, `"greedy"`. |
| `time_limit_seconds` | `float` | `10.0` | Max wall-clock time budget per floor plan. |
| `random_seed` | `int` | `42` | Random seed for reproducible stochastic search. |
| `max_iterations` | `int` | `10` | Iteration limit for metaheuristics (e.g. ALNS). |
| `fallback_mode` | `str` | `"on_demand"` | Fallback arbitration mode: `"on_demand"`, `"arbitrated"`, `"seeded"`. |
| `strict_clearance` | `bool` | `True` | Validate $\ge 0.50\text{ m}$ node clearance on parse. |
| `export_slide8` | `bool` | `True` | Automatically serialize Slide 8 competition JSON. |
| `system_config` | `SystemConfig` | `SystemConfig()` | Embedded physical tolerances and cost weights. |

### `SystemConfig`

Underlying physical constraints, geometric tolerances, and objective penalties:

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `max_nodes_per_hub` | `int` | `5` | Maximum device IoT nodes per hub zone. |
| `max_area_m2` | `float` | `100.0` | Maximum zone floor area ($m^2$). |
| `wall_clearance_m` | `float` | `0.50` | Minimum hub clearance from walls/windows ($m$). |
| `weight_length` | `float` | `1.0` | Secondary objective weight for cable length ($L$). |
| `weight_bends` | `float` | `0.5` | Penalty per 90° bend ($B$). |
| `weight_crossings` | `float` | `5.0` | Penalty per orthogonal self-crossing ($X$). |
| `weight_overlap` | `float` | `1.0` | Penalty per meter of collinear retrace overlap ($O$). |
| `checker_tolerance_m` | `float` | `0.001` | Geometric verification tolerance ($1\text{ mm}$). |

---

## 3. Result Structures & Data Contracts

### `OptimizationResult`

- `plan_id: str`: Unique identifier.
- `status: SolverStatus`: `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `TIMEOUT`, or `ERROR`.
- `is_valid: bool`: Satisfies 100% of hard competition rules.
- `hub_count: int`: Number of placed hubs ($H$).
- `total_length_m: float`: Total cable length ($L$).
- `total_bends: int`: Total 90° turns ($B$).
- `total_crossings: int`: Total orthogonal crossings ($X$).
- `total_overlap_m: float`: Shared doorway retrace overlap ($O$).
- `total_cost_J: float`: Secondary objective $J = L + 5X + O + 0.5B$.
- `area_gap_m2: float`: $\max(A_k) - \min(A_k)$.
- `rules_passed: dict[str, bool]`: Checklist for Rule 1 through Rule 6.
- `violations: list[str]`: Detailed violation diagnostics if invalid.
- `slide8_dict: dict`: Official Slide 8 competition submission dictionary.
- `save_slide8(path)`: Export Slide 8 JSON to disk.
- `to_dict()`, `to_json()`: Serialization utilities.

### `BatchResult`

- `total_plans: int`: Dispatched plan count.
- `successful: int`: Number of valid solutions.
- `failed: int`: Infeasible or error count.
- `results: list[OptimizationResult]`: Ordered individual results.
- `total_cost_J: float`: Cumulative cost across valid plans.
- `total_runtime_s: float`: Wall-clock batch duration.
- `to_json()`, `to_dict()`: Serialization utilities.

---

## 4. Master 6-Phase Pipeline Orchestration

### `resplan_router.phase6_output.pipeline.route_floor_plan`

The low-level master orchestration function used by CLI and standalone scripts:

```python
def route_floor_plan(
    raw_plan_dict_or_path: Union[dict[str, Any], str, Path],
    config: Optional[SystemConfig] = None,
    output_json_path: Optional[Union[str, Path]] = None,
    render_png_path: Optional[Union[str, Path]] = None,
    strict_clearance: bool = True,
) -> Tuple[dict[str, Any], EvaluationReport, SynthesizedRingsBundle]:
```

---

## 5. Verification Oracle (`resplan_router.phase6_output.checker`)

### `FloorPlanChecker`

Authoritative adversarial validator enforcing the 6 Hard Rules with high-precision GEOS intersection predicates:

```python
report: EvaluationReport = FloorPlanChecker.evaluate(
    plan=metric_plan,
    raw_submission=submission_obj,
    tolerance_m=0.001,
)
```

#### Rule Checklist
1. **Rule 1: Full Coverage:** Every eligible room and device node is covered.
2. **Rule 2: Capacity Limits:** $\le 100\text{ m}^2$ area and $\le 5$ nodes per hub.
3. **Rule 3: Doorway Contiguity:** Hub zone rooms form a connected component in the dual door graph.
4. **Rule 4: Closed Ring:** Non-branching closed loop ($H \to N_1 \to \dots \to N_k \to H$).
5. **Rule 5: Free Space:** Rectilinear segments within `inner`, no wall/window intersections.
6. **Rule 6: Hub Clearance:** $\ge 0.50\text{ m}$ clearance from all wall boundaries.

---

## 6. Production Optimizers (`resplan_router.optimizers`)

- **`ProductionFallbackOptimizer`**: Production-certified hybrid engine combining LinUCB-ALNS v2 with deterministic fallbacks.
- **`LinUCB_ALNS_V2Optimizer`**: 24-dimensional contextual bandit selecting 8 LNS operators.
- **`BitmaskDPOptimizer`**: Exact set partitioning solver guaranteeing optimal hub count $H^*$ in $< 2.5\text{ ms}$.

---

## 7. Geometry Healing & Recovery

### `resplan_router.phase1_ingestion.geometry_healing.recover_and_certify_plan`

Detects and repairs corrupted architectural CAD files:
- Purges outer cadastral `"land"` polygons.
- Bridges thick partition walls ($0.16\text{ m}$) through doorway portals.
- Snaps unclosed polyline endpoints.
- Isolates degree-0 inaccessible rooms.
