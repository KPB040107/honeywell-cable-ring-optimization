# Phase 1 Research: Scale Conversion, Polygon Healing & Node Ingestion

> **Phase:** 1 (Foundation)  
> **Source:** `v2.md` Section 5.1, Slide 4 & Organiser Notes  
> **Key Modules:** `scale_converter.py`, `parser.py`, `node_loader.py`, `contracts.py`

---

## 1. Mathematical & Algorithmic Formulation

### 1.1 Metric Scale Inversion (Slide 4)
- **Problem**: Raw ResPlan coordinates are in drawing units ($\approx 12\text{–}26\text{ units/m}$).
- **Formula**:
  $$\text{scale} = \sqrt{\frac{\text{area}(\text{inner\_poly})}{\text{plan["area"]}}}$$
- **Units**: $\text{inner\_poly.area}$ is in $\text{units}^2$, $\text{plan["area"]}$ is in $\text{m}^2 \implies \text{scale}$ is in $\text{units/m}$.
- **Transform**:
  - Point coordinates: $(x, y) \leftarrow (x / \text{scale}, y / \text{scale})$
  - Polygon areas: $A \leftarrow A / \text{scale}^2$
- **Origin**: Bounding box lower-left origin $(x_{\min}, y_{\min}) \to (0, 0)$ with $y$ pointing upwards.
- **Net Area Warning**: `plan["net_area"]` is `0` in 32% of ResPlan plans. **Never** use `net_area`.

### 1.2 Polygon Healing & Slivers (Shapely 2.x)
- ResPlan floor plans contain self-intersecting boundaries, duplicate vertices, and inverted rings.
- Use `shapely.make_valid()` on all input polygons.
- If `make_valid()` produces a `MultiPolygon` or `GeometryCollection`, extract valid `Polygon` components and retain components $\ge 2.0\text{ m}^2$ (filtering out degenerate slivers per Slide 3 benchmark note).

### 1.3 Obstacle & Free-Space Geometry
- **Obstacle Union $\mathcal{O}$**:
  $$\mathcal{O} = \text{wall} \cup \text{window} \cup \text{front\_door}$$
- **Routable Free Space $\mathcal{F}$**:
  $$\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$$
- Note: `balcony` and outdoor areas have zero overlap with `inner`.

### 1.4 Fixed Node Coordinates & Fallback
- **Official Specification**: Fixed node inputs provided in input JSON (1 interior point per eligible room, $\ge 0.5\text{ m}$ from walls).
- **Clearance Invariant**: For every node $N_i \in R_i$:
  $$\text{dist}(N_i, \mathcal{O}) \ge 0.50\text{ m} - 0.001\text{ m} \quad (\text{within } 1\text{ mm tolerance})$$
- **Synthetic/Raw Dataset Fallback**: If an input JSON does not contain pre-computed node points:
  1. Compute wall-eroded safe polygon: $P_i^{\text{safe}} = R_i \setminus \mathcal{O}.\text{buffer}(0.50)$
  2. Deterministically place node using `shapely.ops.polylabel(P_i_safe, tolerance=0.001)`.

---

## 2. Test Fixture Specifications

### Slide 4 Reference Fixture (`slide4_scale_plan.json`)
- Dimensions: $200 \times 180$ drawing units
- `inner.area`: $36000\text{ units}^2$
- `plan["area"]`: $110.0\text{ m}^2$
- `plan["net_area"]`: $0.0$ (to verify net_area immunity)
- Expected `scale`: $\sqrt{36000 / 110.0} \approx 18.09068067\text{ units/m}$
- Metric width: $200 / 18.09068 \approx 11.055\text{ m}$
- Metric height: $180 / 18.09068 \approx 9.950\text{ m}$
- Drawing unit equivalent of $0.5\text{ m}$: $0.5 \times 18.09068 \approx 9.045\text{ units}$

### Slide 5 6-Room Schematic Fixture (`slide5_schematic.json`)
- Total eligible area: $118\text{ m}^2$
- 6 eligible rooms: `Living`, `Bedroom 1`, `Bedroom 2`, `Bedroom 3`, `Kitchen`, `Storage`
- 1 excluded space: `Bathroom` (no node)
- 1 entrance: `Entrance` (`front_door`)
- 6 fixed nodes: $N_1$ to $N_6$ all $\ge 0.5\text{ m}$ from walls
