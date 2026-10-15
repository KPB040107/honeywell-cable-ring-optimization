# Phase 2 Research: Door & Open-Passage Topology & Parallel-Lane Portal Waypoints

> **Phase:** 2 (Foundation)  
> **Source:** `v2.md` Section 5.2, Slide 3, Slide 5, Slide 7 & Organiser Notes  
> **Key Modules:** `contracts.py`, `portal_extractor.py`, `macro_graph.py`, `test_phase2_topology.py`

---

## 1. Topological Formulation & Constraints

### 1.1 Interior Door Detection
- ResPlan floor plans define doors as closed polygons (or line segments representing door swings/thresholds).
- **Rule 3 Contiguity**: Hub zones connect **exclusively** through interior doors or open passages. Adjacency across solid walls, windows, or the building entrance (`front_door`) is strictly invalid.
- **Extraction Protocol**:
  1. Filter out exterior doors: any door whose centroid or boundary lies within $0.05\text{ m}$ of the exterior boundary of `inner` or has category `front_door` / `entrance`.
  2. For each interior door polygon:
     - Expand/buffer by $\epsilon = 0.05\text{ m}$.
     - Query a Shapely `STRtree` populated with all room polygons (`MetricFloorPlan.rooms`).
     - Identify the intersecting rooms. If exactly 2 interior rooms intersect the buffered door polygon with non-trivial area/intersection length ($> 0.05\text{ m}$), instantiate a `door` portal linking `room1` and `room2`.
     - Extract the door threshold segment $S_{\text{door}}$ by intersecting the door polygon with the shared boundary between `room1` and `room2`.

### 1.2 Open Passage Detection
- Open passages occur where two adjacent interior rooms share a boundary inside routable free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$ without an intervening door or wall.
- **Extraction Protocol**:
  1. For each pair of adjacent rooms $(R_a, R_b)$:
     - Compute shared boundary segment: $\Gamma_{ab} = \partial R_a \cap \partial R_b$.
     - Clip to routable free space: $\Gamma_{ab}^{\text{open}} = \Gamma_{ab} \cap \mathcal{F}$.
     - Subtract any already-detected door polygons: $\Gamma_{ab}^{\text{open}} \setminus \bigcup \text{doors}$.
     - If the continuous open segment length $w \ge 0.50\text{ m}$ (standard passageway width per Slide 3), register an `open_passage` portal connecting $R_a$ and $R_b$.

### 1.3 Wall, Window, and Entrance Rejection
- Solid walls: Shared boundary segments that intersect $\text{wall}$ polygons have zero direct passage.
- Windows: Shared boundary segments intersecting $\text{window}$ polygons are strictly impassable.
- Building Entrance (`front_door`): The entrance portal leads to exterior space outside `inner` and must never link interior rooms or permit cable transit.

---

## 2. Zero-Overlap Parallel Doorway Lanes ($O = 0$)

### 2.1 The Overlap Penalty Mechanics (Slide 6 & 7)
- **Overlap Metric ($O$, weight $1.0/\text{m}$)**: Total length in metres where cable lies on top of other cable on the **same line** (within $1\text{ mm}$ lateral distance).
- **Slide 7 Invariant**: *"Does not count: parallel runs on different lines, $O = 0$."*
- **Slide 5 Note**: *"Single-door room: cable goes in and back out through the same door (two parallel runs)."*

### 2.2 Waypoint Synthesis Geometry
For each portal connecting $R_1$ and $R_2$ with threshold segment $S$ of width $w$:
- Let $p_0 = \text{midpoint}(S)$ be the threshold centerline point.
- Let $\hat{t} = \frac{p_{\text{end}} - p_{\text{start}}}{\|p_{\text{end}} - p_{\text{start}}\|}$ be the unit tangent vector along the door threshold.
- Let $\hat{n} = (-\hat{t}_y, \hat{t}_x)$ be the unit normal vector pointing from $R_1$ into $R_2$.
- Determine lateral offset:
  $$\delta = \min(0.10\text{ m}, w / 4) \ge 0.02\text{ m} \quad (20\text{ mm})$$
- Generate three transit tracks:
  1. **Centerline Track ($p_0$)**: Used for single-crossing transits between rooms.
  2. **Lane A Point ($p_A = p_0 - \delta \hat{t}$)**: Used for incoming leg into single-door leaf room.
  3. **Lane B Point ($p_B = p_0 + \delta \hat{t}$)**: Used for outgoing leg from single-door leaf room.
- Lateral separation between Lane A and Lane B is $2\delta \ge 40\text{ mm} \gg 1\text{ mm}$ (exceeding checker tolerance by $40\times$).
- **Room Entry Step Points**: For both lanes, create interior step waypoints inset $0.15\text{ m}$ into $R_1$ and $R_2$ ($p_{A, R_1} = p_A - 0.15\hat{n}$, $p_{A, R_2} = p_A + 0.15\hat{n}$) so axis-aligned routing can make clean $90^\circ$ turns within room free space without clipping wall corners.

---

## 3. Room-Portal Dual Graph $G_{\text{macro}}$ (NetworkX)

- Nodes $V = \{R_1, \dots, R_k\}$ representing all interior rooms.
- Edges $E = \{(R_i, R_j)\}$ representing valid door or open-passage portals.
- Graph properties to compute:
  - `is_connected`: Assert that the subgraph induced by eligible room nodes $\{N_1, \dots, N_n\}$ forms a single connected component.
  - `leaf_rooms`: Identify rooms with degree $\deg(R) = 1$ in $G_{\text{macro}}$ (e.g. `Bedroom 2` and `Storage` on Slide 5) that strictly require out-and-back parallel doorway runs.
