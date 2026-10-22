# Architecture Decision Records (ADRs)

**Project:** Honeywell Cable Ring Optimization  
**Date:** 23 October 2026  
**Status:** Approved & Enforced  
**Branch:** `prd-optimization-engine`

---

## ADR-001: Strict Lexicographic Objective Hierarchy

- **Context:** The official Honeywell problem statement (Slides 1, 3, 6) mandates:
  1. Minimum number of hubs $H$ (always decides first).
  2. Minimum routing cost $J = L + 5X + O + 0.5B$ in metres.
  3. Tie-break: smaller assigned-area gap $\Delta A_{\text{gap}} = \max_k A_k - \min_k A_k$ between hubs.
- **Decision:** All algorithms, reward functions, bandit utilities, and portfolio selectors must evaluate solutions strictly lexicographically:
  $$\text{Fitness}(S) = (H, J, \Delta A_{\text{gap}})$$
  Under no circumstances may an optimizer trade an additional hub ($\Delta H > 0$) for a lower routing cost ($\Delta J < 0$). Any candidate solution with higher $H$ is strictly dominated.
- **Consequences:** Prevents reward hacking and policy distortion in both heuristic and learning algorithms.

---

## ADR-002: Exact Geometric Tolerance and Independent Verification

- **Context:** Competition slides 6 and 7 specify an exact physical spatial tolerance of $1\text{ mm}$ ($0.001\text{ m}$). Lines within $1\text{ mm}$ are collinear (overlap $O$), points within $1\text{ mm}$ are coincident, and terminal snapping must be $\le 1\text{ mm}$. Self-reported metrics in JSON submissions are ignored by the evaluation checker.
- **Decision:**
  1. The independent evaluator `FloorPlanChecker` (`src/resplan_router/phase6_output/checker.py`) is the sole arbiter of validity and scoring.
  2. All geometric comparisons (containment, intersection, collinear overlap clustering, bend counting, crossing detection) are performed at $1\text{ mm}$ precision.
  3. No algorithm may bypass `FloorPlanChecker`. Every output JSON is recomputed directly from raw polyline geometry.
- **Consequences:** Eliminates discrepancy between optimizer claims and competition rankings.

---

## ADR-003: Door-Only Topological Contiguity (Hard Rule 3)

- **Context:** Slide 3 Rule 3 states: *"A hub's rooms are connected through doors or open passages only. Windows, wall-only adjacency and the entrance do not count."*
- **Decision:**
  1. The macro room adjacency graph $G_{\text{macro}}$ used for zone partitioning is built strictly from interior door portals and verified open passages.
  2. Exterior front doors (`front_door`), windows, and shared partition walls without openings are strictly non-conductive edges in $G_{\text{macro}}$.
  3. Candidate zones must form connected induced subgraphs in $G_{\text{macro}}$.
- **Consequences:** Guarantees that every generated zone is physically walkable without requiring exterior wall punch-outs.

---

## ADR-004: Candidate-Zone Completeness and Certified Minimum-Hub Search

- **Context:** In Phase 3, claiming an "optimal hub count" requires proving that the candidate zone catalog is complete with respect to the door-only adjacency graph, area limit ($\le 100\text{ m}^2$), and node limit ($\le 5$).
- **Decision:**
  1. Candidate zone enumeration must exhaustively find all connected eligible-room subsets of cardinality $k \in \{1, \dots, 5\}$ with $\sum A_r \le 100.0\text{ m}^2$.
  2. Every candidate zone must be verified for geometric hub placement feasibility (existence of an interior point in an assigned room with $\ge 0.5\text{ m}$ clearance to all walls and boundaries).
  3. Exact set partitioning is solved via Bitmask Dynamic Programming (BitmaskDP) or CP-SAT to guarantee the absolute minimum feasible $H$.
- **Consequences:** Guarantees certified global minimum hub counts for all benchmark-solvable floor plans.

---

## ADR-005: Physical Cable Routing & Obstacle Avoidance (Hard Rules 4 & 5)

- **Context:** Cables must run horizontally and vertically, stay inside the `inner` polygon, strictly avoid wall and window obstacles ($> 1\text{ mm}$ penetration is illegal), and pass between rooms exclusively through door gaps.
- **Decision:**
  1. Micro-routing utilizes an obstacle-aware Hanan grid augmented with door portal transit points and terminal offsets.
  2. A* search paths find minimum-cost rectilinear routes in free space.
  3. Ring sequencing uses exact Held-Karp dynamic programming for $n \le 5$ assigned nodes per hub (which is computationally trivial since $5! = 120$ permutations).
  4. Single-door retraces are explicitly supported and accounted for as collinear overlap $O$ on the shared line segment.
- **Consequences:** 100% compliance with Slide 3 Hard Rules 4 & 5.

---

## ADR-006: Learning Strategy (LinUCB and PPO Research Protocol)

- **Context:** Learning-based methods (LinUCB contextual bandits, PPO reinforcement learning) are research avenues, not preordained winners. Historical PPO collapsed to near-100% no-op actions due to misaligned rewards and step latency.
- **Decision:**
  1. LinUCB online bandit operator selection is the primary learning baseline, utilizing a 24-feature state vector and Sherman-Morrison rank-1 updates.
  2. PPO training will only be pursued if empirical evidence demonstrates positive headroom beyond tuned classical ALNS/Portfolio solvers.
  3. Training and validation splits must be strictly separated. No evaluation results may leak into training policies.
  4. Learned models must be benchmarked under matched wall-clock and operator budgets against strong classical baselines.
- **Consequences:** Safeguards research integrity and prevents compute wastage on pathological learning dynamics.

---

## ADR-007: Resource-Aware Scheduling and Local Hardware Utilization

- **Context:** Available hardware is an Intel Core i5-13450HX (10 cores / 16 threads) and NVIDIA RTX 4050 Laptop GPU (6 GB VRAM). Uncontrolled multi-process benchmarking or simultaneous subagent execution of heavy workloads will thrash system memory and thermal throttle.
- **Decision:**
  1. Centralized benchmark scheduling limits parallel worker processes to safe CPU core counts.
  2. GPU CUDA acceleration is reserved exclusively for batched neural network operations (PPO policy evaluation/training).
  3. Geometry operations, graph construction, exact BitmaskDP, and ALNS execute on CPU.
- **Consequences:** Maximizes throughput and prevents system crashes.
