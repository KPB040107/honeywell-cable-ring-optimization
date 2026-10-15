---
phase: 1
plan: 1
wave: 1
gap_closure: false
---

# Plan 1.1: Core Configuration, Data Contracts & Exact Scale Normalization

## Objective
Establish the project packaging structure, system configuration defaults, immutable Pydantic v2 data contracts enforcing metric units and $0.1\text{ mm}$ coordinate precision, and implement the Slide 4 metric scale normalizer (`scale = sqrt(inner.area / area)`) with lower-left coordinate frame alignment, ignoring `net_area`.

## Context
- `v2.md` (Sections 2.1, 3.1, 3.2, 4, 5.1)
- `.gsd/SPEC.md`
- `.gsd/phases/1/RESEARCH.md`

## Tasks

<task type="auto">
  <name>Project Packaging & System Configuration</name>
  <files>
    pyproject.toml
    configs/default_v1.yaml
    src/resplan_router/__init__.py
    src/resplan_router/config.py
  </files>
  <action>
    1. Create `pyproject.toml` specifying Python 3.11+ dependencies: `pydantic>=2.0`, `shapely>=2.0`, `networkx>=3.0`, `numpy>=1.24`, `scipy>=1.10`, `ortools>=9.6`, `matplotlib>=3.7`, `pyyaml>=6.0`, and test dependencies (`pytest>=7.0`).
    2. Create `configs/default_v1.yaml` with official defaults: `max_area_m2: 100.0`, `max_nodes_per_hub: 5`, `wall_clearance_m: 0.5`, `checker_tolerance_m: 0.001`, `min_room_area_m2: 2.0`, and weights `weight_length: 1.0`, `weight_crossing: 5.0`, `weight_overlap: 1.0`, `weight_bend: 0.5`.
    3. Create `src/resplan_router/__init__.py` and `src/resplan_router/config.py` defining `SystemConfig` using Pydantic `BaseModel` with YAML load/dump methods and CLI override support.

    AVOID: Hardcoding magic numbers across modules.
    USE: Centralized `SystemConfig` for tolerances, weights, and clearance thresholds.
  </action>
  <verify>
    python -c "from resplan_router.config import SystemConfig; c = SystemConfig(); assert c.max_nodes_per_hub == 5; assert c.checker_tolerance_m == 0.001"
  </verify>
  <done>
    `SystemConfig` instantiates with verified defaults from `v2.md` Section 2.1.
  </done>
</task>

<task type="auto">
  <name>Pydantic v2 Shared Data Contracts</name>
  <files>
    src/resplan_router/contracts.py
  </files>
  <action>
    1. Define immutable data models in `src/resplan_router/contracts.py` using Pydantic v2:
       - `Point2D`: `x: float`, `y: float` with 4-decimal precision rounding ($0.1\text{ mm}$).
       - `RoomCategory`: Enum with eligible (`living`, `bedroom`, `kitchen`, `storage`) and excluded (`bathroom`, `balcony`, `stair`, `garden`, `pool`, `parking`, `land`, `corridor`).
       - `EligibleOrExcludedRoom`: `room_id: str`, `category: str`, `is_eligible: bool`, `area_m2: float`, `polygon_wkt: str`.
       - `FixedNode`: `node_id: str`, `room_id: str`, `location: Point2D`.
       - `MetricFloorPlan`: `plan_id: str`, `scale_units_per_m: float`, `total_area_m2: float`, `inner_wkt: str`, `free_space_wkt: str`, `obstacles_wkt: str`, `rooms: dict[str, EligibleOrExcludedRoom]`, `nodes: dict[str, FixedNode]`.
    2. Add validation methods ensuring all geometric WKTs are valid Shapely geometries and coordinates are non-negative.

    AVOID: Passing unvalidated raw dictionaries or tuples across phase boundaries.
    USE: Immutable Pydantic models with explicit unit annotations (`_m`, `_m2`).
  </action>
  <verify>
    python -c "from resplan_router.contracts import MetricFloorPlan, Point2D; p = Point2D(x=1.23456, y=7.89012); assert round(p.x, 4) == 1.2346"
  </verify>
  <done>
    Contracts import and validate coordinate precision and required plan attributes.
  </done>
</task>

<task type="auto">
  <name>Slide 4 Exact Scale Converter & Metric Normalizer</name>
  <files>
    src/resplan_router/phase1_ingestion/__init__.py
    src/resplan_router/phase1_ingestion/scale_converter.py
    tests/fixtures/slide4_scale_plan.json
    tests/test_phase1_scale.py
  </files>
  <action>
    1. Create `tests/fixtures/slide4_scale_plan.json` matching Slide 4: $200 \times 180$ drawing units, `inner.area = 36000`, `area = 110.0`, `net_area = 0`.
    2. Implement `src/resplan_router/phase1_ingestion/scale_converter.py`:
       - Function `compute_scale_factor(plan_dict: dict) -> float`: computes `math.sqrt(inner_poly.area / float(plan_dict["area"]))`. Raises ValueError if `area <= 0`. Strictly ignores `net_area`.
       - Function `normalize_to_metric(geom: BaseGeometry, scale: float, origin_offset: tuple[float, float] = (0.0, 0.0)) -> BaseGeometry`: scales geometry by `1.0 / scale` and translates so bounding box $(x_{\min}, y_{\min})$ aligns to lower-left origin $(0, 0)$.
    3. Create `tests/test_phase1_scale.py` validating:
       - Scale factor equals $18.09068 \pm 10^{-4}$ on Slide 4 fixture.
       - Metric width $\approx 11.055\text{ m}$, height $\approx 9.950\text{ m}$.
       - Equivalent $0.5\text{ m}$ in drawing units $\approx 9.045\text{ units}$.
       - Setting `net_area = 0` produces zero error.

    AVOID: Using `plan.get("net_area")` anywhere.
    USE: `plan["area"]` exclusively for scale inversion.
  </action>
  <verify>
    pytest tests/test_phase1_scale.py -v
  </verify>
  <done>
    Scale converter unit tests pass with exact Slide 4 numeric values.
  </done>
</task>

## Must-Haves
- [ ] `scale_converter.py` derives $\text{scale} = \sqrt{\text{inner.area} / \text{area}}$ without touching `net_area`.
- [ ] Coordinates normalized to lower-left origin $(0, 0)$ with $y$ pointing upwards in metres.
- [ ] Pydantic contracts enforce $0.1\text{ mm}$ coordinate precision.

## Success Criteria
- [ ] `pytest tests/test_phase1_scale.py` passes 100%.
- [ ] Slide 4 fixture matches expected numerical outputs.
- [ ] SystemConfig loads default weights ($L=1.0, X=5.0, O=1.0, B=0.5$).
