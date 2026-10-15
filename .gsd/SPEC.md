# SPEC.md — Project Specification

> **Status**: `DRAFT (Pending Source & Dataset Verification)`
>
> ⚠️ **Planning Lock**: No implementation code may be written until this spec is marked `FINALIZED`. The claims flagged in Section 7 must be verified against official organizer challenge materials and sample datasets prior to finalization.

---

## 1. Vision
An automated, mathematically rigorous, and deterministic optimization and visualization engine that ingests residential floor plan layouts from the ResPlan benchmark and jointly determines four coupled outputs: (1) minimal hub count and $0.5\text{ m}$ wall-cleared metric locations, (2) contiguous door-connected room/node partitions satisfying capacity limits ($\le 100\text{ m}^2, \le 5$ nodes), (3) closed-ring visiting sequences ($H_k \to N_1 \to \dots \to N_m \to H_k$), and (4) axis-aligned physical rectilinear cable routes. The solution strictly avoids wall and window obstacles, routes inter-room cabling exclusively through door and open-passage gaps, eliminates crossings ($X = 0$) and collinear overlaps ($O = 0$), and minimizes total penalised cost $J = L + 5X + O + 0.5B$ evaluated under a strict $1\text{ mm}$ tolerance.

---

## 2. Goals
1. **Deterministic Scale Conversion & Ingestion**: Parse raw floor plan JSONs in drawing units, normalize coordinates to metres using verified scale conversion ($\text{scale} = \sqrt{\text{inner.area} / \text{area}}$), repair non-simple polygons, and index eligible room polygons with fixed interior nodes.
2. **Door & Passage Dual Topology**: Build the room-portal dual graph $G_{\text{macro}}$ permitting passage exclusively across interior doors and open passages ($\ge 0.5\text{ m}$ width), and establish offset parallel doorway lanes ($\ge 20\text{ mm}$ apart) to guarantee zero overlap ($O = 0$) on out-and-back runs into single-door leaf rooms.
3. **Axis-Aligned Micro-Routing & Directional Cost Tensor**: Construct a free-space Hanan routing grid within $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$ and precompute a directional state-space $(v, d_{\text{in}})$ cost tensor that penalizes $90^\circ$ bends ($+0.5$), $180^\circ$ reversals ($+0.5$), collinear overlaps ($+1.0/\text{m}$), and crossings ($+5.0$).
4. **Lexicographic Zone Partitioning & Hub Placement**: Globally optimize hub count $H \ge H_{\min} = \max(\lceil \text{area}/100 \rceil, \lceil \text{nodes}/5 \rceil)$ and contiguous door-connected room partitions using CP-SAT/MILP with exact ring pricing and area-gap tie-breaking ($\min \Delta A_{\text{gap}}$).
5. **Exact Direction-Aware Closed-Ring Sequencing**: Solve the travelling salesperson ring tour using Held-Karp dynamic programming tracking entry/exit directions at nodes and hubs ($m \le 5$ nodes), synthesizing conflict-free rectilinear polylines.
6. **Independent $1\text{ mm}$ Reference Checker & Slide 8 Export**: Recompute all 6 hard validity rules and cost metric $J = L + 5X + O + 0.5B$ directly from output polylines using a $1\text{ mm}$ tolerance, serialize the exact Slide 8 JSON format, and render two-column schematic overlays with red violation badges.

---

## 3. Non-Goals (Out of Scope for V1 Foundation)
- **Interactive UI & Real-Time Co-Design Studio**: WebSockets, interactive drag-and-drop canvases, and React Three Fiber 3D extrusion are deferred to Milestone 2 (Phases 7–10).
- **Moving Fixed Node Coordinates**: Fixed node coordinates provided in benchmark plans are immutable per official rules; the algorithm cannot relocate them.
- **Diagonal or 3D Vertical Cabling**: All cable runs must be 2D axis-aligned rectilinear polylines ($\Delta x = 0$ or $\Delta y = 0$). No 45° chamfers or 3D risers.
- **Corridor / Foyer Device Nodes**: Corridors, transit spaces, and excluded spaces (`bathroom`, `balcony`, `stair`, etc.) must not host hubs or device nodes.

---

## 4. Users & Target Benchmark
- **Target Benchmark**: UR Hackathon 2026H2 — Problem Statement 4 (`Cable Ring Challenge - Problem Statement4.pptx`, Slides 1–8).
- **Dataset**: ResPlan Dataset on Kaggle ([arXiv:2508.14006](https://arxiv.org/html/2508.14006v1), residential floor plans).
- **Primary Users**: Hackathon Automated Evaluation Checker and Low-Voltage / Fire-Safety Low-Voltage BIM Systems Engineers.

---

## 5. Constraints & Hard Validity Rules (Slide 3)
A solution receives a score of 0 if any of the following 6 hard validity rules is violated:
1. **Rule 1 — Full Coverage**: Every eligible room (`living`, `bedroom`, `kitchen`, `storage`) and its fixed node is assigned to exactly one hub; none omitted, duplicated, or partitioned.
2. **Rule 2 — Capacity Limits**: Each hub serves at most $100.0\text{ m}^2$ of assigned eligible room area AND at most $5$ nodes simultaneously.
3. **Rule 3 — Zone Door Contiguity**: The rooms assigned to any hub must form a connected subgraph in $G_{\text{macro}}$ through doors or open passages only. Adjacency across solid walls, windows, or the building entrance (`front_door`) is strictly invalid.
4. **Rule 4 — Closed Ring Structure**: Exactly one non-branching closed loop per hub: $H_k \to N_{k,1} \to \dots \to N_{k,m} \to H_k$, visiting every assigned node once and returning to the originating hub.
5. **Rule 6 — Hub Placement & Clearance**: Every hub point must be placed inside an eligible room assigned to that hub and maintain at least $0.5\text{ m}$ ($0.5 \times \text{scale}$ drawing units) perpendicular clearance from all wall and window obstacles.
6. **Rule 5 — Free Space & Axis Alignment**: All cable polylines must lie strictly inside `inner`, outside `wall` and `window` polygons, run strictly horizontally or vertically ($\Delta x = 0$ or $\Delta y = 0$), and cross room boundaries only through door or open-passage openings.

### Evaluation Metric (Slides 3, 6 & 7)
- **Primary Ranking**: Minimum hub count $H$.
- **Secondary Ranking**: Minimum total cost $J = L + 5X + O + 0.5B$ (evaluated at $1\text{ mm} = 0.001\text{ m}$ tolerance):
  - **$L$ (Cable Length, weight $1.0/\text{m}$)**: Total polyline length across all rings.
  - **$X$ (Crossings, weight $5.0\text{ each}$)**: Non-collinear intersections between cables occurring $> 1\text{ mm}$ away from any node or hub.
  - **$O$ (Overlap, weight $1.0/\text{m}$)**: Collinear overlap length where segments run within $1\text{ mm}$ lateral distance on the same line.
  - **$B$ (Bends, weight $0.5\text{ each}$)**: Direction changes ($90^\circ$ turns and $180^\circ$ reversals) along each closed ring, including turns at nodes and hubs. Straight-through passage counts as 0 bends.
- **Tertiary Tie-Breaker**: Minimum area gap between hubs: $\Delta A_{\text{gap}} = \max_k \text{Area}(H_k) - \min_k \text{Area}(H_k)$.

---

## 6. Success Criteria
- [ ] 100% pass rate on all 6 hard rules across all test benchmark floor plans.
- [ ] Reference checker (`checker.py`) confirms $X = 0$ and $O = 0.0\text{ m}$ on benchmark plans with single-door leaf rooms.
- [ ] Slide 5 benchmark exact replication: 6 rooms, 2 hubs, $J = 59.0$ ($L = 49.0\text{ m}, B = 20, X = 0, O = 0.0\text{ m}$).
- [ ] Output JSON schema matches Slide 8 verbatim.
- [ ] End-to-end processing runtime $< 2.5\text{ s}$ per floor plan.

---

## 7. Claims Flagged for Source or Dataset Verification Prior to Spec Finalization

The following 9 claims extracted from `v2.md` require verification against official organizer materials (`Cable Ring Challenge - Problem Statement4.pptx`) and sample evaluation dataset files before finalizing `SPEC.md`:

| # | Flagged Claim | Stated Rule / Source in `v2.md` | Verification Required & Risk Analysis | Verification Action |
|---|---------------|--------------------------------|---------------------------------------|---------------------|
| **F-01** | **Input Node Format: Fixed vs Algorithmic** | "Node points are fixed inputs provided in the input JSON... Teams cannot move them. Phase 1 reads fixed node coordinates directly..." | **Risk**: Raw ResPlan JSONs do *not* contain device node coordinates. If the hackathon provides raw ResPlan files without node keys, the algorithm must place nodes (e.g. via $0.5\text{ m}$ `polylabel`). If the organizer provides pre-annotated fixtures, the exact JSON key schema (`nodes` vs `points`) must be confirmed. | Inspect sample input JSON from hackathon organizer package to verify key structure. |
| **F-02** | **Hub Capacity Node Limit: 5 vs 20** | "Each hub serves <= 100 m² ... AND <= 5 nodes at once ... (Note: updated from 20 to 5 in official deck...)" | **Risk**: If the official evaluation engine still enforces the original 20-node limit rather than 5, artificially constraining the solver to 5 nodes could force $H=3$ instead of $H=2$, losing primary ranking points! | Check Problem Statement Slide 2/3 text for whether `max_nodes_per_hub` is 5 or 20, or make it a configurable parameter defaulting to the official deck value. |
| **F-03** | **Scale Normalization Formula & Origin** | "scale = sqrt(plan['inner'].area / plan['area'])" and "Origin is lower-left, y up. Never use net_area." | **Risk**: If `inner` is a polygon in drawing units squared and `area` is in $m^2$, the formula yields $\text{units}/\text{m}$. But does the organizer test harness provide unscaled drawing units or pre-scaled metres? Does ResPlan follow lower-left origin or standard SVG/image upper-left origin ($y$ downwards)? | Inspect raw ResPlan JSON schema and sample test fixtures to verify bounding box orientation and area fields. |
| **F-04** | **Corridor / Foyer / Hallway Room Classification** | "Eligible: living, bedroom, kitchen, storage. Excluded: bathroom, balcony, stair, garden, pool, parking, land." | **Risk**: How are corridors, hallways, and entry foyers labeled in the dataset? If treated as excluded, cabling cannot pass through them unless explicitly defined as transit free space. If treated as eligible, they require device nodes. | Verify the complete set of room category strings occurring in the ResPlan dataset and define corridor transit rules. |
| **F-05** | **Zero-Overlap Parallel Lanes in Narrow Doorways** | "Parallel runs on different lines (> 1 mm apart, e.g. 20 mm offset) do NOT count as overlap (O = 0)!" | **Risk**: Does the organizer checker strictly measure line coincidence within $1\text{ mm}$, or is there a minimum cable-to-cable physical clearance? If $20\text{ mm}$ spacing in a $0.8\text{ m}$ door is valid, $O=0$ is achieved; if door retrace must be on the centerline, $O$ is incurred. | Confirm with Slide 7 diagram and text whether parallel lines separated by $> 1\text{ mm}$ have $O = 0$. |
| **F-06** | **Crossings ($X$) Definition at Nodes and Hubs** | "Legs meeting at a node or hub do not count as crossings (X = 0)... counted away from a node or hub (> 1 mm)." | **Risk**: If two distinct loops touch the same node or pass near a hub, does the checker enforce $X=0$ strictly for legs assigned to that node, or does any line intersecting within $1\text{ mm}$ of the node count as $X=0$? | Review Slide 7 crossing example and checker implementation. |
| **F-07** | **Doorway and Open Passage Geometric Representation** | "buffer by 0.05m and intersect with room polygons... open passages have unobstructed gap >= 0.50m..." | **Risk**: Are doors explicit polygon objects with category `"door"` in the JSON, or line segments? Are open passages explicitly labeled, or must they be inferred from boundary gaps between rooms? | Inspect input dataset JSON to confirm door schema and presence of open passages. |
| **F-08** | **Benchmark Filter Guarantees** | "<= 10 eligible rooms per plan; every eligible room between 2 m² and 100 m²; single connected component." | **Risk**: If an unseen evaluation plan has an eligible room $> 100\text{ m}^2$, Rule 2 makes a valid single-hub assignment impossible without room subdivision. | Confirm whether organizer test suite guarantees room area $\le 100\text{ m}^2$ or requires multi-hub splitting of oversized rooms. |
| **F-09** | **Slide 8 Output Schema Field Exactness** | Specific keys: `plan_id`, `units`, `hubs`, `assigned_rooms`, `assigned_nodes`, `closed_sequence`, `route_segments`, `ring_length_m`, `reported_metrics`. | **Risk**: Automated checkers often perform strict key validation. Any slight difference (`sequence` vs `closed_sequence`, `total_length` vs `total_length_m`) causes automated scoring rejection. | Cross-check output schema against Problem Statement Slide 8 JSON snippet. |

---

*Last updated: 2026-10-15 (GSD Project Initialization)*
