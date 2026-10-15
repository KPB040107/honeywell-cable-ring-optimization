# Phase 5 Research: Direction-Aware Closed-Ring Sequencing & Route Synthesis

> **Phase:** 5 (Foundation)  
> **Source:** `v2.md` Section 5.5, Slide 3, Slide 5, Slide 6, Slide 7 & Slide 8  
> **Key Modules:** `contracts.py`, `exact_ring_tsp.py`, `loop_synthesizer.py`, `test_phase5_ring_sequencing.py`

---

## 1. Mathematical & Algorithmic Formulation

### 1.1 Direction-Aware Held-Karp Ring Tour Dynamic Programming
- **Scope**: Each hub serves at most $m \le 5$ nodes (Rule 2). The solution space is at most $5! = 120$ node permutations and $4^{m+1}$ arrival/departure directions.
- **Node & Hub Turn Invariant (Slide 7)**:
  - Bends ($B$, weight $0.5$) count every direction change along the closed ring, **including at nodes and hubs**.
  - A $90^\circ$ turn counts as $B = 1$ ($+0.5$ cost).
  - A $180^\circ$ reversal counts once ($B = 1$ ($+0.5$ cost)).
  - Straight-through passage counts as zero ($B = 0$).
  - The return leg back to the hub connects back to the departure leg: if $\hat{d}_{\text{hub\_end}} \neq \hat{d}_{\text{hub\_start}}$, this counts as $B = 1$ ($+0.5$ cost) at the hub.
- **Dynamic Programming State**:
  $$DP(S, u, d_{\text{in}}, d_{\text{hub\_start}})$$
  - $S \subseteq \{1, \dots, m\}$: bitmask of visited nodes.
  - $u \in \{1, \dots, m\}$: current node.
  - $d_{\text{in}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$: direction arriving at node $u$.
  - $d_{\text{hub\_start}} \in \{\text{N}, \text{S}, \text{E}, \text{W}\}$: initial departure direction from hub $H_k$.
- **Transitions**:
  - For each unvisited node $v \notin S$ and candidate departure direction $d_{\text{out}}$ from $u$:
    $$\text{cost} = DP(S, u, d_{\text{in}}, d_{\text{hub\_start}}) + 0.5 \cdot \mathbb{I}(d_{\text{out}} \neq d_{\text{in}}) + C(u, d_{\text{out}} \to v, d_{\text{arr}})$$
  - Final return step:
    $$\text{Total Cost} = DP(\{1..m\}, u, d_{\text{in}}, d_{\text{hub\_start}}) + C(u, d_{\text{out}} \to H_k, d_{\text{hub\_end}}) + 0.5 \cdot \mathbb{I}(d_{\text{hub\_end}} \neq d_{\text{hub\_start}})$$
  - Solves to exact mathematical optimality in $< 1\text{ ms}$ per hub.

### 1.2 Zero-Overlap ($O = 0$) Single-Door Leaf Routing
- Single-door leaf rooms (`Bedroom 2`, `Storage` on Slide 5) have only one doorway connecting to the rest of the plan.
- The ring must enter the room, visit the node, and exit the room through the same door threshold.
- **Parallel Lane Allocation**:
  - The incoming leg is assigned to **Door Lane A** ($p_A = p_0 - \delta \hat{t}$).
  - The outgoing leg is assigned to **Door Lane B** ($p_B = p_0 + \delta \hat{t}$).
  - Lateral separation is $2\delta \ge 40\text{ mm} \gg 1\text{ mm}$.
  - Under Slide 7's definition: *"Parallel runs on different lines (> 1 mm apart) do NOT count as overlap ($O = 0$)"*.
  - This eliminates overlap penalty entirely ($O = 0.0\text{ m}$), preventing $6\text{–}10\text{ m}$ of overlap surcharges per plan.

### 1.3 Sequential Conflict-Free Synthesis & Dynamic Penalties
- After synthesizing leg $s$ ($H_k \to N_1$), all rectilinear grid edges traversed by leg $s$ receive a dynamic overlap weight $+1.0/\text{m}$.
- All internal vertices of leg $s$ (excluding node/hub terminals) receive a crossing weight $+5.0$.
- Subsequent legs synthesize paths that avoid sharing line segments with prior legs, eliminating crossings ($X = 0$) and overlaps ($O = 0$).
- **Collinear Segment Simplification**:
  - Consecutive collinear segments $[p_1, p_2]$ and $[p_2, p_3]$ along the same direction vector are merged into $[p_1, p_3]$.
  - Guarantees that every internal vertex in exported polylines represents a true $90^\circ$ turn.

---

## 2. Target Metrics for Slide 5 Schematic

- **Hub 1 (`H1`)**:
  - Sequence: `H1 -> N1 -> N3 -> N2 -> H1`
  - Cable length $L_1 \approx 23.8\text{ m}$
  - Bends $B_1 = 8$
  - Overlap $O_1 = 0.0\text{ m}$
  - Crossings $X_1 = 0$
  - Cost $J_1 = 23.8 + 0.5 \times 8 = 27.8$
- **Hub 2 (`H2`)**:
  - Sequence: `H2 -> N4 -> N5 -> N6 -> H2`
  - Cable length $L_2 \approx 25.2\text{ m}$
  - Bends $B_2 = 12$
  - Overlap $O_2 = 0.0\text{ m}$
  - Crossings $X_2 = 0$
  - Cost $J_2 = 25.2 + 0.5 \times 12 = 31.2$
- **Total Combined Plan Metrics**:
  - $L = L_1 + L_2 = 49.0\text{ m}$
  - $B = B_1 + B_2 = 20$
  - $X = 0$
  - $O = 0.0\text{ m}$
  - Total Cost $J = 49.0 + 0.5 \times 20 = 59.0$
