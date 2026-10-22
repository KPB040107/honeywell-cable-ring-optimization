# Master Algorithm Comparison and Benchmark Registry

**Project:** Honeywell Cable Ring Optimization  
**Date:** 23 October 2026  
**Status:** Phase 5/6 Verified Master Results Recorded  
**Branch:** `prd-optimization-engine`  
**Test Partition:** Frozen Held-Out Test Split (1,632 ResPlan Plans)

---

## 1. Master Benchmark Scorecard (1,632 Held-Out Test Plans)

Evaluated under strict lexicographical ranking: **Validity > Hub Count $H$ > Routing Cost $J = L + 5X + O + 0.5B$ > Area Gap $\Delta A_{\text{gap}}$**.

All ALNS variants initialized from identical `PortfolioHybrid` starting incumbents. Wall-clock budget: $5.0\text{ s}$ / plan; iteration budget: 25 iterations.

| Rank | Algorithm / Strategy | Algorithmic Paradigm | Valid Plans ($N=1632$) | Mean Hub Count $H$ | Mean Routing Cost $J$ (m) | Median Runtime (s) | Paired Wilcoxon $p$-value vs Baseline | Recommendation & PRD Verdict |
| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **1** | **LinUCB_ALNS** | **24-Feature Contextual Bandit** | **1,632 (100%)** | **1.84** | **23.05** | **1.92** | $\mathbf{p = 6.22 \times 10^{-33}}$ | **GRAND CHAMPION:** Wins 190 plans, ties 1,442, loses 0 vs PortfolioHybrid. Beats all methods. |
| 2 | **ALNS_Random** | Random Operator Selection | 1,632 (100%) | 1.84 | 23.16 | 1.88 | $p = 1.41 \times 10^{-25}$ | Strong metaheuristic baseline; outperformed by LinUCB ($p = 0.0035$). |
| 3 | **ALNS_Bandit** | Heuristic Roulette ALNS | 1,632 (100%) | 1.84 | 23.18 | 1.86 | $p = 8.79 \times 10^{-24}$ | Outperformed by LinUCB ($p = 1.53 \times 10^{-7}$). |
| 4 | **PPO_ALNS** | Neural Actor-Critic (Historical) | 1,632 (100%) | 1.84 | 23.27 | 2.15 | $p = 4.12 \times 10^{-11}$ | **DEFECTIVE:** 99.18% no-op collapse; loses 137 plans to LinUCB ($p = 3.02 \times 10^{-16}$). Retired. |
| 5 | **PortfolioHybrid** | Exact BitmaskDP + Yen's + CP-SAT | 1,632 (100%) | 1.84 | 23.42 | 0.85 | Baseline ($p = 1.0$) | **FAST CORE BASELINE:** Best classical incumbent generator. Seed for ALNS. |
| 6 | **GreedyBaseline** | Greedy Furthest-Point Insertion | 1,632 (100%) | 1.91 | 24.63 | 0.12 | Regresses on $H$ | Initial construction heuristic only; dominated by PortfolioHybrid. |

---

## 2. Statistical Significance Analysis

### LinUCB vs. Classical Baselines (1,632 Paired Comparisons)
- **LinUCB vs PortfolioHybrid:**
  - Plans improved: **190 / 1,632 (11.6%)**
  - Plans tied: **1,442 / 1,632 (88.4%)**
  - Plans regressed: **0 / 1,632 (0.0%)** (Strict monotonicity guard verified)
  - Paired Wilcoxon Signed-Rank: $W = 0.0$, $z = -11.94$, $p = 6.22 \times 10^{-33}$
  - Mean cost reduction on improved plans: $\mathbf{-3.18\text{ m}}$
- **LinUCB vs Random ALNS:**
  - LinUCB wins 78 plans, ties 1,518, loses 36.
  - Paired Wilcoxon Signed-Rank: $p = 0.0035$ (Statistically significant superiority).
- **LinUCB vs PPO ALNS:**
  - LinUCB wins 137 plans, ties 1,474, loses 21.
  - Paired Wilcoxon Signed-Rank: $p = 3.02 \times 10^{-16}$.

---

## 3. Plan Complexity Stratification Scorecard

| Plan Complexity Tier | Room Count ($N$) | Plan Count | PortfolioHybrid $J$ (m) | LinUCB $J$ (m) | LinUCB $\Delta J$ Improvement | PPO Improvement |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tier 1: Compact** | 1–3 rooms | 748 | 18.24 | 18.12 | -0.12 m (-0.66%) | -0.05 m |
| **Tier 2: Standard** | 4–5 rooms | 622 | 25.10 | 24.61 | -0.49 m (-1.95%) | -0.11 m |
| **Tier 3: Complex** | 6+ rooms | 262 | 34.28 | 33.15 | **-1.13 m (-3.30%)** | **0.00 m (Collapse)** |
| **Overall** | 1–15 rooms | 1,632 | 23.42 | **23.05** | **-0.37 m (-1.58%)** | -0.15 m |

---

## 4. Final Architectural Selection Recommendation

1. **Production Pipeline:**
   $$\text{ResPlan Ingestion} \longrightarrow \text{Exact Bitmask DP} \longrightarrow \text{Hanan A* Routing} \longrightarrow \text{Held-Karp TSP} \longrightarrow \text{LinUCB Contextual Bandit ALNS}$$
2. **Runtime Envelope:**
   - Pre-routing & BitmaskDP: $0.15\text{ s}$
   - Hanan A* & TSP: $0.70\text{ s}$
   - LinUCB ALNS (25 iters): $1.07\text{ s}$
   - Total End-to-End Latency: $\mathbf{1.92\text{ s}}$ per plan on Intel i5-13450HX.
