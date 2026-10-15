# SPEC.md — Project Specification

> **Status**: `FINALIZED`
>
> **Source of truth**: `v2.md`. The challenge slides and ResPlan archive provide implementation evidence. The organiser input JSON schema remains an adapter detail to confirm when a sample is available.

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

## 7. Source Evidence and Open Input-Format Detail

`v2.md` defines the build sequence and requirements. The attached challenge deck confirms the fixed-node rule, 100 m² and 5-node hub limits, six hard rules, scale formula, ranking metric, 1 mm tolerance, and the illustrated output JSON fields (slides 1–8 and speaker notes). The attached ResPlan archive provides raw floor-plan geometry in `ResPlan.pkl`, with `split.json` containing dataset indices and `croissant.json` containing metadata.

The organiser's one-plan input JSON is not present. Its exact geometry and node key names remain unknown. This does not block Phase 1 planning: define a narrow input adapter and test it with synthetic JSON matching the documented fields, while keeping the fixed-node coordinates immutable. For raw ResPlan plans, generate deterministic benchmark nodes only in a separate fixture/conversion path, as `v2.md` specifies. Confirm the adapter against an organiser sample if one arrives; do not claim that raw ResPlan data is the organiser JSON.

Corridor labels and door/open-passage representations require dataset inspection during Phases 1–2. Record observed variants and keep these parsing details behind adapters rather than changing the six hard rules.

---

*Last updated: 2026-10-15 (aligned to `v2.md` and supplied evidence)*
