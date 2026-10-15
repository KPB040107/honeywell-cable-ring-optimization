# Phase 4 Research: Lexicographic Hub Placement & Contiguous Zone Partitioning

> **Phase:** 4 (Foundation)  
> **Source:** `v2.md` Section 5.4, Slide 2, Slide 3, Slide 5, Slide 6 & Organiser Notes  
> **Key Modules:** `contracts.py`, `hub_candidates.py`, `zone_milp.py`

---

## 1. Mathematical Formulation

### 1.1 Hub Candidate Generation (Rule 6 Compliance)
- **Rule 6**: Every hub point must lie inside an eligible room assigned to that hub, at least $0.5\text{ m}$ from walls and windows ($0.5 \times \text{scale}$ drawing units). A hub and a node may share a room.
- **Safe Placement Polygon**:
  $$P_i^{\text{hub}} = R_i \setminus \mathcal{O}.\text{buffer}(0.505\text{ m})$$
  The extra $5\text{ mm}$ margin guarantees that floating point tolerances will never violate the $0.50\text{ m} \pm 1\text{ mm}$ checker rule.
- **Hanan Grid Alignment**:
  - Sample candidate hub points aligned with the $(x, y)$ coordinates of fixed node $N_i$ and the room's doorways to minimize turns ($B$) along legs from the hub.
  - Require $\|H_k - N_i\|_2 \ge 0.30\text{ m}$ so node and hub markers remain visually distinct on the floor-plan schematic overlay.

### 1.2 Connected Zone Generation & Pricing
- ResPlan plans have $\le 10$ eligible rooms (Slide 2).
- The room adjacency dual graph $G_{\text{macro}}$ from Phase 2 connects rooms through doors and open passages.
- A connected subset $Z \subseteq R_{\text{elig}}$ is a valid candidate zone if and only if:
  1. $1 \le |Z| \le 5$ (Rule 2 node capacity).
  2. $\sum_{r \in Z} \text{Area}(r) \le 100.0\text{ m}^2$ (Rule 2 area capacity).
  3. The induced subgraph $G_{\text{macro}}[Z]$ is connected via doors or open passages (Rule 3 contiguity).
- **Exact Ring Tour Pricing**:
  - For each valid connected zone $Z$ with nodes $\{N_{z, 1}, \dots, N_{z, m}\}$ ($m \le 5$) and candidate hub sites $\mathcal{H}_Z = \bigcup_{r \in Z} \mathcal{H}_r$:
  - Find the optimal hub $h^* \in \mathcal{H}_Z$ and node visiting order $\sigma$ that minimizes:
    $$J^*(Z) = \min_{h \in \mathcal{H}_Z, \sigma} \left( \text{Cost}(h \to N_{\sigma(1)}) + \sum_{i=1}^{m-1} \text{Cost}(N_{\sigma(i)} \to N_{\sigma(i+1)}) + \text{Cost}(N_{\sigma(m)} \to h) \right)$$
    using the precomputed directional pairwise cost tensor from Phase 3.
  - With $m \le 5$, the number of permutations $m! \le 120$ makes this exact evaluation instantaneous ($< 1\text{ ms}$).

### 1.3 Lexicographic Set Partitioning Optimization
- **Primary Objective**: Minimum hub count $H$.
  $$H_{\min} = \max\left(\left\lceil \frac{\sum_{i} A_i}{100.0} \right\rceil, \left\lceil \frac{N}{5} \right\rceil\right)$$
  Solve for $H = H_{\min}$. If no valid connected partition exists, increment $H \leftarrow H + 1$.
- **Secondary & Tertiary Objective**:
  Select $H$ disjoint zones $Z_1, \dots, Z_H$ covering all eligible rooms that minimize:
  $$\min \sum_{k=1}^H J^*(Z_k) + 10^{-3} \cdot \left( \max_{k} A(Z_k) - \min_{k} A(Z_k) \right)$$
- Solved using [Google OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver) or exact Set Partitioning formulation in $< 50\text{ ms}$.

---

## 2. Benchmark Case Verification (Slide 5)

- Total area: $118\text{ m}^2$, $6$ nodes.
- Lower bound: $H_{\min} = \max(\lceil 118/100 \rceil, \lceil 6/5 \rceil) = 2$ hubs.
- Optimal partition:
  - **Zone 1 (H1)**: `{Living, Bedroom 1, Bedroom 3}` $\implies \text{Area} = 40 + 16 + 15 = 71\text{ m}^2 \le 100\text{ m}^2$, $3$ nodes $\le 5$.
  - **Zone 2 (H2)**: `{Bedroom 2, Kitchen, Storage}` $\implies \text{Area} = 16 + 16 + 15 = 47\text{ m}^2 \le 100\text{ m}^2$, $3$ nodes $\le 5$.
- Both zones are connected through doors in $G_{\text{macro}}$ and satisfy all 6 rules!
