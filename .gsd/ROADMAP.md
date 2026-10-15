---
milestone: V1 Foundation (Phases 1–6) & V2 Interactive Extension (Phases 7–10)
version: 1.0.0
updated: 2026-10-15T23:30:00+05:30
---

# ROADMAP.md

> **Current Milestone:** V1 Foundation (Core Algorithmic Engine & Reference Checker)  
> **Current Phase:** Phase 5 — Direction-Aware Closed-Ring Sequencing & Zero-Overlap Route Synthesis  
> **Status:** ⬜ Ready for Planning

---

## Must-Haves (from SPEC)

- [ ] 100% compliance on the 6 Hard Validity Rules (Coverage, Capacity, Contiguity, Closed Ring, Axis-Aligned Free Space, $0.5\text{ m}$ Clearance).
- [ ] Deterministic replication of Slide 5 benchmark: 6 rooms, 2 hubs, $J = 59.0$ ($L = 49.0\text{ m}, B = 20, X = 0, O = 0.0\text{ m}$).
- [ ] Zero crossings ($X = 0$) and zero overlap ($O = 0.0\text{ m}$) on leaf/single-door topologies using $\ge 20\text{ mm}$ parallel doorway lanes.
- [ ] Independent $1\text{ mm}$ reference checker (`checker.py`) evaluating $J = L + 5X + O + 0.5B$ and verifying all 6 rules.
- [ ] Export exact Slide 8 compliant output JSON.
- [ ] Two-column floor-plan overlay visualization with red violation highlights per Slide 3 & 5.
- [ ] Per-plan runtime $< 2.5\text{ s}$.

---

## Milestone 1: V1 Foundation (Phases 1–6)

### Phase 1: Ingestion, Scale Normalization, Polygon Healing & Fixed Node Loading
**Status:** ✅ Complete  
**Objective:** Parse raw ResPlan floor plan JSONs, normalize units/scale using $\text{scale} = \sqrt{\text{inner.area} / \text{area}}$, heal polygons via Shapely `make_valid`, classify eligible vs. excluded rooms, construct free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$, and load/validate fixed interior nodes ($\ge 0.5\text{ m}$ clearance).  
**Requirements:** REQ-INGEST-01, REQ-INGEST-02, REQ-INGEST-03, REQ-INGEST-04, REQ-INGEST-05  

**Plans:**
- [x] Plan 1.1: `scale_converter.py` & `contracts.py` (Metric scale transform, lower-left coordinate frame, Pydantic contracts)
- [x] Plan 1.2: `parser.py` & `node_loader.py` (Polygon healing, room classification, obstacle/free-space union, node validation & polylabel fallback)

---

### Phase 2: Door & Open-Passage Topology Extraction & Parallel-Lane Portal Waypoints
**Status:** ✅ Complete  
**Objective:** Construct the room-portal dual graph $G_{\text{macro}}$ in NetworkX connecting rooms exclusively across doors and open passages ($\ge 0.5\text{ m}$ width, excluding windows/walls/entrance), and synthesize parallel doorway lanes separated by $\ge 20\text{ mm}$ to guarantee zero overlap ($O = 0$) on single-door runs.  
**Depends on:** Phase 1  
**Requirements:** REQ-TOPO-01, REQ-TOPO-02, REQ-TOPO-03, REQ-TOPO-04  

**Plans:**
- [x] Plan 2.1: `portal_extractor.py` (Door polygon buffering, room-pair intersection, open passage detection)
- [x] Plan 2.2: `macro_graph.py` (Dual graph construction, connectivity verification, $20\text{ mm}$ parallel lane generation)

---

### Phase 3: Obstacle-Aware Axis-Aligned Micro-Router & Directional Cost Tensor
**Status:** ✅ Complete  
**Objective:** Build a free-space Hanan routing grid within $\mathcal{F}$ incorporating portal lanes and wall clearance, implement a direction-augmented state-space $(v, d_{\text{in}})$ Dijkstra/$A^*$ router that prices $90^\circ$ turns ($+0.5$), $180^\circ$ reversals ($+0.5$), overlap ($+1.0/\text{m}$), and crossings ($+5.0$), and precompute the all-pairs directional cost tensor.  
**Depends on:** Phase 2  
**Requirements:** REQ-ROUTE-01, REQ-ROUTE-02, REQ-ROUTE-03, REQ-ROUTE-04  

**Plans:**
- [x] Plan 3.1: `rectilinear_grid.py` (Free-space Hanan grid generation, obstacle clipping, room labeling)
- [x] Plan 3.2: `astar_rectilinear.py` & `cost_matrix.py` (Directional state-space $A^*$, bend/reversal accounting, pairwise cost tensor caching)

---

### Phase 4: Lexicographic Hub Placement & Contiguous Zone Partitioning
**Status:** ✅ Complete  
**Objective:** Formulate and solve the contiguous room partitioning problem over $G_{\text{macro}}$ using CP-SAT/MILP, minimizing hub count $H \ge \max(\lceil \text{area}/100 \rceil, \lceil \text{nodes}/5 \rceil)$, ensuring capacity limits ($\le 100\text{ m}^2, \le 5$ nodes), sampling $0.5\text{ m}$ wall-cleared candidate hub points in host rooms, pricing zones with exact ring costs, and applying the area-gap tie-breaker ($\min \Delta A_{\text{gap}}$).  
**Depends on:** Phase 3  
**Requirements:** REQ-PART-01, REQ-PART-02, REQ-PART-03, REQ-PART-04, REQ-PART-05  

**Plans:**
- [x] Plan 4.1: `hub_candidates.py` (Wall-eroded polygon sampling, Hanan alignment, node-hub separation)
- [x] Plan 4.2: `zone_milp.py` (Contiguous zone partitioning, CP-SAT/MILP formulation, lexicographic tie-breaking)

---

### Phase 5: Direction-Aware Closed-Ring Sequencing & Zero-Overlap Route Synthesis
**Status:** ✅ Complete  
**Objective:** Determine the optimal closed node sequence $H_k \to N_1 \to \dots \to N_m \to H_k$ for each hub using direction-aware Held-Karp dynamic programming ($m \le 5$), tracking turns at nodes and hubs, and synthesize physical polylines using parallel door lanes to guarantee zero overlap ($O = 0$) and zero crossings ($X = 0$).  
**Depends on:** Phase 4  
**Requirements:** REQ-RING-01, REQ-RING-02, REQ-RING-03, REQ-RING-04  

**Plans:**
- [x] Plan 5.1: `exact_ring_tsp.py` (Direction-aware Held-Karp DP for $m \le 5$ with wrap-around hub bend tracking)
- [x] Plan 5.2: `loop_synthesizer.py` (Conflict-free polyline routing, dynamic crossing penalties, collinear segment simplification)

---

### Phase 6: Official $1\text{ mm}$ Evaluation Checker, Slide 8 JSON Exporter & Visual Overlay Renderer
**Status:** ✅ Complete  
**Objective:** Build an independent evaluation checker (`checker.py`) replicating Slide 3 & 6-7 rules at $1\text{ mm}$ tolerance, serialize the solution into the exact Slide 8 JSON schema, and render the Slide 5 floor-plan overlay with red violation highlights and reviewer scorecards.  
**Depends on:** Phase 5  
**Requirements:** REQ-OUT-01, REQ-OUT-02, REQ-OUT-03, REQ-OUT-04, REQ-OUT-05  

**Plans:**
- [x] Plan 6.1: `checker.py` (Independent $1\text{ mm}$ rule & metric recomputation: $H, L, X, O, B, J, \Delta A_{\text{gap}}$)
- [x] Plan 6.2: `json_exporter.py` & `visualizer.py` (Slide 8 serialization, 2-column Matplotlib schematic, red violation highlighter, CLI entrypoint)

---

## Milestone 2: V2 Interactive Extension (Phases 7–10)

> Note: Phases 7–10 build directly on the V1 core engine once all V1 regression gates pass.

### Phase 7: Live Floor-Plan Co-Design Canvas (`< 50 ms` Re-Routing)
**Status:** ✅ Complete  
**Objective:** Real-time WebSockets canvas allowing interactive dragging of walls, doors, hubs, and nodes with live $0.5\text{ m}$ clearance haloes and instantaneous recalculation ($< 35\text{ ms}$).  
**Requirements:** REQ-V2-01  

**Plans:**
- [x] Plan 7.1: `incremental_router.py` & `halo_tracker.py` (Sub-50 ms incremental re-routing engine, live wall-clearance halo, FastAPI WebSockets server)
- [x] Plan 7.2: `index.html`, `app.js`, `style.css` (Browser co-design canvas, live reviewer HUD, one-click Slide 8 export, `--serve` CLI launcher)

---

### Phase 8: "Architectural Co-Pilot" Doorway & Wall Optimizer
**Status:** ✅ Complete  
**Objective:** Automated perturbation engine proposing structural tweaks (sliding doors, adding open passages) to reduce required hubs $H$ or lower total cost $J$.  
**Requirements:** REQ-V2-02  

**Plans:**
- [x] Plan 8.1: `wall_optimizer.py` & `candidate_generator.py` (Perturbation search engine, shared interior wall detector, ranking core by $\Delta H$ and $\Delta J$)
- [x] Plan 8.2: `server.py`, `app.js`, `index.html` (Studio HUD integration, canvas ghost doorway overlays with floating savings pills, one-click Apply)

---

### Phase 9: Interactive Wire-Cut Survivability Simulator (NFPA 72 Class A)
**Status:** ✅ Complete  
**Objective:** Interactive wire-cutting tool demonstrating dual-direction loop fault tolerance where signals reverse from the hub return terminal so all nodes remain online.  
**Requirements:** REQ-V2-03  

**Plans:**
- [x] Plan 9.1: `circuit_simulator.py` (NFPA 72 Class A loop graph engine, split-branch path lengths, 100% device survivability proof)
- [x] Plan 9.2: `server.py`, `app.js`, `index.html` (Studio Scissors Tool ✂️, dual-direction animated signal pulses, live survivability gauge)

---

### Phase 10: Synchronized 2D/3D BIM Split-Screen & Live Checker HUD
**Status:** 🔄 In Progress  
**Objective:** 3D extruded apartment viewer (Three.js) synchronized with 2D plan and real-time score HUD ($H, L, X, O, B, J, \Delta A_{\text{gap}}$) with one-click JSON export.  
**Requirements:** REQ-V2-04  

**Plans:**
- [ ] Plan 10.1: `bim_generator.py` (3D architectural BIM extrusion, 2.7m walls, door lintels, elevated conduits, Wavefront .obj export)
- [ ] Plan 10.2: `bim_viewer.js`, `app.js`, `index.html` (Synchronized 2D/3D split-screen, Three.js WebGL viewer, OrbitControls, live HUD formula breakdown)

---

## Progress Summary

| Phase | Milestone | Status | Plans | Complete |
|---|---|---|---|---|
| **Phase 1** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 2** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 3** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 4** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 5** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 6** | V1 Foundation | ✅ Complete | 2/2 | 100% |
| **Phase 7** | V2 Extension | ✅ Complete | 2/2 | 100% |
| **Phase 8** | V2 Extension | ✅ Complete | 2/2 | 100% |
| **Phase 9** | V2 Extension | ✅ Complete | 2/2 | 100% |
| **Phase 10** | V2 Extension | 🔄 In Progress | 0/2 | 0% |

---

*Last updated: 2026-10-15*
