---
milestone: V1 Foundation (Phases 1–6) & V2 Interactive Extension (Phases 7–10)
updated: 2026-10-15T23:30:00+05:30
---

# REQUIREMENTS.md

## Overview
Requirements derived from `v2.md` and `SPEC.md` for full traceability, test verification, and acceptance gating.

---

## Functional Requirements: V1 Foundation

### Phase 1: Ingestion, Scale Normalization, Polygon Healing & Node Loading
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-INGEST-01** | Apply exact scale conversion $\text{scale} = \sqrt{\text{inner.area} / \text{area}}$, converting all coordinates to metres and setting lower-left origin with $y$ pointing upwards. Strictly ignore `net_area`. | SPEC Goal 1 | 1 | Complete |
| **REQ-INGEST-02** | Repair non-simple geometries using `shapely.make_valid()` and filter degenerate slivers ($< 2.0\text{ m}^2$). | SPEC Goal 1 | 1 | Complete |
| **REQ-INGEST-03** | Classify rooms into eligible (`living`, `bedroom`, `kitchen`, `storage`) and excluded (`bathroom`, `balcony`, `stair`, `garden`, `pool`, `parking`, `land`). | SPEC Goal 1 | 1 | Complete |
| **REQ-INGEST-04** | Construct impassable obstacle union $\mathcal{O} = \text{wall} \cup \text{window} \cup \text{front\_door}$ and routable free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$. | SPEC Goal 1 | 1 | Complete |
| **REQ-INGEST-05** | Load immutable fixed interior nodes ($1$ per eligible room, $0$ for excluded rooms) and verify $\ge 0.5\text{ m}$ wall clearance. Provide deterministic $0.5\text{ m}$ `polylabel` generator for raw test fixtures. | SPEC Goal 1 | 1 | Complete |

### Phase 2: Door & Open-Passage Topology Extraction & Portal Waypoints
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-TOPO-01** | Detect interior door connections by buffering door polygons by $0.05\text{ m}$ and intersecting with room polygons using `STRtree`, excluding `front_door`. | SPEC Goal 2 | 2 | Complete |
| **REQ-TOPO-02** | Identify open-passage portals for adjacent room boundaries inside $\mathcal{F}$ having an unobstructed gap of width $\ge 0.50\text{ m}$ without a door polygon. | SPEC Goal 2 | 2 | Complete |
| **REQ-TOPO-03** | Construct dual graph $G_{\text{macro}}$ and verify all eligible room nodes belong to a single connected component. Reject connectivity across walls, windows, or front entrance. | SPEC Goal 2 | 2 | Complete |
| **REQ-TOPO-04** | Synthesize three portal tracks across each doorway/passage gap: centerline ($p_0$), Parallel Lane A ($p_A$), and Parallel Lane B ($p_B$), with lateral offset $\ge 20\text{ mm}$ to guarantee $O = 0$ on single-door retraces. | SPEC Goal 2 | 2 | Complete |

### Phase 3: Obstacle-Aware Axis-Aligned Micro-Routing & Cost Tensor
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-ROUTE-01** | Construct an axis-aligned Hanan grid inside $\mathcal{F}$ induced by fixed nodes, portal tracks, candidate hub positions, offset bypass lines ($\pm 0.04\text{ m}$), and wall-clearance contours. | SPEC Goal 3 | 3 | Complete |
| **REQ-ROUTE-02** | Filter grid edges to ensure all segments are strictly horizontal or vertical and lie entirely inside $\mathcal{F}$ with zero obstacle intersections. | SPEC Goal 3 | 3 | Complete |
| **REQ-ROUTE-03** | Implement direction-augmented state-space $(v, d_{\text{in}})$ Dijkstra/$A^*$ algorithm, where direction changes incur $+0.5$ bend cost ($90^\circ$ turns and $180^\circ$ reversals). | SPEC Goal 3 | 3 | Complete |
| **REQ-ROUTE-04** | Precompute and cache the directional pairwise cost tensor $C(u, d_{\text{start}} \to v, d_{\text{end}})$ between all nodes, portals, and candidate hub sites. | SPEC Goal 3 | 3 | Complete |

### Phase 4: Lexicographic Hub Placement & Contiguous Zone Partitioning
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-PART-01** | Sample candidate hub locations $\mathcal{H}_i$ within $0.5\text{ m}$ wall-eroded eligible room polygons ($R_i \setminus \mathcal{O}.\text{buffer}(0.505\text{ m})$), maintaining $\|H_k - N_i\|_2 \ge 0.30\text{ m}$ for clarity. | SPEC Goal 4 | 4 | Complete |
| **REQ-PART-02** | Enforce minimal hub lower bound: $H \ge H_{\min} = \max(\lceil \sum A_i / 100 \rceil, \lceil N / 5 \rceil)$. Increment $H$ only if no valid partition exists. | SPEC Goal 4 | 4 | Complete |
| **REQ-PART-03** | Partition eligible rooms into $H$ disjoint zones covering all eligible rooms, ensuring each zone is connected in $G_{\text{macro}}$ via doors/open passages only. | SPEC Goal 4 | 4 | Complete |
| **REQ-PART-04** | Enforce capacity limits: assigned eligible-room area $\le 100.0\text{ m}^2$ and assigned nodes $\le 5$ per hub. | SPEC Goal 4 | 4 | Complete |
| **REQ-PART-05** | Formulate lexicographic objective in CP-SAT/MILP: minimize $H$, then minimize sum of exact closed-ring costs $\sum J^*(Z_k)$, with tertiary tie-breaker minimizing area gap $\Delta A_{\text{gap}}$. | SPEC Goal 4 | 4 | Complete |

### Phase 5: Direction-Aware Closed-Ring Sequencing & Route Synthesis
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-RING-01** | Implement direction-aware Held-Karp dynamic programming for $m \le 5$ nodes, tracking node turns and wrap-around hub arrival/departure bend count. | SPEC Goal 5 | 5 | Complete |
| **REQ-RING-02** | Synthesize sequential conflict-free rectilinear polylines for the ring sequence $H_k \to N_1 \to \dots \to N_m \to H_k$ using the zone's rectilinear grid. | SPEC Goal 5 | 5 | Complete |
| **REQ-RING-03** | Route out-and-back runs in single-door leaf rooms along Parallel Door Lanes A and B, achieving $O = 0.0\text{ m}$ and $X = 0$. | SPEC Goal 5 | 5 | Complete |
| **REQ-RING-04** | Simplify polyline representations by merging consecutive collinear segments, ensuring each internal vertex represents an authentic $90^\circ$ turn. | SPEC Goal 5 | 5 | Complete |

### Phase 6: Independent Evaluation Checker, JSON Export & Visualization
| ID | Requirement | Source | Phase | Status |
|----|-------------|--------|-------|--------|
| **REQ-OUT-01** | Build standalone evaluation checker `checker.py` that recomputes Hard Rules 1–6 independently from raw exported polylines using $1\text{ mm}$ spatial tolerance. | SPEC Goal 6 | 6 | Complete |
| **REQ-OUT-02** | Recompute exact metrics $L$, $X$ ($> 1\text{ mm}$ from nodes/hubs), $O$ ($\le 1\text{ mm}$ lateral distance), $B$ (direction changes including nodes and hubs), and cost $J = L + 5X + O + 0.5B$. | SPEC Goal 6 | 6 | Complete |
| **REQ-OUT-03** | Serialize output to exact Slide 8 schema with keys `plan_id`, `units`, `hubs`, `assigned_rooms`, `assigned_nodes`, `closed_sequence`, `route_segments`, `ring_length_m`, and `reported_metrics`. | SPEC Goal 6 | 6 | Complete |
| **REQ-OUT-04** | Render two-column floor-plan overlay matching Slide 5 with pastel zone fills, room labels, numbered leg badges, per-hub summary cards, and reviewer checklists. | SPEC Goal 6 | 6 | Complete |
| **REQ-OUT-05** | Highlight any hard rule violations in bright red (`#E6194B`) with diagnostic callout badges per Slide 3 requirements. | SPEC Goal 6 | 6 | Complete |

---

## Functional Requirements: V2 Interactive Extension (Milestone 2)

| ID | Requirement | Category | Phase | Status |
|----|-------------|----------|-------|--------|
| **REQ-V2-01** | Live Floor-Plan Co-Design Canvas (< 50 ms incremental re-routing over WebSockets on wall/door/node drag). | Interactive UX | 7 | Complete |
| **REQ-V2-02** | "Architectural Co-Pilot" Doorway & Wall Optimizer recommending structural changes to lower $H$ or $J$. | Optimization | 8 | Deferred |
| **REQ-V2-03** | Interactive Wire-Cut NFPA 72 Class A Survivability Simulator demonstrating signal loop recovery. | Simulation | 9 | Deferred |
| **REQ-V2-04** | Synchronized 2D/3D BIM Split-Screen with live checker HUD and one-click JSON export. | 3D Visualization | 10 | Deferred |

---

## Non-Functional Requirements

| ID | Requirement | Category | Target | Status |
|----|-------------|----------|--------|--------|
| **NFR-01** | Execution Runtime | Performance | $< 2.5\text{ s}$ per floor plan for full end-to-end pipeline | Pending |
| **NFR-02** | Geometric Precision | Accuracy | Floating-point coordinates stored with $\ge 0.1\text{ mm}$ precision | Pending |
| **NFR-03** | Determinism | Reliability | Identical inputs produce bitwise identical JSON polylines across runs | Pending |
| **NFR-04** | Test Coverage | Quality | 100% test pass on unit suites, integration suites, and Slide 5 benchmark | Pending |
| **NFR-05** | Modularity | Architecture | Clear Pydantic data contracts at every inter-phase boundary | Pending |
| **NFR-06** | V2 Frame Rate | Interactivity | $\ge 60\text{ FPS}$ UI interaction with $< 35\text{ ms}$ backend solver turnaround | Deferred |

---

## Constraints

| ID | Constraint | Source | Impact |
|----|------------|--------|--------|
| **CON-01** | Rule 1: Full Coverage (every eligible room/node assigned to exactly 1 hub) | Slide 3 | Phase 4 Partitioning |
| **CON-02** | Rule 2: Capacity Limits ($\le 100\text{ m}^2$ area, $\le 5$ nodes per hub) | Slide 3 | Phase 4 Partitioning |
| **CON-03** | Rule 3: Door/Passage Zone Contiguity (no windows, walls, or entrance) | Slide 3 | Phase 2 & 4 Topology |
| **CON-04** | Rule 4: Closed Ring (one closed non-branching loop per hub) | Slide 3 | Phase 5 Sequencing |
| **CON-05** | Rule 5: Axis-Aligned Free-Space Runs ($\Delta x = 0$ or $\Delta y = 0$, inside $\mathcal{F}$) | Slide 3 | Phase 3 Micro-Routing |
| **CON-06** | Rule 6: Hub Point Clearance ($\ge 0.5\text{ m}$ from walls inside eligible room) | Slide 3 | Phase 4 Hub Candidates |
| **CON-07** | Model Independence & Single-Command Shell Discipline | PROJECT_RULES.md | All scripts & execution |

---

## Traceability Matrix

| Requirement | Phase | Primary Module | Test Case | Status |
|---|---|---|---|---|
| REQ-INGEST-01 | 1 | `scale_converter.py` | `test_slide4_exact_scale_conversion` | Complete |
| REQ-INGEST-02 | 1 | `parser.py` | `test_polygon_healing` | Complete |
| REQ-INGEST-03 | 1 | `parser.py` | `test_eligible_and_excluded_rooms` | Complete |
| REQ-INGEST-04 | 1 | `parser.py` | `test_obstacle_and_freespace_union` | Complete |
| REQ-INGEST-05 | 1 | `node_loader.py` | `test_fixed_node_immutability_and_clearance` | Complete |
| REQ-TOPO-01 | 2 | `portal_extractor.py` | `test_slide5_door_connectivity` | Complete |
| REQ-TOPO-02 | 2 | `portal_extractor.py` | `test_open_passage_detection` | Complete |
| REQ-TOPO-03 | 2 | `macro_graph.py` | `test_window_wall_entrance_rejection` | Complete |
| REQ-TOPO-04 | 2 | `macro_graph.py` | `test_parallel_door_lanes_exceed_1mm_tolerance` | Complete |
| REQ-ROUTE-01 | 3 | `rectilinear_grid.py` | `test_hanan_grid_construction` | Complete |
| REQ-ROUTE-02 | 3 | `rectilinear_grid.py` | `test_strict_horizontal_vertical_runs` | Complete |
| REQ-ROUTE-03 | 3 | `astar_rectilinear.py` | `test_slide7_bend_and_reversal_counting` | Complete |
| REQ-ROUTE-04 | 3 | `cost_matrix.py` | `test_pairwise_cost_tensor_caching` | Complete |
| REQ-PART-01 | 4 | `hub_candidates.py` | `test_rule6_hub_05m_wall_clearance` | Complete |
| REQ-PART-02 | 4 | `zone_milp.py` | `test_lower_bound_hub_count` | Complete |
| REQ-PART-03 | 4 | `zone_milp.py` | `test_door_connected_zone_partitions` | Complete |
| REQ-PART-04 | 4 | `zone_milp.py` | `test_capacity_constraints_area_and_nodes` | Complete |
| REQ-PART-05 | 4 | `zone_milp.py` | `test_area_gap_tie_breaker` | Complete |
| REQ-RING-01 | 5 | `exact_ring_tsp.py` | `test_direction_aware_held_karp` | Pending |
| REQ-RING-02 | 5 | `loop_synthesizer.py` | `test_slide5_ring_metrics` | Pending |
| REQ-RING-03 | 5 | `loop_synthesizer.py` | `test_single_door_parallel_runs_zero_overlap` | Pending |
| REQ-RING-04 | 5 | `loop_synthesizer.py` | `test_collinear_vertex_simplification` | Pending |
| REQ-OUT-01 | 6 | `checker.py` | `test_checker_rule_verification` | Pending |
| REQ-OUT-02 | 6 | `checker.py` | `test_slide7_metric_examples_exact` | Pending |
| REQ-OUT-03 | 6 | `json_exporter.py` | `test_slide8_json_schema_exact_keys` | Pending |
| REQ-OUT-04 | 6 | `visualizer.py` | `test_schematic_render_generation` | Pending |
| REQ-OUT-05 | 6 | `visualizer.py` | `test_negative_rules_and_red_violation_rendering` | Pending |

---

*Last updated: 2026-10-15*
