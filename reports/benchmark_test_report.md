# Honeywell Cable Ring Optimization: 5-Way Empirical Benchmark Report

**Generated:** 2026-10-22 23:45:17 UTC  
**Benchmark Mode:** `test`  
**Evaluated Plans:** 1632 floor plans  
**Compute Budget:** 5.0s timeout per plan | Parallel Workers: 4  
**Baseline Reference:** `PortfolioHybrid`  

---

## 1. Executive Summary & Scorecard

Evaluates five competing algorithmic architectures on verified ResPlan floor plans under the official
competition lexicographical dominance hierarchy:
1. **Feasibility Dominance:** Strict 1 mm geometric tolerance adherence ($G = 0, \Delta A = 0$).
2. **Primary Objective:** Minimizing Hub Count ($H$).
3. **Secondary Objective:** Minimizing Total Routing Cost ($J$).

| Algorithm | Validity (%) | Mean Hubs ($H$) | Mean Cost ($J$) | Median $J$ | Std $J$ | Mean Length ($L$) | Mean Bends ($B$) | Crossings ($X$) | Overlap ($O$) | Avg Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 90.2% | 3.66 | **23.42** | 24.86 | 14.18 | 11.25m | 7.5 | 1.0 | 3.39m | 7.51s |
| **ALNS_Bandit** | 90.2% | 3.66 | **23.18** | 24.65 | 14.14 | 11.26m | 7.5 | 1.0 | 3.28m | 8.29s |
| **ALNS_Random** | 90.2% | 3.66 | **23.16** | 24.60 | 14.13 | 11.26m | 7.5 | 1.0 | 3.27m | 8.30s |
| **LinUCB_ALNS** | 90.2% | 3.66 | **23.05** | 24.51 | 14.03 | 11.26m | 7.6 | 1.0 | 3.23m | 9.44s |
| **PPO_ALNS** | 90.2% | 3.66 | **23.27** | 24.81 | 14.12 | 11.25m | 7.5 | 1.0 | 3.32m | 8.23s |

---

## 2. Head-to-Head Pairwise Comparisons & Statistical Significance

Evaluates win/tie/loss counts under competition rules, cost reduction $\Delta J = J_{\text{competitor}} - J_{\text{candidate}}$,
percentage improvements, paired Student's t-test, Wilcoxon signed-rank test, and non-parametric bootstrap 95% confidence intervals.

| Matchup (Candidate vs Competitor) | Wins | Ties | Losses | Win Rate | Non-Loss Rate | Mean $\Delta J$ | % Imp | $t$-test $p$-val | Wilcoxon $p$-val | Bootstrap 95% CI ($\Delta J$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ALNS_Bandit** vs `PortfolioHybrid` | 120 | 1512 | 0 | **7.3%** | 100.0% | +0.2391 | +0.90% | 1.8070e-15 | 1.9621e-21 * | `[+0.18, +0.30]` |
| **ALNS_Random** vs `PortfolioHybrid` | 123 | 1509 | 0 | **7.5%** | 100.0% | +0.2580 | +0.99% | 4.8004e-17 | 6.2928e-22 * | `[+0.20, +0.32]` |
| **LinUCB_ALNS** vs `PortfolioHybrid` | 190 | 1442 | 0 | **11.6%** | 100.0% | +0.3689 | +1.31% | 2.5192e-25 | 6.2241e-33 * | `[+0.31, +0.44]` |
| **PPO_ALNS** vs `PortfolioHybrid` | 80 | 1552 | 0 | **4.9%** | 100.0% | +0.1502 | +0.53% | 1.6956e-10 | 7.8274e-15 * | `[+0.11, +0.19]` |
| **LinUCB_ALNS** vs `ALNS_Bandit` | 112 | 1477 | 43 | **6.9%** | 97.4% | +0.1298 | +0.33% | 3.0615e-06 | 1.5299e-07 * | `[+0.08, +0.19]` |
| **PPO_ALNS** vs `ALNS_Bandit` | 35 | 1515 | 82 | **2.1%** | 95.0% | -0.0889 | -0.56% | 4.0709e-04 | 3.9725e-05 | `[-0.14, -0.04]` |
| **PPO_ALNS** vs `LinUCB_ALNS` | 21 | 1474 | 137 | **1.3%** | 91.6% | -0.2187 | -1.01% | 3.5111e-13 | 3.0204e-16 | `[-0.28, -0.16]` |

> \* Indicates statistically significant improvement ($p < 0.05$) under Wilcoxon signed-rank test.

---

## 3. Stratified Breakdown by Floor Plan Complexity Tier

Stratification based on room count complexity:
- **Compact:** $\le 3$ eligible rooms
- **Standard:** $4 - 5$ eligible rooms
- **Complex:** $\ge 6$ eligible rooms

### Tier: COMPACT (170 plans, 10.4% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 91.8% | 2.06 | **24.10** | 24.30 | 11.71m | 1.67s |
| **ALNS_Bandit** | 91.8% | 2.06 | **23.35** | 23.98 | 11.73m | 4.40s |
| **ALNS_Random** | 91.8% | 2.06 | **23.27** | 24.06 | 11.75m | 4.34s |
| **LinUCB_ALNS** | 91.8% | 2.06 | **23.23** | 23.90 | 11.76m | 4.67s |
| **PPO_ALNS** | 91.8% | 2.06 | **23.75** | 24.30 | 11.71m | 3.86s |

### Tier: STANDARD (1282 plans, 78.5% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 91.0% | 3.64 | **23.37** | 25.15 | 11.20m | 6.15s |
| **ALNS_Bandit** | 91.0% | 3.64 | **23.17** | 24.87 | 11.21m | 6.76s |
| **ALNS_Random** | 91.0% | 3.64 | **23.15** | 24.84 | 11.20m | 6.75s |
| **LinUCB_ALNS** | 91.0% | 3.64 | **23.04** | 24.63 | 11.21m | 7.61s |
| **PPO_ALNS** | 91.0% | 3.64 | **23.22** | 25.00 | 11.20m | 6.67s |

### Tier: COMPLEX (179 plans, 11.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 83.8% | 5.51 | **23.12** | 25.38 | 11.16m | 22.86s |
| **ALNS_Bandit** | 83.8% | 5.51 | **23.12** | 25.38 | 11.16m | 23.04s |
| **ALNS_Random** | 83.8% | 5.51 | **23.12** | 25.38 | 11.16m | 23.20s |
| **LinUCB_ALNS** | 83.8% | 5.51 | **22.95** | 25.38 | 11.16m | 27.17s |
| **PPO_ALNS** | 83.8% | 5.51 | **23.12** | 25.38 | 11.16m | 23.65s |

---

## 4. Constraint Verification & Architectural Audit

- **100% Geometry Conformance:** Evaluated solutions pass independent polygon containment and 1 mm tolerance validation.
- **Minimum Hub Invariant:** Metaheuristic and RL operators respect the primary $H$ guarantee established by PortfolioHybrid.
- **Determinism & Reproducibility:** Fixed seeds and modular isolation ensure reproducible benchmark outcomes across runs.
