---
phase: 3
plan: 1
wave: 1
gap_closure: false
---

# Plan 3.1: Axis-Aligned Hanan Grid Construction & Contracts

## Objective
Define the Phase 3 micro-routing data contracts (`CompassDirection`, `PairRouteMetric`, `RectilinearRoutingIndex`) and implement the obstacle-aware Hanan grid generator (`rectilinear_grid.py`) that constructs a dense axis-aligned routing network strictly inside routable free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$, incorporating fixed node coordinates, portal waypoints, room step points, and bypass offsets.

## Context
- `v2.md` (Sections 2.1, 2.2, 4, 5.3)
- `.gsd/SPEC.md`
- `.gsd/phases/3/RESEARCH.md`
- `src/resplan_router/contracts.py`

## Tasks

<task type="auto">
  <name>Phase 3 Data Contracts</name>
  <files>
    src/resplan_router/contracts.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` to add Phase 3 contracts:
       - `CompassDirection(str, Enum)`: `NORTH = "N"`, `SOUTH = "S"`, `EAST = "E"`, `WEST = "W"`.
         - Helper method `from_delta(dx: float, dy: float) -> CompassDirection`.
         - Helper method `unit_vector() -> tuple[float, float]`.
       - `PairRouteMetric`:
         - `start_id: str` (e.g. node or hub ID).
         - `end_id: str`.
         - `start_dir: Optional[CompassDirection]`.
         - `end_dir: Optional[CompassDirection]`.
         - `length_m: float` (cable length in metres, ge=0.0).
         - `bends: int` (number of 90-degree turns + reversals, ge=0).
         - `cost_J: float` ($J = L + 0.5B$).
         - `polyline: list[Point2D]` (axis-aligned vertices from start to end).
       - `RectilinearRoutingIndex`:
         - `plan_id: str`.
         - `grid_node_count: int`.
         - `grid_edge_count: int`.
         - `pair_metrics: dict[str, PairRouteMetric]` (keyed by composite string `f"{start}_{end}_{dir_in}_{dir_out}"`).
    2. Add validators verifying all polyline segments are strictly horizontal or vertical ($|\Delta x| < 10^{-4}$ or $|\Delta y| < 10^{-4}$).

    AVOID: Using raw dictionaries for routing metric caches.
    USE: Type-safe Pydantic models with geometric validation.
  </action>
  <verify>
    python -c "from resplan_router.contracts import CompassDirection, PairRouteMetric, RectilinearRoutingIndex; print('Contracts syntax valid')"
  </verify>
  <done>
    Phase 3 contracts import and validate coordinate precision and directional enumerations.
  </done>
</task>

<task type="auto">
  <name>Obstacle-Aware Hanan Grid Generator</name>
  <files>
    src/resplan_router/phase3_micro_routing/__init__.py
    src/resplan_router/phase3_micro_routing/rectilinear_grid.py
  </files>
  <action>
    1. Create `src/resplan_router/phase3_micro_routing/__init__.py`.
    2. Implement `src/resplan_router/phase3_micro_routing/rectilinear_grid.py`:
       - Class `HananRoutingGrid`:
         - Extracts $X$ and $Y$ coordinate sets from:
           - All fixed node coordinates in `plan.nodes`.
           - All portal threshold centers, Lane A, Lane B, and step points from `topology.portals`.
           - Offset bypass lines: $\pm 0.04\text{ m}$ around nodes and door centers.
           - Inset contours: room bounding box coordinates inset by $0.15\text{ m}$ where within room interior.
         - Constructs rectilinear grid edges by sorting unique $X$ and $Y$ coordinates with $1\text{ mm}$ deduplication.
         - Filters candidate edges against `plan.free_space_geom`: retains an edge if and only if `LineString([p1, p2]).within(free_space_geom)` (or distance to obstacles $\ge 0.001\text{ m}$).
         - Tags each edge with `room_id` (or `portal_id`).
         - Builds an adjacency graph suitable for state-space pathfinding.
       - Method `build_hanan_grid(plan: MetricFloorPlan, topology: PortalTopologyGraph) -> HananRoutingGrid`.

    AVOID: Generating diagonal edges or edges intersecting walls/windows.
    USE: Strictly horizontal and vertical segments tested against Shapely `free_space_geom`.
  </action>
  <verify>
    python -c "from resplan_router.phase3_micro_routing.rectilinear_grid import HananRoutingGrid; print('rectilinear_grid imported successfully')"
  </verify>
  <done>
    `HananRoutingGrid` constructs valid axis-aligned grid inside free space without obstacle collisions.
  </done>
</task>

<task type="auto">
  <name>Hanan Grid Unit Tests</name>
  <files>
    tests/test_phase3_grid.py
  </files>
  <action>
    1. Create `tests/test_phase3_grid.py` testing:
       - Grid generation on synthetic plans and Slide 5 schematic.
       - Verification that 100% of grid edges are strictly axis-aligned ($|\Delta x| < 10^{-6}$ or $|\Delta y| < 10^{-6}$).
       - Verification that zero grid edges intersect `plan.obstacles_geom` or lie outside `plan.inner_geom`.
       - Verification that every fixed node and portal lane waypoint corresponds to an exact vertex in the grid.
       - Verification that all edges are tagged with valid room or portal IDs.

    AVOID: Shallow edge count checks.
    USE: Exhaustive spatial intersection tests asserting zero obstacle collisions.
  </action>
  <verify>
    pytest tests/test_phase3_grid.py -v
  </verify>
  <done>
    Grid unit tests pass 100% with strict horizontal/vertical alignment and free space containment confirmed.
  </done>
</task>

## Must-Haves
- [ ] 100% of grid edges are axis-aligned ($\Delta x = 0$ or $\Delta y = 0$).
- [ ] 100% of grid edges lie strictly within free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$.
- [ ] All fixed nodes and portal lane points exist as exact grid vertices.

## Success Criteria
- [ ] `pytest tests/test_phase3_grid.py` passes 100%.
- [ ] Zero obstacle crossings across all grid edges.
