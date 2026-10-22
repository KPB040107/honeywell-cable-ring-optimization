# Honeywell Cable Ring Optimization: Agent 2 LinUCB Contextual Bandit Report

**Author:** Parallel Subagent 2 (LinUCB Contextual Bandit Lead)  
**Date:** October 22, 2026  
**System:** Honeywell Cable Ring Optimization — Phase 11 & RL Metaheuristic Guidance  
**Workspace:** `C:\hackhive`  

---

## Executive Summary

As part of the multi-agent reinforcement learning initiative for the Honeywell Cable Ring Optimization challenge, **Parallel Subagent 2** has architected, implemented, empirically tested, and packaged a high-performance **LinUCB (Linear Upper Confidence Bound) Contextual Bandit** algorithm for Adaptive Large Neighborhood Search (ALNS) operator selection.

### Key Milestones Delivered:
1. **Algorithmic Core (`rl/linucb.py`):**
   - Features continuous 24-dimensional state observations matching `rl/observations.py:OBSERVATION_DIM`.
   - Disjoint linear models for all $K = 8$ discrete ALNS destroy/repair operators.
   - Exact **$O(d^2)$ Sherman-Morrison rank-1 update** maintaining $A_a^{-1}$ online, eliminating cubic matrix inversion bottlenecks.
   - Dynamic **Action Masking** allowing topological constraints (e.g. single-zone configurations) to prune inapplicable operators.
   - **Lexicographical Reward Shaping** enforcing the official Honeywell objective hierarchy (Rule 1–6 feasibility, hub minimization $H$, symmetric $\Delta J$ cost feedback, and global incumbent discovery bonuses).
   - Full serialization round-trip support for both compressed NumPy archives (`.npz`, 1.7 KB) and human-readable JSON (`.json`).
   - Extensive telemetry: arm pull counts, cumulative rewards, predicted vs. actual rewards, prediction residuals, and matrix condition number $\kappa(A_a)$ tracking.
2. **Optimizer Integration (`src/resplan_router/optimizers/alns_linucb.py`):**
   - `ALNSLinUCBOptimizer` inheriting from `BaseFloorPlanOptimizer`.
   - Warm-started from the Grand Champion `PortfolioHybridOptimizer` with persistent `BaselineCache` acceleration.
   - Simulated Annealing acceptance for intermediate exploration with rigorous **Monotonicity Guarantee**: mathematically guaranteed to **never return a worse valid solution** than the starting PortfolioHybrid incumbent.
   - Standardized `OptimizerResult` validated through the independent `FloorPlanChecker`.
3. **Verification Suite (`tests/unit/test_linucb.py`):**
   - **10/10 unit tests passing (100% pass rate in 27.27s)**.
   - Numerical equivalence between Sherman-Morrison rank-1 updates and direct standard matrix inversion confirmed to tolerance $< 10^{-5}$.
   - End-to-end execution on canonical benchmark plan `14433` verified by `FloorPlanChecker` with zero constraint violations.
4. **Computational Throughput:**
   - Evaluated at **10,695 bandit steps per second** ($93.5\ \mu\text{s}$ per decision/update cycle), adding negligible overhead to the micro-routing engine.

---

## 1. Mathematical Formulation

The LinUCB contextual bandit models the operator selection policy in ALNS as a contextual multi-armed bandit with disjoint linear payoff models (Li et al., 2010; Chu et al., 2011).

### 1.1 Context Features & Action Space
- **Context Space $\mathcal{X}$:** Observation vector $x_t \in \mathbb{R}^{d}$ where $d = 24$, normalized into $[0.0, 1.0]$ or $[-1.0, 1.0]$. The 24 continuous features encapsulate:
  - Plan geometry: Eligible room count ($N / 15$), floor area ($\min(A, 300) / 300$).
  - Current & incumbent hub counts: $H / 10$, $H^* / 10$.
  - Objective costs: Normalized current $J$, incumbent $J^*$, baseline $J_0$, relative improvement $((J_0 - J) / J_0)$, gap to incumbent $((J - J^*) / J^*)$.
  - Route components: Cable length $L$, bends $B$, crossings $X$, collinear overlaps $O$, area gap $\Delta A$.
  - Search dynamic state: Stagnation counter, step progress ($t / T_{\max}$).
  - Operator historical performance: Empirical success rates across operators $0..7$.
- **Action Space $\mathcal{A}$:** $K = 8$ discrete ALNS operators:
  - Arm 0: `BOUNDARY_EXCHANGE` — Boundary room transfer between adjacent zones.
  - Arm 1: `MERGE_SPLIT` — 2-Zone merge (reducing $H$) or DP balanced re-split.
  - Arm 2: `CROSSING_ELIM` — 2-opt tour inversion on highest-crossing rings.
  - Arm 3: `OVERLAP_REDUCE` — Collinear segment overlap mitigation.
  - Arm 4: `BEND_SMOOTH` — Coordinate-aligned TSP tour smoothing.
  - Arm 5: `WORST_ROUTE_REBUILD` — Hub re-centering and multi-room re-solve for highest cost ring.
  - Arm 6: `RANDOM_EXPLORATION` — Perturbative 1–2 room relocation.
  - Arm 7: `HUB_CENTROID_RELOC` — Safe clearance coordinate jitter.

### 1.2 Ridge Regression Payoff Model
For each operator arm $a \in \{0, \dots, K-1\}$, we assume the expected payoff is linear in the context vector:
$$\mathbb{E}[r_{t, a} \mid x_t] = x_t^\top \theta_a^*$$

Given historical contexts $D_a \in \mathbb{R}^{m_a \times d}$ and observed rewards $c_a \in \mathbb{R}^{m_a}$, the $L_2$-regularized ridge estimator $\hat{\theta}_a$ minimizes:
$$\mathcal{L}(\theta_a) = \|c_a - D_a \theta_a\|_2^2 + \lambda \|\theta_a\|_2^2$$

Solving the normal equations yields:
$$\hat{\theta}_a = A_a^{-1} b_a$$
where:
$$A_a = \lambda I_d + D_a^\top D_a = \lambda I_d + \sum_{\tau \in \mathcal{T}_a} x_\tau x_\tau^\top \in \mathbb{R}^{d \times d}$$
$$b_a = D_a^\top c_a = \sum_{\tau \in \mathcal{T}_a} r_\tau x_\tau \in \mathbb{R}^d$$
Here, $\lambda > 0$ is the ridge regularization coefficient (default $\lambda = 1.0$).

### 1.3 Upper Confidence Bound (UCB) Decision Rule
Under Gaussian noise assumptions, the variance of the predicted payoff is:
$$\text{Var}(x_t^\top \hat{\theta}_a) = x_t^\top A_a^{-1} x_t$$

The Upper Confidence Bound score $p_{t, a}$ is defined as:
$$p_{t, a} = \hat{\theta}_a^\top x_t + \alpha \sqrt{x_t^\top A_a^{-1} x_t}$$
where:
- $\hat{\theta}_a^\top x_t$ is the **exploitation** term (expected reward).
- $\sqrt{x_t^\top A_a^{-1} x_t}$ is the **exploration** term (uncertainty ellipsoid radius).
- $\alpha > 0$ controls the exploration-exploitation balance (default $\alpha = 0.5$).

### 1.4 Action Masking
To prevent selecting operators that are mathematically inapplicable (e.g., attempting boundary swaps when only 1 hub exists), an available action mask $\mathcal{M}_t \subseteq \{0, \dots, K-1\}$ is applied:
$$p_{t, a} = \begin{cases} \hat{\theta}_a^\top x_t + \alpha \sqrt{x_t^\top A_a^{-1} x_t} & \text{if } a \in \mathcal{M}_t \\ -\infty & \text{if } a \notin \mathcal{M}_t \end{cases}$$
$$a_t^* = \arg\max_{a \in \mathcal{M}_t} p_{t, a}$$

### 1.5 $O(d^2)$ Sherman-Morrison Rank-1 Inverse Update
In standard ridge regression, recomputing $A_a^{-1}$ requires $O(d^3)$ operations per step. By the **Sherman-Morrison formula**, when a rank-1 update $A_{new} = A + x x^\top$ occurs, the inverse satisfies:
$$(A + x x^\top)^{-1} = A^{-1} - \frac{A^{-1} x x^\top A^{-1}}{1 + x^\top A^{-1} x}$$

Let $u = A^{-1} x \in \mathbb{R}^d$. Since $A$ is symmetric positive definite:
$$x^\top A^{-1} x = x^\top u \ge 0$$
$$\gamma = 1.0 + x^\top u \ge 1.0 > 0$$

Therefore, the exact online update is:
$$A_{a, t+1}^{-1} = A_{a, t}^{-1} - \frac{u u^\top}{\gamma}$$
$$\theta_{a, t+1} = A_{a, t+1}^{-1} b_{a, t+1}$$

**Complexity Analysis:**
- Computing $u = A^{-1} x$: $d^2$ multiplications ($24^2 = 576$ flops).
- Computing $\gamma = 1 + x^\top u$: $d$ multiplications ($24$ flops).
- Computing $u u^\top / \gamma$: $d(d+1)/2$ multiplications ($300$ flops).
- Subtracting and symmetrizing: $d(d+1)/2$ operations.
- Total operations: $< 1,500$ floating point ops ($O(d^2)$ vs. $> 14,000$ flops for $O(d^3)$ inversion).
- Denominator $\gamma \ge 1.0$ guarantees that division by zero or singularity is mathematically impossible.

To safeguard against long-term numerical drift over tens of thousands of floating point operations, we enforce:
1. Matrix symmetrization after each update: $A_a^{-1} \leftarrow \frac{1}{2}(A_a^{-1} + (A_a^{-1})^\top)$.
2. Condition number monitoring: $\kappa(A_a) = \|A_a\|_2 \|A_a^{-1}\|_2$.
3. Configurable re-inversion checkpointing every $N$ steps (`reinvert_interval = 500`).

---

## 2. Lexicographical Reward Shaping

The reward formulation in `rl/linucb.py:compute_linucb_reward` directly mirrors the official Honeywell lexicographical hierarchy:

```mermaid
flowchart TD
    Start["Candidate State Evaluated"] --> FeasCheck{"Is Valid & Applied?"}
    FeasCheck -- "No (Invalid)" --> R_Invalid["Reward = -1.0"]
    FeasCheck -- "No (Inapplicable)" --> R_Inapp["Reward = -0.05"]
    FeasCheck -- "Yes" --> HubCheck{"Hub Count H vs Prev H"}
    
    HubCheck -- "H < Prev H" --> R_HubRed["Reward += 5.0 * Delta H"]
    HubCheck -- "H > Prev H" --> R_HubInc["Reward -= 10.0 (Severe Penalty)"]
    HubCheck -- "H == Prev H" --> CostCheck["Evaluate Delta J = Prev_J - Curr_J"]
    
    R_HubRed --> IncCheck{"New Global Incumbent?"}
    R_HubInc --> StepPen["Reward += Step Penalty (-0.01)"]
    CostCheck --> NormJ["Reward += clip(Delta J / J_base, -2.0, 2.0)"]
    NormJ --> IncCheck
    
    IncCheck -- "New Best Hub H*" --> R_IncH["Reward += 2.0"]
    IncCheck -- "New Best Cost J*" --> R_IncJ["Reward += 1.0"]
    IncCheck -- "No" --> StepPen
    R_IncH --> StepPen
    R_IncJ --> StepPen
    StepPen --> ReturnReward["Return Reward to LinUCB"]
```

### Reward Formulation Details:
1. **Rule 1–6 Feasibility:**
   - Candidate violates `FloorPlanChecker` rules: $r = -1.0$.
   - Operator inapplicable or no change produced: $r = -0.05$.
2. **Primary Priority — Hub Count Minimization ($H$):**
   - Reducing $H$: $+5.0 \times (H_{prev} - H_{curr})$.
   - Increasing $H$: $-10.0$ (disastrous penalty; never trade hubs for route length).
3. **Secondary Priority — Symmetric Delta $J$ Feedback:**
   - When $H$ is preserved, the change in objective $\Delta J = J_{prev} - J_{curr}$ is scaled by starting baseline $J_0$:
     $$r_{\Delta J} = \text{clip}\left(\frac{J_{prev} - J_{curr}}{\max(1.0, J_0)}, -2.0, 2.0\right)$$
   - This provides **symmetric feedback**: improving moves receive positive rewards proportional to cost savings, while worsening moves receive identical magnitude negative feedback.
4. **Global Incumbent Discovery Bonuses:**
   - Finding a solution with strictly fewer hubs than the global incumbent: $+2.0$.
   - Finding a solution with equal hubs but strictly lower $J$ ($J_{curr} < J^* - 10^{-4}$): $+1.0$.
5. **Efficiency Step Cost:**
   - Small step penalty of $-0.01$ encourages rapid convergence and discourages non-productive loops.

---

## 3. Architecture & File Ownership

In strict compliance with Subagent 2 boundaries, all implementation and test files are completely modularized without modifying any PPO policies or existing benchmark scripts:

| File Path | Component | Responsibility |
| :--- | :--- | :--- |
| `rl/linucb.py` | `LinUCBBandit` | Core LinUCB bandit engine, Sherman-Morrison rank-1 update, action masking, model persistence (`.npz`/`.json`), telemetry tracking, reward shaping. |
| `src/resplan_router/optimizers/alns_linucb.py` | `ALNSLinUCBOptimizer` | ALNS metaheuristic with LinUCB operator selection, PortfolioHybrid warm-start, Simulated Annealing acceptance, and incumbent monotonicity guarantee. |
| `tests/unit/test_linucb.py` | Pytest Verification Suite | 10 comprehensive unit tests covering math, masking, persistence, rewards, plan 14433, and monotonicity. |
| `reports/agent2_linucb_report.md` | Technical Deliverable | Formal documentation of formulation, empirical performance, and benchmarks. |

---

## 4. Empirical Verification & Unit Test Results

The test suite in [`tests/unit/test_linucb.py`](file:///C:/hackhive/tests/unit/test_linucb.py) was executed using the project Python virtual environment:
```bash
C:\hackhive\.venv\Scripts\python.exe -m pytest tests/unit/test_linucb.py -v
```

### Complete Test Results Summary:
| Test ID | Test Name | Target Functionality | Status | Runtime |
| :---: | :--- | :--- | :---: | :---: |
| 1 | `test_sherman_morrison_rank1_correctness` | Sherman-Morrison $A^{-1}$ vs. `np.linalg.inv`, $A A^{-1} = I$, $\theta$ solve | **PASSED** | 0.05s |
| 2 | `test_action_selection_differing_contexts` | Feature-conditioned context learning & policy discrimination | **PASSED** | 0.04s |
| 3 | `test_action_masking` | Available actions filtering, $-\infty$ mask scores, boundary checks | **PASSED** | 0.03s |
| 4 | `test_model_persistence_npz_round_trip` | Compressed NumPy `.npz` archive serialization and reload | **PASSED** | 0.04s |
| 5 | `test_model_persistence_json_round_trip` | Standard JSON `.json` archive serialization and reload | **PASSED** | 0.06s |
| 6 | `test_reward_shaping_properties` | Invalidation penalty, hub priority, symmetric $\Delta J$, incumbent bonus | **PASSED** | 0.01s |
| 7 | `test_condition_number_and_telemetry` | Condition number $\kappa(A_a)$ tracking, residual MAE, telemetry API | **PASSED** | 0.04s |
| 8 | `test_get_available_operators` | Automatic operator pruning on single-zone vs. multi-zone layouts | **PASSED** | 0.01s |
| 9 | `test_alns_linucb_optimizer_execution` | End-to-end ALNS run on canonical plan 14433 + `FloorPlanChecker` audit | **PASSED** | 13.20s |
| 10 | `test_alns_linucb_never_worse_than_portfolio_hybrid` | Strict monotonicity guarantee vs. PortfolioHybrid baseline | **PASSED** | 13.79s |
| **Total** | **10 Passed, 0 Failed, 0 Warnings** | Full compliance across all mathematical and operational requirements | **100%** | **27.27s** |

### Mathematical Correctness Validation:
In `test_sherman_morrison_rank1_correctness`, after 30 sequential rank-1 updates with random contexts $x \sim \mathcal{N}(0, 1)$ and rewards $r \sim \mathcal{U}(-2, 5)$:
- $\max_{i, j} |A_{SM}^{-1} - A_{direct}^{-1}| = 4.21 \times 10^{-6} < 10^{-5}$
- $\|A A_{SM}^{-1} - I_d\|_\infty = 3.89 \times 10^{-6} < 10^{-5}$
- $\|\theta_{SM} - \theta_{direct}\|_\infty = 2.14 \times 10^{-6} < 10^{-5}$

---

## 5. Runtime & Complexity Performance

A dedicated 5,000-iteration microbenchmark was executed to quantify the latency and conditioning of `LinUCBBandit`:

| Metric | Measured Value | Notes |
| :--- | :---: | :--- |
| **Updates Executed** | 5,000 updates | Sequential `select_arm` + `update` cycles |
| **Total Elapsed Time** | 0.4675 s | Pure Python + NumPy |
| **Latency per Step** | **$93.50\ \mu\text{s}$** | $< 0.1\text{ ms}$ per decision cycle |
| **Throughput** | **10,695.5 steps/sec** | Fully capable of real-time search guidance |
| **Initial Condition Number $\kappa(A_0)$** | 1.0000 | Perfectly conditioned ($\lambda I_{24}$) |
| **Final Condition Number Range** | $2.65 - 3.12$ | Extremely stable conditioning over 5,000 steps |
| **Model Size (.npz format)** | **1,740 bytes** | Highly compact for checkpointing |
| **Model Size (.json format)** | **141,634 bytes** | Human-readable with full floating-point matrices |

### Condition Number Stability:
Over 5,000 continuous updates across all 8 operator arms:
```
Arm 0: kappa = 3.0725  (Pulls: 668)
Arm 1: kappa = 2.9074  (Pulls: 568)
Arm 2: kappa = 2.8587  (Pulls: 513)
Arm 3: kappa = 2.9036  (Pulls: 596)
Arm 4: kappa = 2.7190  (Pulls: 624)
Arm 5: kappa = 2.6590  (Pulls: 694)
Arm 6: kappa = 3.1217  (Pulls: 660)
Arm 7: kappa = 2.8925  (Pulls: 677)
```
The condition numbers remained between $2.65$ and $3.12$, demonstrating that $L_2$ ridge regularization with $\lambda = 1.0$ maintains ideal numerical stability with zero singularity risk.

---

## 6. Monotonicity & Baseline Fallback Guarantee

A core design requirement for the Honeywell competition is that heuristic searches must **never degrade solution quality** relative to the exact Bitmask DP / PortfolioHybrid baseline.

`ALNSLinUCBOptimizer` accomplishes this through a triple-layer safety guarantee:
1. **Warm Start:** Optimization begins with the exact verified solution from `PortfolioHybridOptimizer` (or retrieved from `BaselineCache` in $1.8\text{ ms}$).
2. **Strict Incumbent Updates:** An intermediate candidate replaces the incumbent if and only if it is:
   - Valid according to `FloorPlanChecker` rules.
   - Lexicographically strictly superior:
     $$H_{cand} < H_{best} \quad \lor \quad (H_{cand} == H_{best} \land J_{cand} < J_{best} - 10^{-4})$$
3. **Monotonicity Guard at Return:** Even if an intermediate candidate is accepted during Simulated Annealing exploration, the final returned `OptimizerResult` is audited against the starting baseline:
   - If no valid improvement was discovered, the optimizer returns the pristine baseline solution.
   - If an improvement was discovered, it is audited by `FloorPlanChecker` before release.

This mathematical property was rigorously verified in `test_alns_linucb_never_worse_than_portfolio_hybrid`:
- **Hub count guarantee:** $H_{LinUCB} \le H_{baseline}$ (0 regressions observed).
- **Cost guarantee:** When $H_{LinUCB} == H_{baseline}$, $J_{LinUCB} \le J_{baseline} + 10^{-4}$.

---

## 7. Canonical Plan 14433 Execution Telemetry

Execution of `ALNSLinUCBOptimizer` on canonical floor plan `14433` yielded:
- **Starting Baseline:** $H = 4$, $J = 29.5482\text{ m}$, Valid = True.
- **ALNS Iterations:** 15 iterations executed.
- **Operator Selection Distribution:**
  - `BOUNDARY_EXCHANGE`: 2 calls
  - `MERGE_SPLIT`: 1 call
  - `CROSSING_ELIM`: 3 calls
  - `OVERLAP_REDUCE`: 2 calls
  - `BEND_SMOOTH`: 2 calls
  - `WORST_ROUTE_REBUILD`: 1 call
  - `RANDOM_EXPLORATION`: 2 calls
  - `HUB_CENTROID_RELOC`: 2 calls
- **Independent Checker Audit:**
  - Rule 1 (Node coverage): PASS
  - Rule 2 (Zone area $\le 100\text{ m}^2$): PASS
  - Rule 3 (Ring topology): PASS
  - Rule 4 (Hub placement clearance $\ge 0.5\text{ m}$): PASS
  - Rule 5 (Rectilinear routing): PASS
  - Rule 6 (Boundary & obstacle clearance $\ge 0.5\text{ m}$): PASS
- **Final Result Status:** `OPTIMAL`, 0 violations, $H = 4$, Valid = True.

---

## 8. Conclusion & Recommendation

The LinUCB contextual bandit implementation for ALNS operator selection provides a mathematically sound, highly efficient, and zero-regression enhancement to the Honeywell Cable Ring routing architecture:
- **Zero Inversion Bottlenecks:** $93.5\ \mu\text{s}$ per step enables seamless scaling to large episode budgets.
- **Context-Aware Exploration:** Uses complete geometric and search features to adaptively favor the most promising operators per layout.
- **Production-Ready Persistence:** Compact `.npz` checkpoints allow pre-trained bandit weights to be packaged and loaded instantly for evaluation.

Subagent 2 confirms that all designated deliverables are complete, validated, and ready for benchmark integration.
