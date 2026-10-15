# Phase 3 Research: Obstacle-Aware Axis-Aligned Micro-Router & Directional Cost Tensor

> **Phase:** 3 (Foundation)  
> **Source:** `v2.md` Section 5.3, Slide 3, Slide 6, Slide 7 & Organiser Notes  
> **Key Modules:** `contracts.py`, `rectilinear_grid.py`, `astar_rectilinear.py`, `cost_matrix.py`

---

## 1. Algorithmic Formulation

### 1.1 Axis-Aligned Free-Space Hanan Grid
- **Rule 5 Compliance**: Cable must run strictly horizontally ($\Delta y = 0$) or vertically ($\Delta x = 0$), stay strictly within `inner`, outside `wall` and `window` polygons, and pass between rooms only through door/open-passage openings.
- **Hanan Grid Principle**: An optimal rectilinear route between terminal points lies on the grid lines induced by the coordinates of the terminals and obstacle corners.
- **Grid Induction Sources**:
  1. Fixed interior node coordinates $(x(N_i), y(N_i))$ from Phase 1.
  2. Portal waypoints: threshold midpoints $p_0$, Parallel Lane A ($p_A$), Parallel Lane B ($p_B$), and room step points ($p_{A, R}, p_{B, R}$) from Phase 2.
  3. Parallel offset bypass lines: $\pm 0.04\text{ m}$ around node coordinates and door centers to allow cable bypass without collinear collision.
  4. Wall-clearance contour lines: inset $0.15\text{ m}$ from room boundaries to provide natural corridor tracks inside rooms.
- **Edge Filtering & Room Indexing**:
  - Horizontal segment $e_H = [(x_i, y_k), (x_{i+1}, y_k)]$ and vertical segment $e_V = [(x_i, y_k), (x_i, y_{k+1})]$.
  - An edge is valid if and only if $e \subset \mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$ within $1\text{ mm}$ tolerance.
  - Tag each edge with its container room ID (or portal ID) so that inter-room routing is restricted to assigned rooms and shared portals.

### 1.2 Direction-State $(v, d_{\text{in}})$ Dijkstra / $A^*$
- Slide 6 & 7 state:
  - Bends ($B$, weight $0.5$) count every direction change along the closed ring, **including at nodes and hubs**.
  - All turns are $90^\circ$ ($B = 1 \implies +0.5$).
  - A $180^\circ$ reversal counts once ($B = 1 \implies +0.5$).
  - Passing straight through counts as zero ($B = 0$).
- Standard spatial Dijkstra searches over $(v)$ and cannot price turns accurately without knowing arrival direction.
- **State-Space Formulation**:
  - Search state: $s = (v, d_{\text{in}})$ where $v = (x, y) \in V_{\text{grid}}$ and $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}, \text{None}\}$.
  - Step transition from $(u, d_{\text{in}})$ to neighbor $(v, d_{\text{out}})$ along axis-aligned edge $(u, v)$:
    $$\Delta J = \|u - v\|_1 + 0.5 \cdot \mathbb{I}(d_{\text{in}} \neq \text{None} \land d_{\text{out}} \neq d_{\text{in}}) + 1.0 \cdot \text{overlap}(u, v) + 5.0 \cdot \text{crossings}(u, v)$$
  - **Key Equivalence**: If $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$ and $d_{\text{out}} \neq d_{\text{in}}$, whether $d_{\text{out}} \perp d_{\text{in}}$ ($90^\circ$ turn) or $d_{\text{out}} = -d_{\text{in}}$ ($180^\circ$ reversal), the penalty is exactly $+0.5$ ($1$ bend)! If $d_{\text{out}} = d_{\text{in}}$, the penalty is $0.0$. This perfectly models Slide 7!

### 1.3 Pairwise Directional Cost Tensor $C(u, d_{\text{start}} \to v, d_{\text{end}})$
- For every node $u$ and $v$ (and candidate hub location), and for each departure direction $d_{\text{start}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$ and arrival direction $d_{\text{end}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$:
  - Run state-space Dijkstra starting at $(u, d_{\text{start}})$ and terminating at $(v, d_{\text{end}})$.
  - Record optimal metric length $L$, bend count $B$, cost $J = L + 0.5B$, and simplified polyline.
  - Cache results in `RectilinearRoutingIndex` for instant lookup during Phase 4 (partitioning) and Phase 5 (Held-Karp TSP).
