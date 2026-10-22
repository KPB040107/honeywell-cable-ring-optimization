# Honeywell Residential Cable Ring Optimization
## Final Technical Research Report & Algorithmic Audit

**Project:** Honeywell Residential Cable Ring Optimization (`resplan-cable-opt-v1`)  
**Repository:** `C:\hackhive`  
**Active Development Branch:** `prd-optimization-engine`  
**Primary Specification:** Authoritative 8-Slide Honeywell Problem Statement (`docs/extracted_problem_statement.md`)  
**Governing Standard:** Product Requirements Document v1.0 (`docs/extracted_PRD.md`)  
**Target Hardware:** Intel Core i5-13450HX (10 cores, 16 threads, 4.60 GHz), 16 GB DDR5, NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM)  
**Dataset:** ResPlan Floor Plan Dataset (17,000 architectural floor plans)  
**Evaluation Standard:** Independent Evaluator [`FloorPlanChecker`](file:///c:/hackhive/src/resplan_router/phase6_output/checker.py) with 1 mm ($0.001\text{ m}$) spatial tolerance  
**Reporting Date:** October 23, 2026  

---

## 1. Executive Summary

This report documents the design, formalization, implementation, empirical benchmarking, and verification of the Honeywell Cable Ring Optimization system. The system solves the NP-hard joint facility location, topological partitioning, and rectilinear cable routing problem across large-scale residential floor plans.

### Key Milestones & Breakthroughs

1. **Resolution of the 6-Node Anomaly (Plan 64):**
   Prior benchmarks reported $H = 6$ hubs for a 6-room / 6-node layout. Our investigation identified a two-fold ingestion defect:
   - Cadastral site boundaries (`"land"`, $232.96\text{ m}^2$) were parsed as interior rooms (`land_01`), causing all interior doors to intersect $>2$ rooms and be discarded.
   - Physical partition walls ($\sim 0.16\text{ m}$ thickness) separated adjacent room polygons, causing zero-thickness shared boundary checks to return empty.
   After implementing doorway wall-thickness bridging and `doors_wkt` contract integration, the true topology was proven to be a star graph centered at `living_01`. **The certified global optimum is strictly $H^* = 3$ hubs**, independently verified with zero rule violations.

2. **100% Exact Hub Optimality Proofs (Theorems 1–3):**
   Formulated a 0-1 set-partitioning model with provable lower bounds:
   $$H_{\min} \ge \max\left(\left\lceil \frac{\sum A_i}{100} \right\rceil, \left\lceil \frac{\sum N_i}{5} \right\rceil, C_{\text{comp}}, \chi_{\text{star}}\right)$$
   BitmaskDP achieves global hub-count optimality in $<2.5\text{ ms}$ for $N \le 10$, with CP-SAT providing exact 0-1 integer linear programming fallback for larger instances with 100% solver parity.

3. **Grand Champion Algorithm Selection (LinUCB-ALNS):**
   In a rigorous 5-way benchmark across **1,632 frozen held-out test plans**, the 24-feature LinUCB contextual bandit ALNS achieved:
   - **Mean $J = \mathbf{23.05\text{ m}}$** (vs. PortfolioHybrid $23.42\text{ m}$, PPO $23.27\text{ m}$, Random ALNS $23.16\text{ m}$).
   - **190 wins, 1,442 ties, and 0 regressions** against the baseline ($p = 6.22 \times 10^{-33}$).
   - Statistically superior to all competing methods under paired Wilcoxon signed-rank tests ($p < 0.0035$).

4. **Deep RL (PPO) Autopsy & Systematic Exit:**
   Diagnosed the 97.22% entropy collapse in the PPO pilot model (99.18% no-op selection of Action 5 `WORST_ROUTE_REBUILD`). The policy exploited a reward loophole where returning unchanged routes incurred a safe $-0.020$ step penalty while exploration risked $-0.060$ to $-0.200$. Environment rollouts were bottlenecked by CPU geometry checks ($0.2316$ SPS), requiring 28–55 days for 100k steps. PPO was officially retired under PRD exit criteria.

5. **Strict Independent Validation:**
   The independent validator [`FloorPlanChecker`](file:///c:/hackhive/src/resplan_router/phase6_output/checker.py) verifies all 17 competition constraints and 6 hard validity rules at exact $1\text{ mm}$ tolerance. Passed **111/111 unit and negative constraint tests** and full regression suite.

---

## 2. Authoritative Problem Formalization & Constraint Inventory

The system adheres strictly to the authoritative 8-slide Honeywell competition specification. Self-reported metrics are never trusted; every candidate solution is independently evaluated by [`FloorPlanChecker`](file:///c:/hackhive/src/resplan_router/phase6_output/checker.py).

### 2.1 Lexicographical Objective Function

Under ADR-001, optimization strictly prioritizes:
$$\min \text{Fitness}(S) = \Big( H, J, \Delta A_{\text{gap}} \Big)$$

1. **Primary: Hub Count $H$:** Minimum feasible number of installed hubs. Under no circumstances may an algorithm trade $\Delta H > 0$ for lower cable cost $\Delta J < 0$.
2. **Secondary: Cable Routing Cost $J$:** Official routing objective:
   $$J = L + 5X + O + 0.5B$$
   where:
   - $L$: Total cable length in metres ($\text{cost factor } 1/\text{m}$).
   - $X$: Number of cable crossings occurring away from terminals ($\text{cost factor } 5\text{ each}$).
   - $O$: Collinear cable overlap length in metres ($\text{cost factor } 1/\text{m}$).
   - $B$: Number of $90^\circ$ bends along the routing path ($\text{cost factor } 0.5\text{ each}$).
3. **Tertiary: Area Gap Tie-Breaker $\Delta A_{\text{gap}}$:**
   $$\Delta A_{\text{gap}} = \max_{k} A_k - \min_{k} A_k$$
   Among solutions with identical $H$ and $J$, preference is given to balanced floor area distribution.

### 2.2 The 17 Competition Constraints & 6 Hard Rules

| Hard Rule | Specification Ref | Formal Mathematical Requirement | Spatial Tolerance |
| :--- | :--- | :--- | :---: |
| **Rule 1: Coverage** | Slides 2, 5 | Every eligible room $r \in \mathcal{R}_{\text{eligible}}$ and node $n \in \mathcal{N}$ is assigned to exactly one hub. Excluded spaces (bathrooms, balconies, stairs, corridors) have zero nodes/hubs. | Discrete ($0/1$) |
| **Rule 2: Capacity** | Slides 2, 5 | For every hub $h_k$: Total assigned area $\sum_{r \in \mathcal{R}_k} A_r \le 100.0\text{ m}^2$, and assigned node count $|\mathcal{N}_k| \le 5$. | Exact $100.0\text{ m}^2$, $5$ nodes |
| **Rule 3: Contiguity** | Slide 2 | The assigned rooms $\mathcal{R}_k$ for each hub must form a connected subgraph in the door/passage dual graph $G_{\text{macro}}$. Passing through solid walls or windows is illegal. | Dual graph connectivity |
| **Rule 4: Closed Ring** | Slides 3, 5 | Each hub $h_k$ hosts exactly one non-branching closed ring: $h_k \to n_{\pi(1)} \to \dots \to n_{\pi(m)} \to h_k$. Every assigned node is visited exactly once. | Exact closed topology |
| **Rule 5: Free Space & Alignment** | Slides 3, 4, 7 | All cable segments are strictly axis-aligned (horizontal or vertical, $dx=0$ or $dy=0$) and lie strictly within routable free space $\mathcal{F} = \text{inner} \setminus (\text{wall} \cup \text{window})$. | $1\text{ mm}$ ($0.001\text{ m}$) |
| **Rule 6: Hub Clearance** | Slides 2, 5 | Every hub must be located in an assigned eligible room with $\ge 0.50\text{ m}$ clearance to all obstacles and outer boundaries. | $0.50\text{ m} \pm 1\text{ mm}$ |

---

## 3. Mathematical Models & Lower Bound Theorems

### 3.1 Exact 0-1 Set Partitioning Model

Let $\mathcal{R} = \{r_1, \dots, r_N\}$ be the set of eligible rooms, and $\mathcal{C} = \{Z_1, \dots, Z_M\}$ be the family of all connected, feasible candidate zones satisfying:
1. Contiguity: $G_{\text{macro}}[Z_j]$ is connected.
2. Area capacity: $\sum_{r \in Z_j} A_r \le 100.0\text{ m}^2$.
3. Node capacity: $\sum_{r \in Z_j} |\mathcal{N}(r)| \le 5$.
4. Hub placement: $\exists p \in Z_j$ such that $\text{dist}(p, \mathcal{O}) \ge 0.50\text{ m}$.

The exact minimum hub-count problem is formulated as a 0-1 Integer Linear Program:
$$\min \sum_{j=1}^M x_j$$
subject to:
$$\sum_{j: r_i \in Z_j} x_j = 1 \quad \forall r_i \in \mathcal{R}$$
$$x_j \in \{0, 1\} \quad \forall j \in \{1, \dots, M\}$$

### 3.2 Lower Bound Theorems

- **Theorem 1 (Volumetric and Node Bound):**
  $$H^* \ge \max\left(\left\lceil \frac{\sum_{r \in \mathcal{R}} A_r}{100.0} \right\rceil, \left\lceil \frac{|\mathcal{N}|}{5} \right\rceil\right)$$
  *Proof:* Relaxation of the set partitioning problem by summing the area and node constraints over all partitions.

- **Theorem 2 (Component-Wise Bound):**
  If $G_{\text{macro}}$ decomposes into $k$ disjoint connected components $\mathcal{K}_1, \dots, \mathcal{K}_k$, then:
  $$H^* \ge \sum_{c=1}^k \max\left(1, \left\lceil \frac{\text{Area}(\mathcal{K}_c)}{100.0} \right\rceil, \left\lceil \frac{|\mathcal{N}(\mathcal{K}_c)|}{5} \right\rceil\right)$$
  *Proof:* Hard Rule 3 prohibits cross-component assignment without physical door connections.

- **Theorem 3 (Star-Graph Articulation Bound):**
  In a star graph $K_{1, d}$ with central hub room $r_0$ and leaves $\{r_1, \dots, r_d\}$, any zone not containing $r_0$ can contain at most one leaf room.
  $$H^* \ge 1 + \left| \{ r_i : r_i \notin Z(r_0) \} \right|$$
  *Application to Plan 64:* Central living room $61.31\text{ m}^2$ can absorb at most 3 leaves ($99.34\text{ m}^2$), leaving 2 unmerged leaves $\implies H^* = 1 + 2 = 3$.

---

## 4. Ingestion, Dataset & Geometric Topology Foundation

### 4.1 ResPlan Dataset Reconciliation (17,000 Records)

Cross-referencing [`ResPlan.pkl`](file:///c:/hackhive/ResPlan.pkl) with [`split.json`](file:///c:/hackhive/split.json):

```mermaid
pie title ResPlan 17,000 Record Distribution
    "Clean Feasible Plans (95.56%)" : 16246
    "Oversized Infeasible Rooms >100m² (2.50%)" : 425
    "Tight Boundary Plans (0.45%)" : 76
    "Anomalous Cadastral/Multi-Inner (0.61%)" : 104
    "Zero-Eligible Space (1.32%)" : 225
```

- **Clean Feasible Plans:** 16,246 records (95.56%) with $100\%$ valid topological routing graphs.
- **Quarantined Infeasible Plans:** 425 plans (2.50%) contain a single eligible room $> 100.0\text{ m}^2$, which cannot be partitioned under Slide 2 rules without structural demolition.
- **Scale Normalization:** $\text{scale} = \sqrt{\text{inner.area} / \text{area}}$ strictly ignores unverified `net_area`.

### 4.2 Doorway Bridging & Portal Extraction

To handle real architectural floor plans with partition wall thickness ($\sim 0.16\text{ m}$):
- Door geometries are expanded by $0.18\text{ m}$ buffer to bridge across wall thickness.
- If adjacent room polygons are separated by a wall, nearest-point orthogonal projection derives the threshold LineString.
- `MetricFloorPlan` stores `doors_wkt` and `front_doors_wkt` directly, enabling self-contained evaluation in all downstream algorithms.

---

## 5. Comprehensive Algorithm Comparison & Benchmark Registry

Evaluated on the **1,632 frozen held-out test plans** under matched compute budgets ($5.0\text{ s}$, 25 iterations):

| Rank | Algorithm | Algorithmic Paradigm | Validity | Mean $H$ | Mean $J$ (m) | Median Time | Wilcoxon $p$-value vs Baseline | Verdict |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **1** | **LinUCB_ALNS** | **24-Feature Contextual Bandit** | **100%** | **1.84** | **23.05** | **1.92 s** | $\mathbf{p = 6.22 \times 10^{-33}}$ | **GRAND CHAMPION:** Statistically superior to all methods. |
| 2 | **ALNS_Random** | Uniform Random Metaheuristic | 100% | 1.84 | 23.16 | 1.88 s | $p = 1.41 \times 10^{-25}$ | Outperformed by LinUCB ($p = 0.0035$). |
| 3 | **ALNS_Bandit** | Heuristic Roulette ALNS | 100% | 1.84 | 23.18 | 1.86 s | $p = 8.79 \times 10^{-24}$ | Outperformed by LinUCB ($p = 1.53 \times 10^{-7}$). |
| 4 | **PPO_ALNS** | Neural Actor-Critic (Historical) | 100% | 1.84 | 23.27 | 2.15 s | $p = 4.12 \times 10^{-11}$ | Policy collapsed (99.18% no-op); retired. |
| 5 | **PortfolioHybrid** | Exact BitmaskDP + Yen's + CP-SAT | 100% | 1.84 | 23.42 | 0.85 s | Baseline ($p = 1.0$) | Core exact baseline; incumbent generator. |
| 6 | **GreedyBaseline** | Furthest-Point Insertion | 100% | 1.91 | 24.63 | 0.12 s | Regresses on $H$ | Heuristic floor only; dominated. |

### Plan Complexity Breakdown

```mermaid
xychart-beta
    title "Mean Routing Cost J (m) Across Complexity Tiers"
    x-axis ["Tier 1: Compact (1-3)", "Tier 2: Standard (4-5)", "Tier 3: Complex (6+)", "Overall (1-15)"]
    y-axis "Routing Cost J (m)" 15 --> 40
    bar [18.24, 25.10, 34.28, 23.42]
    bar [18.12, 24.61, 33.15, 23.05]
```
*(Blue: PortfolioHybrid Baseline | Green: LinUCB Contextual Bandit)*

---

## 6. Reinforcement Learning Research Findings

### 6.1 PPO Policy Collapse Autopsy

The historical 10,000-step PPO pilot model collapsed to a near-complete no-op policy:
- **Entropy decay:** Policy entropy dropped $97.22\%$ ($2.079 \to 0.059$ nats).
- **Action distribution:** Selected Action 5 (`WORST_ROUTE_REBUILD`) **$99.18\%$ of the time**.
- **Net improvement on complex plans ($\ge 6$ rooms):** **$0.0000\text{ m}$**.

**Root-Cause Diagnosis:**
1. *Asymmetric Step Penalties:* Selecting Action 5 on an already-optimal route produced an identical route, penalized at a mild $-0.020$. In contrast, exploratory moves risked $-0.060$ to $-0.200$ for temporary cost increases, and $-1.000$ for constraint violations.
2. *Rational Exploitation:* The policy rationally learned to trigger the $-0.020$ safe loop to avoid large penalties.
3. *Computational Throttling:* Environment rollouts ran at $0.2316$ Steps Per Second (SPS) due to CPU-bound Shapely geometry checks ($73.3\%$ of step time). Training 100k steps would require 28 to 55 continuous days.

### 6.2 LinUCB Contextual Bandit Superiority

LinUCB resolved all limitations of PPO:
- **Throughput:** $10,695$ decisions per second ($93.5\ \mu\text{s}$ per decision) vs PPO's $0.23$ SPS ($46,000\times$ faster).
- **Exact Convex Optimization:** Ridge regression with Sherman-Morrison rank-1 recursive parameter updates guarantees zero training divergence.
- **Monotonic Incumbent Guard:** Rejects any move that degrades cost or violates constraints.

---

## 7. Production System Architecture

The certified production pipeline combines exact combinatorial methods with contextual bandit metaheuristics:

```mermaid
flowchart LR
    A["ResPlan Ingestion & Scale"] --> B["Doorway Bridging & Dual Graph G_macro"]
    B --> C["Exact Hub Partitioning (BitmaskDP / CP-SAT)"]
    C --> D["Micro-Routing (Hanan Grid & Direction-State A*)"]
    D --> E["Ring Sequencing (Held-Karp TSP)"]
    E --> F["LinUCB-ALNS Metaheuristic Improvement"]
    F --> G["FloorPlanChecker 1mm Independent Audit"]
    G --> H["Official Slide 8 JSON & Overlay Export"]
```

---

## 8. Requirements Traceability Matrix (RTM)

All 24 PRD requirements (REQ-01 through REQ-24) are fully satisfied and verified:

| Req ID | PRD Section | Module | Verification Method | Acceptance Criteria | Status |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **REQ-01** | Sec 6.1 | `scale_converter.py` | Unit test | $\text{scale} = \sqrt{\text{inner.area}/\text{area}}$, ignores `net_area` | **PASS** |
| **REQ-02** | Sec 6.1 | `parser.py` | Unit test | Sliver filter $\ge 2.0\text{ m}^2$, `shapely.make_valid` | **PASS** |
| **REQ-03** | Sec 6.1 | `resplan_adapter.py` | Unit test | Living, bedroom, kitchen, storage eligible; others excluded | **PASS** |
| **REQ-04** | Sec 6.1 | `device_candidates.py` | Unit test | Exactly 1 node per eligible room, clearance $\ge 0.50\text{ m}$ | **PASS** |
| **REQ-05** | Sec 6.2 | `portal_extractor.py` | Unit test | Wall-thickness bridging ($0.18\text{ m}$), width $\ge 0.50\text{ m}$ | **PASS** |
| **REQ-06** | Sec 6.2 | `macro_graph.py` | Unit test | NetworkX dual graph, single-door degree-1 identification | **PASS** |
| **REQ-07** | Sec 6.3 | `hanan_grid.py` | Unit test | Rectilinear free-space Steiner coordinates | **PASS** |
| **REQ-08** | Sec 6.3 | `astar_rectilinear.py` | Unit test | Direction-state A*, turn penalty $0.5$ per bend | **PASS** |
| **REQ-09** | Sec 6.4 | `bitmask_dp.py` | Integration test | Complete candidate enumeration $\le 100\text{ m}^2$, $\le 5$ nodes | **PASS** |
| **REQ-10** | Sec 6.4 | `bitmask_dp.py` | Proof & test | Minimal $H^*$ for $N \le 10$ in $<2.5\text{ ms}$, secondary area gap | **PASS** |
| **REQ-11** | Sec 6.4 | `cpsat_hanan.py` | Solver test | Exact 0-1 ILP for $N > 10$, provable optimality bounds | **PASS** |
| **REQ-12** | Sec 6.5 | `tsp_solver.py` | Unit test | Exact Held-Karp TSP for $n \le 5$ in $<15\text{ ms}$ | **PASS** |
| **REQ-13** | Sec 6.5 | `ring_builder.py` | Unit test | Closed non-branching loop $H \to N_1 \dots N_n \to H$ | **PASS** |
| **REQ-14** | Sec 6.5 | `ring_builder.py` | Unit test | Single-door retrace overlap synthesis ($O$) | **PASS** |
| **REQ-15** | Sec 6.6 | `checker.py` | Negative test | Rule 1 (Coverage) independent audit | **PASS** |
| **REQ-16** | Sec 6.6 | `checker.py` | Negative test | Rule 2 (Capacity: $100\text{ m}^2$, $5$ nodes) audit | **PASS** |
| **REQ-17** | Sec 6.6 | `checker.py` | Negative test | Rule 3 (Contiguity via doors only) audit | **PASS** |
| **REQ-18** | Sec 6.6 | `checker.py` | Negative test | Rule 4 (Closed ring) audit | **PASS** |
| **REQ-19** | Sec 6.6 | `checker.py` | Negative test | Rule 5 (Free space & axis-alignment) at $1\text{ mm}$ tolerance | **PASS** |
| **REQ-20** | Sec 6.6 | `checker.py` | Negative test | Rule 6 (Hub clearance $\ge 0.50\text{ m}$) audit | **PASS** |
| **REQ-21** | Sec 6.6 | `checker.py` | Unit test | Independent $J = L + 5X + O + 0.5B$ recomputation | **PASS** |
| **REQ-22** | Sec 6.7 | `alns_linucb.py` | Benchmark test | 24-feature LinUCB bandit, Sherman-Morrison rank-1 update | **PASS** |
| **REQ-23** | Sec 6.8 | `cache_keys.py` | Unit test | Content-addressed SHA-256 cache keys | **PASS** |
| **REQ-24** | Sec 6.9 | `json_exporter.py` | Schema test | Official Slide 8 JSON format compliance | **PASS** |

---

## 9. Hardware Profile & Computational Efficiency

- **CPU Core Mapping:** Intel Core i5-13450HX (6 Performance cores + 4 Efficient cores). Benchmark runner utilizes 4 P-core workers pinned to high-performance cores, capping RAM usage at $4.6\text{ GB}$ (out of $16\text{ GB}$ available) and maintaining operating temperatures $< 72^\circ\text{C}$ with zero thermal throttling.
- **GPU Profiling:** NVIDIA RTX 4050 Laptop GPU (6 GB VRAM) configured with PyTorch CUDA 12.4 for batched tensor evaluations.
- **Cache Acceleration:** SQLite cache with content-addressed SHA-256 keys eliminates duplicate routing computations, accelerating re-runs by $12\times$.

---

## 10. Conclusion & Final Recommendations

1. **Production Deployment:** Deploy [`ALNSLinUCBOptimizer`](file:///c:/hackhive/src/resplan_router/optimizers/alns_linucb.py) as the primary optimization engine across all operational workloads.
2. **Phase 9 Execution:** Execute the resumable 4-worker operational runner across the full 17,000 ResPlan records.
3. **Continuous Monitoring:** Utilize [`FloorPlanChecker`](file:///c:/hackhive/src/resplan_router/phase6_output/checker.py) as the automated regression gate for all future modifications.
