# Product Requirements Document (PRD): Joint Optimization of Control-Panel Placement and Closed-Loop Cable Routing (Version 1 Foundation)

**Project Code:** `resplan-cable-opt-v1`

**Target Benchmark:** Honeywell Technologies / UR Hackathon 2026H2 Problem Statement (PS4)

**Primary Dataset:** [ResPlan Dataset on Kaggle](https://www.google.com/search?q=https%3A%2F%2Fwww.kaggle.com%2Fdatasets%2Fresplan%2Fresplan) ([arXiv:2508.14006](https://www.google.com/search?q=https%3A%2F%2Farxiv.org%2Fabs%2F2508.14006))

**Document Scope:** Version 1 (V1) Core Algorithmic & Backend Foundation — Pre-Novelty Baseline

## 1. Executive Summary Sets the V1 Engineering Baseline

Version 1 (V1) establishes a deterministic, mathematically rigorous, and fully tested Python backend and visualization foundation that ingests an unseen residential floor plan from the [ResPlan Dataset](https://www.google.com/search?q=https%3A%2F%2Fwww.kaggle.com%2Fdatasets%2Fresplan%2Fresplan) and jointly solves four coupled problems with zero manual adjustment:

1. **Hub Count & Placement:** Determines the minimum number of control panels (hubs) $K$ and their exact $(x, y)$ coordinates inside eligible rooms.

2. **Contiguous Zone Assignment:** Assigns every eligible room (`living`, `bedroom`, `kitchen`, `storage`) and its single fixed device (node) to a control panel such that each panel's rooms form a single connected territory linked through doorways or open passages.

3. **Closed-Ring Device Sequencing:** Determines the visiting sequence $H_k \to N_{k,1} \to N_{k,2} \to \dots \to N_{k,m} \to H_k$ for each panel's closed, non-branching ring.

4. **Obstacle-Aware Axis-Aligned Physical Routing:** Synthesizes horizontal and vertical (rectilinear/Manhattan) physical cable runs that stay strictly inside the building's interior boundary, cross between rooms exclusively through doors and open passages, avoid walls and windows, separate parallel entry/exit runs in single-door rooms, and minimize total installation cost $J$.

All experimental UI sliders, real-time WebGL/React interactions, and post-V1 algorithmic novelties are intentionally deferred until this 6-phase V1 foundation passes 100% of unit, integration, and regression gates.

## 2. Problem Formulation & Constraint Reconciliation Lock In System Rules

### 2.1 Reconciliation of Problem Statement Variants

Inspection of the project brief and the UR Hackathon 2026H2 slide deck reveals one parameter difference between the high-level project abstract (`IA` slide) and the detailed evaluation slides (`1/8` and `5/8`). V1 resolves this cleanly through explicit configuration profiles so the engine passes both grading rubrics without code changes:

| Constraint Parameter | UR Hackathon 2026H2 Rubric (`strict_v1` Default) | IA Overview Brief (`ia_extended` Profile) | Implementation Strategy in V1 Foundation | 
| ----- | ----- | ----- | ----- | 
| **Eligible Room Categories** | `living`, `bedroom`, `kitchen`, `storage` | `living`, `bedroom`, `kitchen`, `storage` | Fixed taxonomy list in `SystemConfig.eligible_room_types` | 
| **Excluded Room Categories** | `bathroom`, `balcony`, `parking`, `pool`, `stair`, `garden`, `land` | Non-listed room types excluded from node placement | Excluded rooms receive 0 devices; interior corridors/passages permit transit only if connected by doors/openings | 
| **Nodes per Eligible Room** | Exactly 1 fixed node per eligible room | Exactly 1 device per eligible room | Placed deterministically via pole of inaccessibility (`polylabel`) | 
| **Max Eligible Area per Hub (**$A_{\max}$**)** | $\le 100.0\text{ m}^2$ | $\le 100.0\text{ m}^2$ | Hard capacity constraint in Zone MILP solver | 
| **Max Nodes per Hub (**$N_{\max}$**)** | $\le 5\text{ nodes}$ | $\le 20\text{ devices}$ | Configurable `max_nodes_per_hub: int = 5` (override via `--max-nodes 20`) | 
| **Zone Contiguity** | Connected subgraph via doors and open passages | Contiguous room territory | Enforced via Single-Commodity Flow (SCF) constraints on the room-portal graph | 
| **Cable Geometry** | Axis-aligned (horizontal & vertical runs only) | Obstacle-aware physical closed loop | Rectilinear Hanan/grid graph routing with $90^\circ$ bend state tracking | 
| **Primary Ranking Hierarchy** | 1st: Fewest hubs ($K$); 2nd: Lowest cost $J$ | Joint optimization of panel count, length, crossings, overlaps, bends | Hierarchical lexicographic optimization: minimize $K$ first, then minimize $J$ | 

### 2.2 Mathematical Objective Function

For a floor plan with eligible rooms $R_{\text{elig}} = \{r_1, \dots, r_N\}$ having areas $A_i$ and fixed device nodes $N_i$:

1. **Theoretical Lower Bound on Hub Count (**$K_{\min}$**):**
   

   $$
   K_{\min} = \max\left( \left\lceil \frac{\sum_{i=1}^{N} A_i}{A_{\max}} \right\rceil, \; \left\lceil \frac{N}{N_{\max}} \right\rceil, \; C_{\text{comp}} \right)
   $$

   
   where $C_{\text{comp}}$ is the number of disconnected door-reachable components containing eligible rooms.

2. **Secondary Routing Cost Objective (**$J$**):**
   For a given valid hub count $K$, minimize total system installation cost across all $K$ closed rings:
   

   $$
   J = \sum_{k=1}^{K} \left( w_{\text{len}} \cdot L(\mathcal{C}_k) + w_{\text{bend}} \cdot B(\mathcal{C}_k) + w_{\text{ovlp}} \cdot O(\mathcal{C}_k) \right) + w_{\text{cross}} \cdot X(\mathcal{C}_1, \dots, \mathcal{C}_K)
   $$

   
   where:

   * $L(\mathcal{C}_k)$ is the total rectilinear cable length (in meters) of closed loop $\mathcal{C}_k$.

   * $B(\mathcal{C}_k)$ is the total number of $90^\circ$ orthogonal bends along loop $\mathcal{C}_k$.

   * $O(\mathcal{C}_k)$ is the collinear segment overlap length (penalized so parallel runs into single-door rooms maintain a clean separation offset $2\delta$).

   * $X(\mathcal{C}_1, \dots, \mathcal{C}_K)$ is the total number of point intersections (crossings) within or between cable loops.

   * Default weights (configurable in `SystemConfig`): $w_{\text{len}} = 1.0\text{ m}^{-1}$, $w_{\text{bend}} = 0.5\text{ per bend}$, $w_{\text{ovlp}} = 50.0\text{ m}^{-1}$, $w_{\text{cross}} = 25.0\text{ per crossing}$.

## 3. Technology Stack & Repository Architecture Standardize Development

### 3.1 Core Technology Stack

Every library in the V1 stack is selected for deterministic execution, native C/C++ geometric or combinatorial performance, and offline reliability:

| Architectural Layer | Technology / Library | Role in V1 Foundation | 
| ----- | ----- | ----- | 
| **Language & Runtime** | Python 3.11+ | Core execution environment with strict static type annotations (`mypy --strict`) | 
| **Data Contracts & Config** | [Pydantic v2](https://www.google.com/search?q=https%3A%2F%2Fpydantic.dev%2Fdocs%2Fvalidation%2Flatest%2Fget-started%2F) | Immutable data models, input ResPlan JSON validation, configuration management, and scoring JSON serialization | 
| **Computational Geometry** | [Shapely 2.x](https://www.google.com/search?q=https%3A%2F%2Fshapely.readthedocs.io%2Fen%2Fstable%2Freference%2Fshapely.Polygon.html) (GEOS C-backend) | Polygon parsing, boolean set operations (`difference`, `intersection`, `unary_union`), `polylabel` node placement, and `STRtree` spatial indexing | 
| **Graph Topology & Algorithms** | [NetworkX 3.x](https://www.google.com/search?q=https%3A%2F%2Fnetworkx.org%2Fdocumentation%2Fstable%2Freference%2Fclasses%2Fgraph.html) | Room-to-portal macro dual graph $G_{\text{macro}}$, connected component analysis, Single-Commodity Flow topology setup, and state-space rectilinear $A^*$ / Dijkstra | 
| **Numerical & Spatial Arrays** | [NumPy](https://www.google.com/search?q=https%3A%2F%2Fnumpy.org%2Fdoc%2Fstable%2F) & [SciPy Spatial/Optimize](https://www.google.com/search?q=https%3A%2F%2Fdocs.scipy.org%2Fdoc%2Fscipy%2Freference%2Fspatial.html) | Pairwise distance/cost matrix operations, coordinate snapping, and spatial KD-tree lookups | 
| **Combinatorial Optimization (MILP & TSP)** | [Google OR-Tools](https://www.google.com/search?q=https%3A%2F%2Fdevelopers.google.com%2Foptimization%2Fintroduction%2Fpython) (`ortools.sat.python.cp_model` & `ortools.constraint_solver.routing_enums_pb2`) + [PuLP](https://www.google.com/search?q=https%3A%2F%2Fcoin-or.github.io%2Fpulp%2F) fallback | Exact CP-SAT / MILP solver for capacity-constrained contiguous zone partitioning and exact/meta-heuristic TSP solver (plus exact Held-Karp bitmask DP for $N_{\max} \le 12$) | 
| **Floor-Plan Visualization** | [Matplotlib 3.x](https://www.google.com/search?q=https%3A%2F%2Fmatplotlib.org%2Fstable%2Findex.html) (`patches`, `collections`, `FancyArrowPatch`) | Schematic-grade PNG/SVG floor-plan overlay rendering rooms, doors, hubs, nodes, numbered rectilinear cable loops, per-hub summary table, and reviewer checklist | 
| **Testing & Quality Assurance** | [pytest](https://www.google.com/search?q=https%3A%2F%2Fdocs.pytest.org%2Fen%2Fstable%2Fgetting-started.html) | Automated unit tests, geometric invariant checks, cross-phase integration tests, and full regression suites | 

### 3.2 Modular Directory Layout

```
resplan_cable_router/
├── pyproject.toml
├── README.md
├── configs/
│   ├── strict_v1.yaml              # Default: max_area=100.0, max_nodes=5
│   └── ia_extended.yaml            # Extended: max_area=100.0, max_nodes=20
├── src/
│   └── resplan_router/
│       ├── __init__.py
│       ├── config.py               # SystemConfig & optimization weight definitions
│       ├── contracts.py            # Shared Pydantic schemas connecting Phases 1–6
│       ├── phase1_ingestion/
│       │   ├── parser.py           # ResPlan JSON/WKT loader & MultiPolygon splitter
│       │   ├── scale_normalizer.py # Metric area calibration against GFA metadata
│       │   └── node_placer.py      # Deterministic polylabel device placement
│       ├── phase2_topology/
│       │   ├── portal_extractor.py # Door & open-passage geometric detection
│       │   └── macro_graph.py      # Dual graph G_macro & dual-lane portal waypoints
│       ├── phase3_micro_routing/
│       │   ├── rectilinear_grid.py # Obstacle-buffered Hanan + uniform grid builder
│       │   ├── astar_rectilinear.py# Bend-penalized state-space Dijkstra / A*
│       │   └── cost_matrix.py      # All-pairs topological rectilinear cost tensor
│       ├── phase4_partitioning/
│       │   ├── hub_candidates.py   # Valid hub placement candidate generator per room
│       │   ├── zone_milp.py        # CP-SAT / MILP contiguous zone + hub solver
│       │   └── ccvt_fallback.py    # Capacitated region-growing fallback for large plans
│       ├── phase5_ring_sequencing/
│       │   ├── tsp_solver.py       # Exact Held-Karp DP (N<=12) & OR-Tools TSP (N<=20)
│       │   ├── loop_synthesizer.py # Full closed-ring polyline assembly
│       │   └── lane_nudger.py      # Parallel-run offset separation & corner de-crossing
│       ├── phase6_output/
│       │   ├── validator.py        # 8-rule programmatic design auditing engine
│       │   ├── json_exporter.py    # Scoring & validation JSON serializer
│       │   └── visualizer.py       # Schematic overlay renderer (matches Slide 5/8)
│       └── cli.py                  # End-to-end command-line runner
└── tests/
    ├── fixtures/
    │   ├── schematic_6room.json    # Synthetic 6-room benchmark from Slide 5/8 (118 m2)
    │   └── sample_resplan_01.json  # Real ResPlan layout fixture
    ├── test_phase1_ingestion.py
    ├── test_phase2_topology.py
    ├── test_phase3_micro_routing.py
    ├── test_phase4_partitioning.py
    ├── test_phase5_ring_sequencing.py
    ├── test_phase6_output.py
    └── test_integration_pipeline.py

```

## 4. Shared Data Contracts Enforce Seamless Handshakes Across Phases

All 6 phases communicate exclusively through strongly typed, immutable [Pydantic v2](https://www.google.com/search?q=https%3A%2F%2Fpydantic.dev%2Fdocs%2Fvalidation%2Flatest%2Fget-started%2F) models defined in `src/resplan_router/contracts.py`. No phase is permitted to mutate raw dictionaries or pass untyped tuples across module boundaries:

| Contract Model | Produced By | Consumed By | Key Validated Fields | 
| ----- | ----- | ----- | ----- | 
| `FloorPlanModel` | Phase 1 | Phases 2, 3, 4, 5, 6 | `plan_id`, `scale_factor`, `inner_polygon_wkt`, `obstacle_union_wkt`, `rooms: list[RoomEntity]`, `doors: list[DoorEntity]`, `nodes: list[DeviceNode]` | 
| `MacroTopologyModel` | Phase 2 | Phases 3, 4, 5 | `room_ids: list[str]`, `portals: list[PortalEdge]`, `adjacency_list: dict[str, list[str]]`, `connected_components: list[list[str]]` | 
| `MicroRoutingIndex` | Phase 3 | Phases 4, 5 | `waypoints: dict[str, Point2D]`, `distance_matrix: dict[tuple[str, str], float]`, `bend_matrix: dict[tuple[str, str], int]`, `path_cache: dict[tuple[str, str], list[Point2D]]` | 
| `ZonePartitionResult` | Phase 4 | Phases 5, 6 | `hub_count: int`, `lower_bound_hubs: int`, `zones: list[ZoneAssignment]` (each with `hub_id`, `hub_coord`, `room_ids`, `node_ids`, `total_area_m2`) | 
| `RingRoutingResult` | Phase 5 | Phase 6 | `rings: list[ClosedRingRoute]` (each with `hub_id`, `ordered_node_ids`, `segments: list[RouteSegment]`, `total_length_m`, `total_bends`, `crossings_count`), `total_cost_j: float` | 
| `ValidatedSolutionBundle` | Phase 6 | CLI / Evaluator | `is_valid: bool`, `rule_checks: dict[str, bool]`, `violations: list[str]`, `scoring_json: dict`, `overlay_image_path: str` | 

## 5. Six-Phase Engineering Plan Details Build, Test, Integration, and Review Gates

### Phase 1: ResPlan Geometry Ingestion, Scale Normalization & Fixed Node Placement

#### 5.1.1 Phase 1 Goal

Transform a raw [ResPlan JSON file](https://www.google.com/search?q=https%3A%2F%2Farxiv.org%2Fhtml%2F2508.14006v1) into a geometrically cleaned, metric-calibrated `FloorPlanModel` where every individual room is an indexed polygon, impassable obstacles (`wall`, `window`, `neighbor`, `front_door`, exterior) are unified into a spatial obstacle mask, and every eligible room (`living`, `bedroom`, `kitchen`, `storage`) is assigned exactly one valid, wall-cleared device node $N_i$.

#### 5.1.2 What & How to Build in Phase 1

1. **`parser.py` (Polygon Extraction & Indexing):**

   * Load the ResPlan JSON dictionary containing semantic keys (`living`, `bedroom`, `kitchen`, `storage`, `bathroom`, `balcony`, `wall`, `door`, `window`, `front_door`, `inner`, `land`, `wall_depth`, `area`, `id`).

   * Parse WKT or coordinate-list representations into [Shapely](https://www.google.com/search?q=https%3A%2F%2Fshapely.readthedocs.io%2Fen%2Fstable%2Freference%2Fshapely.Polygon.html) `Polygon` and `MultiPolygon` objects.

   * Apply `shapely.make_valid()` and `.buffer(0)` to heal any self-intersecting vertices or zero-width spikes.

   * Decompose each `MultiPolygon` in room categories into individual `Polygon` instances, sorted deterministically by $(x_{\min}, y_{\min})$ and assigned stable identifiers (`bedroom_0`, `bedroom_1`, `living_0`, `kitchen_0`, `storage_0`, `bathroom_0`).

   * Filter out sliver artifacts with area $< 0.5\text{ m}^2$.

2. **`scale_normalizer.py` (Metric Unit Calibration):**

   * Compute the sum of raw polygon areas inside `inner` (or across all interior rooms and walls) and compare against the JSON metadata `area` (Gross Floor Area in $\text{m}^2$) and `wall_depth` (standard $0.15\text{ m}$).

   * If the input coordinates are in pixels or unscaled CAD units rather than meters, compute the linear scaling factor $s = \sqrt{A_{\text{metadata}} / A_{\text{raw}}}$ (or $0.15 / w_{\text{raw}}$) and apply `shapely.affinity.scale(geom, xfact=s, yfact=s, origin=(0, 0))` across all geometries so all downstream phases operate strictly in meters ($ext{m}$) and square meters ($ext{m}^2$).

3. **`node_placer.py` (Deterministic Interior Device Placement):**

   * Identify all rooms where `category in {"living", "bedroom", "kitchen", "storage"}`.

   * For each eligible room polygon $P_i$:

     * Compute the eroded interior safe polygon $P_i^{\text{safe}} = P_i.\text{buffer}(-d_{\text{clear}})$ with $d_{\text{clear}} = 0.25\text{ m}$ so nodes never sit directly on a wall or doorway threshold. (If $P_i$ is very narrow and $P_i^{\text{safe}}$ is empty, fall back to $P_i$).

     * If $P_i^{\text{safe}}.\text{contains}(P_i.\text{centroid})$, place node $N_i$ at $P_i.\text{centroid}$.

     * Otherwise (e.g., L-shaped, U-shaped, or non-convex rooms where the centroid falls inside a wall or another room), compute the **Pole of Inaccessibility** via `shapely.ops.polylabel(P_i_safe, tolerance=0.01)` to guarantee that $N_i$ lies at the deepest interior point of the room.

     * Snap $N_i$ coordinates to a $0.05\text{ m}$ ($5\text{ cm}$) rectilinear grid while verifying $P_i.\text{contains}(N_i)$ remains `True`.

#### 5.1.3 Phase 1 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase1_ingestion.py` to enforce the following automated assertions before completing Phase 1:

* **Test 1.1 (`test_multipolygon_decomposition`):** On the 6-room schematic fixture (3 bedrooms, 1 living, 1 kitchen, 1 storage, 1 bathroom), verify exactly 7 `RoomEntity` records are created (`6` eligible, `1` excluded).

* **Test 1.2 (`test_eligible_vs_excluded_filtering`):** Verify `len(model.nodes) == 6` and zero nodes are placed in `bathroom`, `balcony`, `stair`, or `garden`.

* **Test 1.3 (`test_l_shaped_nonconvex_room_node_containment`):** Pass a synthetic L-shaped polygon whose geometric centroid lies outside the polygon; assert `room.polygon.contains(node.point) is True` and `node.point.distance(room.polygon.boundary) >= 0.20`.

* **Test 1.4 (`test_obstacle_mask_integrity`):** Assert that no placed `DeviceNode` intersects `obstacle_union` (`wall` $\cup$ `window` $\cup$ `front_door`) and every `DeviceNode` is strictly inside `inner_polygon`.

#### 5.1.4 Phase 1 Cross-Check & Review Gate

* **Foundational Contract Check:** Validate that `FloorPlanModel.model_dump_json()` serializes and deserializes cleanly with zero data loss, establishing the immutable input baseline for Phase 2.

* **Phase 1 Code & Edge-Case Review:** Verify handling of empty ResPlan categories (`MULTIPOLYGON EMPTY`), missing `inner` polygon fallback (reconstructed via `unary_union` of rooms + interior doors), and floating-point precision tolerance ($10^{-6}\text{ m}$).

### Phase 2: Topological Dual-Graph Construction & Door/Passage Portal Extraction

#### 5.2.1 Phase 2 Goal

Build the macro-level topological dual graph $G_{\text{macro}} = (V_R, E_P)$ in [NetworkX](https://www.google.com/search?q=https%3A%2F%2Fnetworkx.org%2Fdocumentation%2Fstable%2Freference%2Fclasses%2Fgraph.html) where vertices $V_R$ represent interior rooms and edges $E_P$ represent valid passable portals (`door` polygons or open interior passages), and precompute dual-lane portal waypoints so single-door rooms can accept both an incoming and outgoing cable run without collinear overlap.

#### 5.2.2 What & How to Build in Phase 2

1. **`portal_extractor.py` (Door & Open-Passage Detection):**

   * **Door Portals:** For each interior `door` polygon $D_m$ (excluding `front_door` and any exterior door touching the exterior boundary of `inner`):

     * Buffer $D_m$ by $\epsilon = 0.05\text{ m}$ (`D_m.buffer(0.05)`) and query the room polygons in `FloorPlanModel.rooms` using a [Shapely STRtree](https://www.google.com/search?q=https%3A%2F%2Fshapely.readthedocs.io%2Fen%2Fstable%2Fstrtree.html).

     * Identify the pair of distinct rooms $(r_i, r_j)$ that intersect $D_m.\text{buffer}(0.05)$ along a shared boundary segment of length $\ge 0.40\text{ m}$.

   * **Open-Passage (`arch`) Portals:** For every pair of adjacent interior rooms $(r_i, r_j)$ whose polygons touch or lie within $\epsilon = 0.05\text{ m}$:

     * Compute the shared interface geometry $I_{ij} = r_i.\text{buffer}(0.05) \cap r_j.\text{buffer}(0.05)$.

     * Subtract the obstacle mask (`wall` $\cup$ `window` $\cup$ `front_door`): $P_{ij} = I_{ij} \setminus \text{obstacle\_union}$.

     * If $P_{ij}$ contains a continuous opening of width $\ge 0.60\text{ m}$ that is not already covered by a `door` polygon, register $P_{ij}$ as an `open_passage` portal between $r_i$ and $r_j$.

2. **`macro_graph.py` (Dual Graph & Dual-Lane Waypoint Generation):**

   * Construct an undirected [NetworkX Graph](https://www.google.com/search?q=https%3A%2F%2Fnetworkx.org%2Fdocumentation%2Fstable%2Freference%2Fclasses%2Fgraph.html) $G_{\text{macro}}$ with node attributes (`category`, `area_m2`, `is_eligible`, `device_node_id`) and edge attributes (`portal_id`, `portal_type`, `width_m`, `center_point`, `lane_a_point`, `lane_b_point`, `orientation`).

   * **Dual-Lane Portal Waypoints:** For every portal of width $w$ with axis-aligned opening vector $\hat{t}$ (parallel to the doorway threshold) and normal vector $\hat{n}$ (pointing from room $r_i$ into room $r_j$):

     * Compute the center waypoint $p_0$ at the midpoint of the doorway threshold.

     * Compute two parallel lane waypoints $p_A = p_0 - \delta \hat{t}$ and $p_B = p_0 + \delta \hat{t}$ (with $\delta = \min(0.15\text{ m}, w / 4)$), together with room-side entry/exit clearance points $p_A^{\pm} = p_A \pm d_{\text{step}} \hat{n}$ inside $r_i$ and $r_j$. This guarantees that when a cable enters and exits a single-door leaf room (as in `Bedroom 2` or `Storage` on Slide 5/8), the two parallel runs use Lane A and Lane B without touching or crossing.

#### 5.2.3 Phase 2 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase2_topology.py` to verify:

* **Test 2.1 (`test_door_portal_adjacency`):** On the 6-room schematic fixture, assert that $G_{\text{macro}}$ detects the exact door edges (`Living`–`Bedroom 3`, `Bedroom 3`–`Bedroom 1`, `Living`–`Bedroom 1`, `Bedroom 2`–`Kitchen`, `Kitchen`–`Storage`) and does **not** create an edge across solid walls (e.g., no edge between `Living` and `Storage` or `Bedroom 1` and `Bedroom 2`).

* **Test 2.2 (`test_front_door_and_window_exclusion`):** Assert that `front_door` (`Entrance`) and `window` polygons generate zero edges in $G_{\text{macro}}$.

* **Test 2.3 (`test_dual_lane_waypoints_inside_free_space`):** Assert that for every `PortalEdge`, `lane_a_point`, `lane_b_point`, and their room-entry clearance points do not intersect `wall` or `window` polygons and have Euclidean separation $\ge 0.15\text{ m}$.

#### 5.2.4 Cross-Phase Integration Check (Phase 2 $\leftrightarrow$ Phase 1) & Review Gate

* **Cross-Check with Phase 1:** Verify that `set(G_macro.nodes) == {r.room_id for r in floor_plan.rooms}`, that every eligible room with a `DeviceNode` in Phase 1 has degree $\ge 1$ in $G_{\text{macro}}$ (or is flagged immediately as an isolated architectural anomaly), and that every portal polygon lies strictly inside `floor_plan.inner_polygon`.

* **Phase 1 + 2 Regression Suite:** Re-run `pytest tests/test_phase1_ingestion.py tests/test_phase2_topology.py` to confirm zero regressions.

### Phase 3: Intra-Room Rectilinear (Manhattan) Obstacle-Free Pathfinding & Pairwise Cost Matrix

#### 5.3.1 Phase 3 Goal

Build the micro-level obstacle-aware axis-aligned router that finds valid horizontal/vertical cable paths between any combination of device nodes, portal waypoints, and candidate hub positions while avoiding all walls, windows, and exterior spaces, and precompute the all-pairs topological rectilinear cost matrix $D_{\text{rect}}$.

#### 5.3.2 What & How to Build in Phase 3

1. **`rectilinear_grid.py` (Constrained Hanan + Clearance Grid Construction):**

   * Define the routable free space polygon $\mathcal{F}$:
     

     $$
     \mathcal{F} = \left( \bigcup_{r \in R_{\text{interior}}} P_r \cup \bigcup_{p \in E_P} P_{\text{portal}, p} \right) \setminus \left( (\text{wall} \cup \text{window} \cup \text{front\_door} \cup \text{neighbor}).\text{buffer}(d_{\text{wall}}) \right)
     $$

     
     where $d_{\text{wall}} = 0.08\text{ m}$ enforces visual and physical clearance from walls and windows, with local unbuffering at portal lanes so cables pass cleanly through narrow doorways.

   * Construct a **Hybrid Hanan + Uniform Rectilinear Graph** $G_{\text{rect}} = (V_G, E_G)$:

     * Collect exact $X$ and $Y$ coordinates of all Phase 1 `DeviceNode` positions, all Phase 2 portal lane waypoints ($p_A^{\pm}, p_B^{\pm}$), all Phase 4 candidate hub points, and the bounding box / interior wall-hugging offset lines ($0.25\text{ m}$ inset from interior room walls), augmented with a uniform $0.25\text{ m}$ background grid.

     * Keep only vertices $(x, y) \in \mathcal{F}$ and axis-aligned horizontal/vertical edges $e = ((x_1, y_1), (x_2, y_2))$ whose entire `LineString` is contained inside $\mathcal{F}$ (`F.contains(edge_line)`).

     * Tag every edge with the `room_id` or `portal_id` it traverses so room-restricted routing (restricting a hub's cable to its assigned zone $Z_k$) can filter $G_{\text{rect}}$ in $O(1)$ per edge.

2. **`astar_rectilinear.py` (Bend-Penalized State-Space Dijkstra /** $A^*$**):**

   * Model each search state as $(v, d)$ where $v \in V_G$ and $d \in \{\text{N}, \text{S}, \text{E}, \text{W}, \text{START}\}$ is the arrival direction.

   * Transitioning from $(u, d_1)$ to neighbor $(v, d_2)$ along axis-aligned segment $(u, v)$ incurs step cost:
     

     $$
     c((u, d_1) \to (v, d_2)) = w_{\text{len}} \cdot \Vert{}u - v\Vert{}_1 + w_{\text{bend}} \cdot \mathbb{I}(d_1 \neq \text{START} \land d_1 \neq d_2) + \text{penalty}_{\text{used}}(u, v)
     $$

   * Use Manhattan heuristic $h(v, t) = w_{\text{len}} \cdot (\vert{}v_x - t_x\vert{} + \vert{}v_y - t_y\vert{}) + w_{\text{bend}} \cdot \mathbb{I}(v_x \neq t_x \land v_y \neq t_y)$ for admissible, optimal $A^*$ search.

   * Compress consecutive collinear grid segments into minimal-vertex rectilinear polylines.

3. **`cost_matrix.py` (All-Pairs Topological Rectilinear Cost Tensor):**

   * Compute and cache the shortest obstacle-free rectilinear path, metric length $L(u, v)$, bend count $B(u, v)$, and combined cost $C(u, v)$ between all pairs of eligible nodes and room hub anchors.

#### 5.3.3 Phase 3 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase3_micro_routing.py` to verify:

* **Test 3.1 (`test_strict_axis_alignment`):** For every computed path segment $((x_1, y_1), (x_2, y_2))$, assert $\min(\vert{}x_1 - x_2\vert{}, \vert{}y_1 - y_2\vert{}) < 10^{-9}$ (zero diagonal segments).

* **Test 3.2 (`test_zero_wall_or_window_intersection`):** Assert `LineString(path).intersects(floor_plan.obstacle_union) is False` and `floor_plan.inner_polygon.contains(LineString(path)) is True` across all pairs of nodes.

* **Test 3.3 (`test_bend_count_accuracy`):** On an L-shaped path with 1 turn and a U-shaped path with 2 turns, assert the bend counter returns `1` and `2` respectively and that adding a higher $w_{\text{bend}}$ selects the fewer-bend route when lengths are equal.

* **Test 3.4 (`test_zone_restricted_routing`):** When routing is restricted to a subset of rooms $Z_k$, assert that the router only traverses rooms in $Z_k$ and portals connecting rooms in $Z_k$, raising `NoZoneRouteFound` if $Z_k$ is disconnected.

#### 5.3.4 Cross-Phase Integration Check (Phase 3 $\leftrightarrow$ Phases 1 & 2) & Review Gate

* **Cross-Check with Phases 1 & 2:** Verify that two eligible nodes $(N_i, N_j)$ from Phase 1 have a finite rectilinear path in Phase 3 **if and only if** their host rooms $(r_i, r_j)$ belong to the same connected component of $G_{\text{macro}}$ in Phase 2, and that every room-to-room transition along the Phase 3 polyline intersects a valid `PortalEdge` polygon from Phase 2.

* **Phase 1–3 Regression Suite:** Run `pytest tests/test_phase1_ingestion.py tests/test_phase2_topology.py tests/test_phase3_micro_routing.py`.

### Phase 4: Capacity-Constrained Contiguous Zone Partitioning & Hub Placement

#### 5.4.1 Phase 4 Goal

Jointly determine the minimum feasible number of hubs $K$, assign every eligible room to a contiguous zone $Z_k$ respecting area ($\le 100\text{ m}^2$) and node count ($\le N_{\max}$) limits, and select the optimal physical hub coordinate $H_k$ inside each zone.

#### 5.4.2 What & How to Build in Phase 4

1. **`hub_candidates.py` (Room Hub Candidate Generation):**

   * For each eligible room $r_i \in R_{\text{elig}}$, generate a discrete set of candidate hub positions $\mathcal{H}_i$ inside $P_i \cap \mathcal{F}$ (specifically: the room's wall-inset midpoints and an offset point $0.40\text{ m}$ from $N_i$ along the room's principal axis, ensuring $H_k \neq N_i$ so hub and node markers never overlap visually or geometrically).

2. **`zone_milp.py` (Exact Contiguous Zone & Hub Placement MILP / CP-SAT Solver):**

   * Start at $K = K_{\min} = \max(\lceil \sum_{i \in R_{\text{elig}}} A_i / 100.0 \rceil, \lceil \vert{}R_{\text{elig}}\vert{} / N_{\max} \rceil)$.

   * Formulate a Mixed-Integer Program in [Google OR-Tools CP-SAT](https://www.google.com/search?q=https%3A%2F%2Fdevelopers.google.com%2Foptimization%2Fcp%2Fcp_solver) (with [PuLP](https://www.google.com/search?q=https%3A%2F%2Fcoin-or.github.io%2Fpulp%2F) CBC fallback):

     * **Decision Variables:**

       * $y_r \in \{0, 1\}$ for $r \in R_{\text{elig}}$: `1` if room $r$ is selected as the root/hub room of a zone ($\sum_{r \in R_{\text{elig}}} y_r = K$).

       * $x_{i, r} \in \{0, 1\}$ for $i \in V_R, r \in R_{\text{elig}}$: `1` if room $i$ is assigned to the zone rooted at $r$. (Note: non-eligible transit rooms such as interior hallways may have $x_{i,r} = 1$ with $0$ area/node load if needed to bridge two eligible rooms through doors, or zones can be restricted strictly to $R_{\text{elig}}$ when all eligible rooms connect directly).

       * $f_{u, v, r} \ge 0$ for directed portal edges $(u, v) \in E_P$: Single-Commodity Flow (SCF) variable carrying flow from root room $r$ to all rooms $i$ assigned to $r$.

     * **Hard Constraints:**

       1. *Exact Assignment of Eligible Rooms:* $\sum_{r \in R_{\text{elig}}} x_{i, r} = 1 \quad \forall i \in R_{\text{elig}}$, and $x_{r, r} = y_r$.

       2. *Activation Bound:* $x_{i, r} \le y_r \quad \forall i \in V_R, r \in R_{\text{elig}}$.

       3. *Area Capacity (*$\le 100\text{ m}^2$*):* $\sum_{i \in R_{\text{elig}}} A_i \cdot x_{i, r} \le 100.0 \cdot y_r \quad \forall r \in R_{\text{elig}}$.

       4. *Node Count Capacity (*$\le N_{\max}$*):* $\sum_{i \in R_{\text{elig}}} x_{i, r} \le N_{\max} \cdot y_r \quad \forall r \in R_{\text{elig}}$.

       5. *Single-Commodity Flow Contiguity:* For each root $r$ and every room $i \neq r$, net inflow equals $x_{i, r}$:
          

          $$
          \sum_{u : (u, i) \in E_P} f_{u, i, r} - \sum_{v : (i, v) \in E_P} f_{i, v, r} = x_{i, r}
          $$

          
          and flow is only permitted on edges where both endpoints belong to zone $r$:
          

          $$
          f_{u, v, r} \le \vert{}V_R\vert{} \cdot x_{u, r}, \quad f_{u, v, r} \le \vert{}V_R\vert{} \cdot x_{v, r}
          $$

          
          This mathematically guarantees that every zone $Z_r = \{i \mid x_{i, r} = 1\}$ forms a single connected subgraph of $G_{\text{macro}}$.

     * **Objective Function:** Minimize the total topological star/MST proxy routing cost $\sum_{r \in R_{\text{elig}}} \sum_{i \in R_{\text{elig}}} D_{\text{rect}}(N_i, H_r) \cdot x_{i, r}$.

     * If CP-SAT proves $K$ infeasible (e.g., due to bottleneck door topology), increment $K \leftarrow K + 1$ and re-solve.

   * **Exact Hub Coordinate Refinement:** Once each zone $Z_k$ is fixed, evaluate all candidate hub coordinates $h \in \bigcup_{r \in Z_k} \mathcal{H}_r$ inside $Z_k$ and select the point $H_k$ that minimizes the exact closed-loop TSP cost over $Z_k$.

3. **`ccvt_fallback.py` (Capacitated Region-Growing Heuristic Fallback):**

   * For unusually large multi-wing layouts where CP-SAT exceeds a $3.0\text{ s}$ timeout, implement a deterministic Capacitated Centroid Voronoi Tessellation (CCVT) region-growing solver with boundary-swap local search on $G_{\text{macro}}$.

#### 5.4.3 Phase 4 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase4_partitioning.py` to verify:

* **Test 4.1 (`test_schematic_6room_partition`):** On the 6-room schematic fixture ($118\text{ m}^2$ total eligible area, 6 nodes: Living $40\text{ m}^2$, Bedroom 1 $16\text{ m}^2$, Bedroom 2 $16\text{ m}^2$, Bedroom 3 $15\text{ m}^2$, Kitchen $16\text{ m}^2$, Storage $15\text{ m}^2$), assert:

  * $K_{\min} = 2$ and `result.hub_count == 2`.

  * Zone 1 = `{Living, Bedroom 1, Bedroom 3}` ($71\text{ m}^2$, 3 nodes) and Zone 2 = `{Bedroom 2, Kitchen, Storage}` ($47\text{ m}^2$, 3 nodes).

* **Test 4.2 (`test_capacity_and_disjointness_invariants`):** Assert $\sum_{k} \vert{}Z_k \cap R_{\text{elig}}\vert{} == \vert{}R_{\text{elig}}\vert{}$, $Z_j \cap Z_k = \emptyset$ for $j \neq k$, $\text{area}(Z_k) \le 100.0\text{ m}^2$, and $\text{nodes}(Z_k) \le N_{\max}$.

* **Test 4.3 (`test_zone_graph_contiguity`):** For every zone $Z_k$, assert `nx.is_connected(G_macro.subgraph(Z_k.room_ids)) is True`.

* **Test 4.4 (`test_cut_vertex_infeasibility_escalation`):** Construct a synthetic floor plan where $K_{\min} = 2$ by area ($90\text{ m}^2 + 90\text{ m}^2$), but a central room separates three leaf wings such that no 2-way contiguous split satisfies $\le 100\text{ m}^2$; assert the solver automatically escalates from $K=2$ to $K=3$ and returns a valid 3-hub partition.

#### 5.4.4 Cross-Phase Integration Check (Phase 4 $\leftrightarrow$ Phases 1, 2, 3) & Review Gate

* **Cross-Check with Phases 1–3:** Confirm that every `ZoneAssignment.hub_coord` is a valid grid node in Phase 3's `MicroRoutingIndex`, lies strictly inside its host room polygon from Phase 1 (`room.polygon.contains(Point(hub_coord))`), has finite intra-zone rectilinear paths to all assigned `node_ids` using only Phase 2 portals inside $Z_k$, and never coincides with a `DeviceNode` coordinate.

* **Phase 1–4 Regression Suite:** Run `pytest tests/test_phase1_ingestion.py tests/test_phase2_topology.py tests/test_phase3_micro_routing.py tests/test_phase4_partitioning.py`.

### Phase 5: Closed-Loop Device Sequencing (TSP) & Physical Ring Cable Synthesis

#### 5.5.1 Phase 5 Goal

For each zone $Z_k$, determine the optimal closed-loop visiting sequence $H_k \to N_{k,\sigma(1)} \to \dots \to N_{k,\sigma(m)} \to H_k$ and synthesize the complete, non-branching, obstacle-free, axis-aligned physical cable polyline with separated parallel runs through shared doorways so single-door rooms have zero self-overlap or crossings.

#### 5.5.2 What & How to Build in Phase 5

1. **`tsp_solver.py` (Exact Held-Karp DP & OR-Tools Routing Solver):**

   * Given zone $Z_k$ with hub $H_k$ (index $0$) and $m = \vert{}N(Z_k)\vert{}$ assigned nodes (indices $1, \dots, m$):

   * Restrict the Phase 3 rectilinear graph $G_{\text{rect}}$ to rooms and portals belonging to $Z_k$ (`G_rect_zone`) so a cable loop for hub $H_k$ can **never** shortcut through another hub's zone or through an excluded bathroom.

   * Compute the $(m+1) \times (m+1)$ intra-zone cost matrix $M_{ij} = w_{\text{len}} L_{Z_k}(i, j) + w_{\text{bend}} B_{Z_k}(i, j)$.

   * **If** $m == 1$**:** The sequence is trivially $H_k \to N_{k,1} \to H_k$, routed using two distinct lane-offset paths so the outgoing leg $H_k \to N_{k,1}$ and return leg $N_{k,1} \to H_k$ form a clean rectangular loop with zero collinear overlap.

   * **If** $2 \le m \le 12$ **(includes default** $N_{\max} = 5$**):** Solve the Travelling Salesperson Problem **to global optimality** via exact Held-Karp bitmask dynamic programming in $O(m^2 2^m)$ time ($< 1\text{ ms}$ for $m=5$, $< 15\text{ ms}$ for $m=12$). Also evaluate both tour orientations (clockwise vs. counter-clockwise) during physical synthesis to pick the orientation with fewer bends and crossings.

   * **If** $13 \le m \le 20$ **(`ia_extended` profile):** Solve via [Google OR-Tools RoutingModel](https://www.google.com/search?q=https%3A%2F%2Fdevelopers.google.com%2Foptimization%2Frouting%2Fvrp) with Guided Local Search (`GUIDED_LOCAL_SEARCH`) and Christofides/2-opt initialization (time limit $0.5\text{ s}$ per zone).

2. **`loop_synthesizer.py` & `lane_nudger.py` (Sequential Path Synthesis & Parallel-Lane Separation):**

   * Route each leg $e_s = (v_{s}, v_{s+1})$ sequentially for $s = 0, \dots, m$ (where $v_0 = v_{m+1} = H_k$) over `G_rect_zone`:

     * **Single-Door Room Spur Handling (Slide 5/8 Requirement):** When visiting a degree-1 leaf room (such as `Bedroom 2` or `Storage` on Slide 5/8), force the entry leg to pass through Portal **Lane A** ($p_A$) and the exit leg to pass through Portal **Lane B** ($p_B$), chosen according to the relative clockwise/counter-clockwise angle of the previous and next targets so the two parallel runs enter and exit cleanly without crossing each other inside the room or doorway!

     * **Dynamic Soft-Obstacle Penalization Across Legs:** After routing leg $s$, add a soft edge/vertex usage penalty ($w_{\text{ovlp}}$ for shared segments, $w_{\text{cross}}$ for orthogonal crossings) to `G_rect_zone` before routing leg $s+1$.

     * **Joint Hub Co-Optimization Pass:** Perform a final pass over the top-3 candidate hub positions $H_k \in Z_k$ using the exact synthesized loop cost $J_k = w_{\text{len}} L_k + w_{\text{bend}} B_k + w_{\text{ovlp}} O_k + w_{\text{cross}} X_k$ and keep the minimum-cost ring.

#### 5.5.3 Phase 5 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase5_ring_sequencing.py` to verify:

* **Test 5.1 (`test_closed_loop_topology`):** For every generated `ClosedRingRoute`, assert `ring.segments[0].start == ring.hub_coord`, `ring.segments[-1].end == ring.hub_coord`, `ring.segments[s].end == ring.segments[s+1].start` for all $s$, and `len(ring.segments) == len(ring.ordered_node_ids) + 1`.

* **Test 5.2 (`test_exact_node_visitation_once`):** Assert that `set(ring.ordered_node_ids) == set(zone.node_ids)`, `len(ring.ordered_node_ids) == len(zone.node_ids)`, and no unassigned node is touched.

* **Test 5.3 (`test_single_door_room_parallel_runs_zero_crossing`):** On a single-door leaf room (e.g., `Bedroom 2` and `Storage` in the 6-room schematic), assert that the incoming segment $s_{\text{in}}$ and outgoing segment $s_{\text{out}}$ have zero collinear overlap (`s_in.intersection(s_out).length == 0`) and zero point crossings (`s_in.crosses(s_out) is False`).

* **Test 5.4 (`test_cost_j_computation`):** Verify exact calculation of total length $L$ (in meters), total $90^\circ$ bends $B$, overlap length $O$, and crossing count $X$.

#### 5.5.4 Cross-Phase Integration Check (Phase 5 $\leftrightarrow$ Phases 1–4) & Review Gate

* **Cross-Check with Phases 1–4:** Verify that every synthesized `ClosedRingRoute` stays strictly inside the room polygons assigned to its zone in Phase 4, crosses room boundaries exclusively through Phase 2 portals belonging to that zone, uses axis-aligned coordinates from Phase 3, and has zero intersection with Phase 1 walls, windows, or exterior boundaries.

* **Phase 1–5 Regression Suite:** Run `pytest tests/test_phase1_ingestion.py tests/test_phase2_topology.py tests/test_phase3_micro_routing.py tests/test_phase4_partitioning.py tests/test_phase5_ring_sequencing.py`.

### Phase 6: Automated Design Validator, Scoring JSON Exporter & Floor-Plan Visual Overlay Engine

#### 5.6.1 Phase 6 Goal

Audit every generated solution against all 8 hard competition rules via an independent geometric/topological verifier (`DesignValidator`), serialize the machine-gradable validation/scoring JSON, and render a publication-grade floor-plan schematic overlay matching Slide 5/8 (`Expected solution: illustrative schematic`).

#### 5.6.2 What & How to Build in Phase 6

1. **`validator.py` (Independent 8-Rule Programmatic Design Auditor):**

   * Implement `DesignValidator.validate(floor_plan, topology, partition, routing) -> ValidationReport` which independently checks geometric primitives without trusting solver flags:

     1. **`rule_1_all_eligible_rooms_have_one_node`:** Every `living`, `bedroom`, `kitchen`, and `storage` room contains exactly 1 node; all other room types contain 0 nodes.

     2. **`rule_2_exclusive_node_and_room_assignment`:** Every eligible node belongs to exactly 1 hub; zones are mutually disjoint.

     3. **`rule_3_capacity_limits_respected`:** Every hub serves $\le 100.0\text{ m}^2$ of eligible room area and $\le N_{\max}$ nodes.

     4. **`rule_4_contiguous_zone_topology`:** Every hub's assigned rooms form a single connected component via doors/open passages only.

     5. **`rule_5_closed_nonbranching_ring`:** Every hub has a single closed loop $H_k \to N_{k,1} \to \dots \to N_{k,m} \to H_k$ visiting each assigned node once and returning to the **same** hub $H_k$.

     6. **`rule_6_axis_aligned_geometry`:** Every polyline segment is strictly horizontal ($\Delta y = 0$) or vertical ($\Delta x = 0$).

     7. **`rule_7_obstacle_and_boundary_avoidance`:** Zero cable segments intersect `wall`, `window`, `front_door`, or exterior areas outside `inner`.

     8. **`rule_8_inter_room_transitions_via_portals_only`:** Every transition between two rooms occurs inside a valid `door` or `open_passage` polygon.

2. **`json_exporter.py` (Validation & Scoring JSON Output):**

   * Emit a deterministic JSON document containing plan metadata, ranking metrics (`hub_count`, `lower_bound_hubs`, `total_cost_j`, `total_cable_length_m`, `total_bends`, `total_crossings`), per-hub zone summaries, ordered node sequences, segment-by-segment rectilinear coordinates, and the boolean `validation_summary`.

   * Schema structure:

     ```
     {
       "plan_id": "resplan_00142",
       "config_profile": "strict_v1",
       "summary_metrics": {
         "hub_count": 2,
         "lower_bound_hubs": 2,
         "total_eligible_area_m2": 118.0,
         "total_eligible_nodes": 6,
         "total_cable_length_m": 49.0,
         "total_bends": 20,
         "total_crossings": 0,
         "total_overlap_m": 0.0,
         "objective_cost_j": 59.0
       },
       "hubs": [
         {
           "hub_id": "H1",
           "hub_coord": [6.5, 3.2],
           "host_room_id": "living_0",
           "assigned_room_ids": ["living_0", "bedroom_0", "bedroom_2"],
           "assigned_node_ids": ["N1", "N2", "N3"],
           "zone_area_m2": 71.0,
           "node_count": 3,
           "ring_sequence": ["H1", "N1", "N3", "N2", "H1"],
           "cable_length_m": 23.8,
           "bend_count": 8,
           "crossings_count": 0,
           "segments": [
             {
               "step_index": 1,
               "from_id": "H1",
               "to_id": "N1",
               "waypoints": [[6.5, 3.2], [9.2, 3.2], [9.2, 1.4]],
               "length_m": 4.5,
               "bends": 1
             }
           ]
         }
       ],
       "validation": {
         "is_valid": true,
         "checks": {
           "all_eligible_rooms_have_one_node": true,
           "exclusive_node_assignment": true,
           "area_and_node_capacity_respected": true,
           "zones_contiguous_via_doors": true,
           "closed_non_branching_rings": true,
           "axis_aligned_routing": true,
           "no_wall_window_or_exterior_crossed": true,
           "inter_room_via_doors_or_passages_only": true
         },
         "violations": []
       }
     }
     
     ```

3. **`visualizer.py` (Schematic Overlay Engine Matching Slide 5/8):**

   * Build a two-panel [Matplotlib](https://www.google.com/search?q=https%3A%2F%2Fmatplotlib.org%2Fstable%2Findex.html) figure (`16x9` aspect ratio at `300 DPI`, exporting both `.png` and `.svg`):

     * **Left Main Canvas (Floor-Plan Overlay):**

       * Color-code each hub's contiguous zone with a soft pastel fill (e.g., Zone $H_1$ in soft sky blue `#DCE8F8`, Zone $H_2$ in soft peach `#FDE5D2`, Zone $H_3$ in soft mint `#DCF2E4`).

       * Render excluded rooms (`bathroom`, `balcony`) in neutral light grey `#EAEAEA` with label `"no node"`.

       * Draw exterior and interior `wall` polygons in crisp dark charcoal `#222222` with clear geometric gaps at `door` and `open_passage` portals, and `front_door` (`Entrance`) in bronze/brown.

       * Annotate each room in its top-left corner with its bold name and area in $\text{m}^2$ (e.g., `Living\n40 m²`).

       * Draw each Hub $H_k$ as a bold filled square with white label (`H1`, `H2`) in the zone's saturated primary color (`#1A62B8`, `#D96B27`).

       * Draw each Device Node $N_i$ as a filled circle (`N1`, `N2`, ...) in the zone's saturated primary color.

       * Draw each cable segment as an axis-aligned polyline with directional arrowheads approaching each target and a white-filled circled sequence badge (`1`, `2`, `3`, `4`) at the midpoint of each leg (where the final number marks the return leg to $H_k$).

     * **Right Sidebar (Legend, Per-Hub Summary & Reviewer Checklist):**

       * **Card 1 (`How to read`):** Legend icons for Hub, Node, Cable + sequence numbers, Return number, and Excluded room / door gaps.

       * **Card 2 (`Per-hub summary`):** Color-coded badge for each hub (`H1`, `H2`, ...) listing assigned rooms, total area ($ext{m}^2$), node count, cable length ($ext{m}$), and bend count.

       * **Card 3 (`What a reviewer confirms`):** Live pass/fail green checkmarks (`✓`) populated directly from `DesignValidator`:

         * `✓ Each node once; ring ends at its own hub`

         * `✓ <= 100 m² and <= 5 nodes per hub`

         * `✓ Each zone connected through doors only`

         * `✓ Axis-aligned; no wall or window crossed`

         * `✓ K hubs = lower bound (Area m², N nodes)`

#### 5.6.3 Phase 6 Verification Gate (Unit Tests & Invariant Passes)

Run `pytest tests/test_phase6_output.py tests/test_integration_pipeline.py` to verify:

* **Test 6.1 (`test_negative_validator_mutations`):** Inject 5 synthetic invalid solutions into `DesignValidator` (a route crossing a wall, an open route not returning to $H_k$, a loop ending at $H_2$ instead of $H_1$, a diagonal segment, and a zone with $105\text{ m}^2$ area); assert `validator.validate()` returns `is_valid == False` and pinpoints the exact violated rule for all 5 cases.

* **Test 6.2 (`test_json_schema_roundtrip`):** Assert that the exported JSON validates against `ValidatedSolutionBundle`, contains non-empty waypoints for every segment, and matches the sum of segment lengths and bends to within $10^{-4}$.

* **Test 6.3 (`test_visual_overlay_generation`):** Assert that `visualizer.render()` produces non-empty `.png` and `.svg` files with dimensions $\ge 2400 \times 1350\text{ px}$ in $< 2.0\text{ s}$.

#### 5.6.4 Cross-Phase Integration Check (Phase 6 $\leftrightarrow$ Phases 1–5) & Final System Review

* **End-to-End Pipeline Handshake:** Execute `cli.py` on both the 6-room schematic fixture and multiple unseen ResPlan JSON plans (`1`-bedroom, `2`-bedroom, `4`-bedroom, and multi-wing `5`-bedroom layouts) and verify that Phases $1 \to 2 \to 3 \to 4 \to 5 \to 6$ complete end-to-end with `validation.is_valid == True` and zero manual intervention.

## 6. Master Cross-Phase Integration & Regression Matrix Summarizes Verification Gates

Every phase completion requires passing three mandatory gates before engineering advances to the next phase:

| Phase | Core Deliverable Module | Phase Unit Test Gate (`pytest`) | Cross-Phase Handshake Verification | Regression & Performance Gate | 
| ----- | ----- | ----- | ----- | ----- | 
| **Phase 1** | `phase1_ingestion` (`FloorPlanModel`) | `test_phase1_ingestion.py`: WKT parsing, metric scaling, `polylabel` node containment, excluded room filtering | Validates raw ResPlan JSON $\to$ `FloorPlanModel` schema integrity | $< 150\text{ ms}$ per plan; 0 invalid/self-intersecting polygons | 
| **Phase 2** | `phase2_topology` (`MacroTopologyModel`) | `test_phase2_topology.py`: Door/arch portal detection, `front_door`/window rejection, dual-lane offsets | Confirms 1-to-1 room ID alignment with Phase 1 & portal containment inside `inner_polygon` | Re-runs Phase 1 + 2 tests; $< 100\text{ ms}$ graph build | 
| **Phase 3** | `phase3_micro_routing` (`MicroRoutingIndex`) | `test_phase3_micro_routing.py`: Strict $\Delta x=0 / \Delta y=0$ alignment, 0 wall/window hits, bend counting | Confirms Phase 3 paths exist iff Phase 2 `G_macro` components connect & cross only Phase 2 portals | Re-runs Phase 1–3 tests; $< 800\text{ ms}$ all-pairs matrix | 
| **Phase 4** | `phase4_partitioning` (`ZonePartitionResult`) | `test_phase4_partitioning.py`: Area $\le 100\text{ m}^2$, nodes $\le N_{\max}$, SCF contiguity, $K=K_{\min}$ lower bound | Confirms Phase 4 MILP uses Phase 3 costs, Phase 2 adjacency, and places hubs inside Phase 1 rooms | Re-runs Phase 1–4 tests; $< 1.5\text{ s}$ CP-SAT solve | 
| **Phase 5** | `phase5_ring_sequencing` (`RingRoutingResult`) | `test_phase5_ring_sequencing.py`: Closed loop $H_k \to \dots \to H_k$, each node once, 0 single-door spur crossings | Confirms Phase 5 rings stay inside Phase 4 zones, use Phase 2 dual-lane doors, and avoid Phase 1 walls | Re-runs Phase 1–5 tests; $< 500\text{ ms}$ ring synthesis | 
| **Phase 6** | `phase6_output` (`ValidatedSolutionBundle`) | `test_phase6_output.py` & `test_integration_pipeline.py`: 8-rule audit, JSON schema, PNG/SVG overlay | Full end-to-end check across Phases 1–6 on unseen ResPlan floor plans with `is_valid == True` | Full suite `pytest` 100% pass; total runtime $< 3.5\text{ s}$/plan | 
