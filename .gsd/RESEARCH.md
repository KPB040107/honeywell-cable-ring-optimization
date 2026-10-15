# RESEARCH.md — Algorithmic Architecture & Verification Strategy

> **Project Code:** `resplan-cable-opt-v1`  
> **Source Documents:** `v2.md`, UR Hackathon 2026H2 Problem Statement 4, ResPlan Dataset (arXiv:2508.14006)

---

## 1. Technical Stack & Rationale

| Domain | Selected Technology | Alternative Considered | Selection Rationale |
|---|---|---|---|
| **Data Contracts** | Pydantic v2 | dataclasses / TypedDict | Enforces runtime type validation, serialization to exact Slide 8 JSON schema, and strict tolerance bounds at phase handshakes. |
| **Computational Geometry** | Shapely 2.x + GEOS C backend | pyclipper / custom | Native C-speed vector operations for polygon healing (`make_valid`), affine scaling, spatial indexing (`STRtree`), and safe interior pole determination (`polylabel`). |
| **Topology & Dual Graph** | NetworkX 3.x | igraph / scipy.sparse | Pythonic representation of room-portal graphs with clean sub-graph connectivity checks and portal lane routing. |
| **Combinatorial Partitioning** | Google OR-Tools CP-SAT | PuLP / SCIP | ResPlan floor plans have $\le 10$ eligible rooms and $m \le 5$ nodes per hub. CP-SAT solves the exact Single-Commodity Flow or Set Partitioning problem with ring costs to global optimality in $< 50\text{ ms}$. |
| **Ring Sequencing (TSP)** | Exact Direction-Aware Held-Karp DP | 2-opt / Concorde | With $m \le 5$ nodes per hub ($5! = 120$ permutations), Held-Karp DP with $(S, u, d_{\text{in}}, d_{\text{hub\_start}})$ directions solves to exact optimality in $< 1\text{ ms}$, incorporating bend costs at nodes and hubs. |
| **Visualization & Reporting** | Matplotlib 3.x (Agg) | Pillow / Cairo | High-fidelity vector rendering of 2-column floor-plan layouts matching Slide 5, plus red violation annotations and scorecard badges. |

---

## 2. Core Algorithmic Insights

### 2.1 The Scale Conversion Principle
Raw coordinates in the dataset are in arbitrary drawing units ($\approx 12\text{–}26\text{ units/m}$). Inverting scale once upon plan ingestion converts all geometries into true SI metres:
$$\text{scale} = \sqrt{\frac{\text{area}(\text{inner\_poly})}{\text{plan["area"]}}}$$
- `plan["area"]` is in $\text{m}^2$.
- `inner_poly.area` is in $\text{units}^2$.
- Therefore, $\text{scale}$ is in $\text{units}/\text{m}$.
- Dividing coordinates by $\text{scale}$ yields metres.
- Dividing polygon areas by $\text{scale}^2$ yields $\text{m}^2$.
- **Critical Caveat**: `net_area` must never be used because it is reported as `0` in $\approx 32\%$ of plans.

### 2.2 Eliminating Overlap ($O = 0$) in Single-Door Leaf Rooms
Slide 3 Rule 4 permits retracing a door to service a single-door room. Slide 6 & 7 define overlap $O$ as cable running on the **same line** (within $1\text{ mm}$ lateral distance).
- If an algorithm retraces the exact same centerline through a door, it incurs $O = 1.0/\text{m}$ for the retraced length.
- However, if the algorithm offsets the entry leg to **Parallel Lane A** ($p_0 - 0.02\text{ m}$) and the exit leg to **Parallel Lane B** ($p_0 + 0.02\text{ m}$), the two legs remain separated by $40\text{ mm} \gg 1\text{ mm}$.
- Because the legs are on separate lines, **Overlap $O = 0.0\text{ m}$**, saving significant penalty points without violating the door boundary.

### 2.3 Direction-Aware State-Space Pathfinding
The official checker penalizes direction changes ($B$, weight $0.5$) along the closed ring, **including at nodes and hubs**.
- A standard Euclidean TSP distance matrix ignores arrival and departure directions.
- By defining pathfinding state as $(v, d_{\text{in}})$ where $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$, a $90^\circ$ turn ($d_{\text{out}} \perp d_{\text{in}}$) and a $180^\circ$ reversal ($d_{\text{out}} = -d_{\text{in}}$) both incur $+0.5$ cost ($1$ bend).
- Passing straight through a node ($d_{\text{out}} = d_{\text{in}}$) incurs $+0.0$ cost.
- This aligns the micro-router cost directly with the checker's true objective $J = L + 5X + O + 0.5B$.

---

## 3. Investigation Plan for Flagged Verification Items

Prior to marking `SPEC.md` as `FINALIZED`, the following research tasks should be executed when official sample datasets or organizer communications are available:

1. **Input JSON Format Inspection (F-01, F-03, F-07, F-09)**:
   - Obtain or inspect a real test JSON from UR Hackathon Problem Statement 4.
   - Confirm if `nodes` array exists or if only room geometries are provided.
   - Check coordinate convention (confirm lower-left vs upper-left origin).
   - Confirm door representation (polygon vs line vs semantic tag).
   - Validate Slide 8 output schema key names.

2. **Capacity Rule Clarification (F-02, F-08)**:
   - Verify whether `max_nodes_per_hub` is 5 or 20 in the automated grading script.
   - Architectural mitigation: Expose `max_nodes_per_hub` in `SystemConfig` with a default of 5 (overridable via CLI `--max-nodes`).
   - Check if any test plan contains eligible rooms $> 100\text{ m}^2$.

3. **Room Taxonomy & Corridors (F-04)**:
   - Run a schema survey across available ResPlan files to extract all distinct values of room type/label.
   - Clarify whether corridors/hallways are treated as excluded spaces or routable transit zones.

4. **Checker Tolerance Edge Cases (F-05, F-06)**:
   - Confirm that parallel lines separated by $\ge 20\text{ mm}$ in a doorway are accepted by the official checker with $O = 0$.
   - Confirm whether two distinct rings crossing or touching at a shared node or doorway incurs $X = 0$ or $X > 0$.

---

*Last updated: 2026-10-15*
