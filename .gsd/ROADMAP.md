# ROADMAP.md — resplan-cable-opt-v1

> **Current Phase**: Not started
> **Milestone**: v1.0 Foundation
> **Status**: Ready for Planning

## Must-Haves (from SPEC.md & PRD v1)
- [ ] Reproducible Python 3.12 environment with declared dependencies (Pydantic v2, Shapely 2, NetworkX, OR-Tools, Matplotlib, Pytest).
- [ ] Canonical synthetic 6-room benchmark fixture (`schematic_6room.json`) with exact 118 m² area matching Slide 5/8.
- [ ] Real ResPlan importer/adapter with schema validation.
- [ ] Phase 1: Ingestion, scaling, and deterministic polylabel device placement.
- [ ] Phase 2: Macro dual-graph topology and dual-lane door/passage portal extraction (with transit-only corridor support).
- [ ] Phase 3: Obstacle-aware rectilinear Hanan/grid graph, bend-penalized A*, and pairwise cost matrix.
- [ ] Phase 4: Capacity-constrained contiguous zone partitioning via CP-SAT MILP ($K \ge K_{\min}$, area $\le 100\text{ m}^2$, nodes $\le N_{\max}$, Single-Commodity Flow contiguity).
- [ ] Phase 5: Closed-ring TSP sequencing (exact Held-Karp DP for $m \le 12$) and physical rectilinear cable loop synthesis with dual-lane separation for leaf rooms.
- [ ] Phase 6: Independent 8-rule `DesignValidator`, deterministic scoring JSON serialization, and dual-panel Matplotlib PNG/SVG schematic overlay.

---

## Phases

### Phase 1: Environment, Contracts & ResPlan Ingestion
**Status**: ⬜ Not Started
**Objective**: Set up Python 3.12 environment, create immutable Pydantic contracts (`FloorPlanModel`, `RoomEntity`, `DeviceNode`), construct the canonical 6-room benchmark fixture (`schematic_6room.json`), build real ResPlan schema parser/normalizer, and implement deterministic polylabel device placement.
**Verification Gate**: `tests/test_phase1_ingestion.py` (7 rooms in 6-room fixture, 6 eligible nodes, safe wall clearance $\ge 0.20\text{ m}$, 0 nodes in bathroom, serialization roundtrip).

### Phase 2: Topological Dual Graph & Portal Extraction
**Status**: ⬜ Not Started
**Objective**: Build `portal_extractor.py` and `macro_graph.py` to extract door and open-passage portals, distinguish transit-only rooms, compute dual-lane portal waypoints ($p_A, p_B$) with room-entry clearances, and represent $G_{\text{macro}}$ in NetworkX.
**Verification Gate**: `tests/test_phase2_topology.py` (exact door detection, window/front-door exclusion, free-space containment of dual-lane waypoints, regression pass on Phase 1).

### Phase 3: Rectilinear Micro-Routing & Pairwise Cost Matrix
**Status**: ⬜ Not Started
**Objective**: Construct the obstacle-buffered Hanan + clearance rectilinear grid, implement bend-penalized state-space $A^*$/Dijkstra, support zone-restricted subgraphs, and precompute the all-pairs rectilinear cost tensor.
**Verification Gate**: `tests/test_phase3_micro_routing.py` (strict horizontal/vertical segments, 0 wall/window hits, exact bend count, regression pass on Phases 1–2).

### Phase 4: Contiguous Zone Partitioning & Hub Placement (MILP)
**Status**: ⬜ Not Started
**Objective**: Implement candidate hub generation, CP-SAT Single-Commodity Flow (SCF) zone partitioner enforcing area $\le 100\text{ m}^2$, node capacity $\le N_{\max}$, and contiguous connectivity via portals (with escalating $K$ if cut vertices prevent feasibility), plus CCVT region-growing fallback.
**Verification Gate**: `tests/test_phase4_partitioning.py` (exact $K=2$ on 6-room benchmark, contiguity checks, cut-vertex escalation, regression pass on Phases 1–3).

### Phase 5: Closed-Ring Sequencing (TSP) & Physical Cable Synthesis
**Status**: ⬜ Not Started
**Objective**: Implement exact Held-Karp bitmask DP for TSP ($m \le 12$), synthesize non-branching rectilinear loops returning to originating hubs, and route dual-lane door offsets for single-door leaf rooms to prevent parallel overlap or self-intersection.
**Verification Gate**: `tests/test_phase5_ring_sequencing.py` (closed loop verification, each node visited once, zero collinear overlap or crossings on single-door leaf spurs, exact $J$ computation, regression pass on Phases 1–4).

### Phase 6: Independent Design Validator, Scoring JSON & Schematic Overlay
**Status**: ⬜ Not Started
**Objective**: Build independent 8-rule `DesignValidator`, scoring JSON serializer, and two-panel Matplotlib schematic visualizer (PNG + SVG) matching Slide 5/8; package end-to-end CLI.
**Verification Gate**: `tests/test_phase6_output.py` and `tests/test_integration_pipeline.py` (negative mutation test catching all 8 violation types, JSON schema roundtrip, high-res visual render in $< 2.0\text{ s}$, 100% full regression pass).
