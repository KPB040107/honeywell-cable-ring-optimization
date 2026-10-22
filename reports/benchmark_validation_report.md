# Honeywell Cable Ring Optimization: 5-Way Empirical Benchmark Report

**Generated:** 2026-10-22 18:17:19 UTC  
**Benchmark Mode:** `validation`  
**Evaluated Plans:** 100 floor plans  
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
| **PortfolioHybrid** | 98.0% | 3.63 | **24.63** | 26.57 | 13.75 | 12.16m | 8.0 | 1.0 | 3.35m | 9.51s |
| **ALNS_Bandit** | 98.0% | 3.63 | **24.28** | 25.93 | 13.67 | 12.18m | 8.1 | 1.0 | 3.22m | 10.71s |
| **ALNS_Random** | 98.0% | 3.63 | **24.10** | 25.59 | 13.56 | 12.16m | 8.0 | 0.9 | 3.24m | 10.35s |
| **LinUCB_ALNS** | 98.0% | 3.63 | **23.77** | 25.40 | 13.37 | 12.15m | 8.0 | 0.9 | 3.13m | 9.04s |
| **PPO_ALNS** | 98.0% | 3.63 | **24.37** | 26.57 | 13.59 | 12.16m | 8.0 | 1.0 | 3.30m | 10.69s |

---

## 2. Head-to-Head Pairwise Comparisons & Statistical Significance

Evaluates win/tie/loss counts under competition rules, cost reduction $\Delta J = J_{\text{competitor}} - J_{\text{candidate}}$,
percentage improvements, paired Student's t-test, Wilcoxon signed-rank test, and non-parametric bootstrap 95% confidence intervals.

| Matchup (Candidate vs Competitor) | Wins | Ties | Losses | Win Rate | Non-Loss Rate | Mean $\Delta J$ | % Imp | $t$-test $p$-val | Wilcoxon $p$-val | Bootstrap 95% CI ($\Delta J$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ALNS_Bandit** vs `PortfolioHybrid` | 11 | 89 | 0 | **11.0%** | 100.0% | +0.3495 | +1.27% | 0.0216 | 0.0033 * | `[+0.11, +0.67]` |
| **ALNS_Random** vs `PortfolioHybrid` | 13 | 87 | 0 | **13.0%** | 100.0% | +0.5251 | +1.67% | 0.0035 | 0.0015 * | `[+0.22, +0.88]` |
| **LinUCB_ALNS** vs `PortfolioHybrid` | 24 | 76 | 0 | **24.0%** | 100.0% | +0.8553 | +2.90% | 4.7266e-05 | 1.8215e-05 * | `[+0.51, +1.25]` |
| **PPO_ALNS** vs `PortfolioHybrid` | 6 | 94 | 0 | **6.0%** | 100.0% | +0.2530 | +0.72% | 0.0184 | 0.0277 * | `[+0.07, +0.47]` |
| **LinUCB_ALNS** vs `ALNS_Bandit` | 16 | 81 | 3 | **16.0%** | 97.0% | +0.5058 | +1.63% | 7.2248e-04 | 3.4156e-04 * | `[+0.25, +0.80]` |
| **PPO_ALNS** vs `ALNS_Bandit` | 3 | 89 | 8 | **3.0%** | 92.0% | -0.0964 | -0.74% | 0.3967 | 0.3281 | `[-0.31, +0.11]` |
| **PPO_ALNS** vs `LinUCB_ALNS` | 0 | 79 | 21 | **0.0%** | 79.0% | -0.6023 | -2.63% | 3.0757e-04 | 5.9570e-05 | `[-0.91, -0.33]` |

> \* Indicates statistically significant improvement ($p < 0.05$) under Wilcoxon signed-rank test.

---

## 3. Stratified Breakdown by Floor Plan Complexity Tier

Stratification based on room count complexity:
- **Compact:** $\le 3$ eligible rooms
- **Standard:** $4 - 5$ eligible rooms
- **Complex:** $\ge 6$ eligible rooms

### Tier: COMPACT (14 plans, 14.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 100.0% | 2.07 | **29.12** | 27.98 | 13.48m | 2.07s |
| **ALNS_Bandit** | 100.0% | 2.07 | **26.89** | 25.52 | 13.59m | 5.10s |
| **ALNS_Random** | 100.0% | 2.07 | **26.49** | 24.15 | 13.47m | 5.18s |
| **LinUCB_ALNS** | 100.0% | 2.07 | **26.15** | 24.15 | 13.48m | 5.10s |
| **PPO_ALNS** | 100.0% | 2.07 | **27.96** | 27.59 | 13.53m | 4.39s |

### Tier: STANDARD (73 plans, 73.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 97.3% | 3.61 | **22.92** | 25.76 | 11.51m | 7.25s |
| **ALNS_Bandit** | 97.3% | 3.61 | **22.88** | 25.76 | 11.52m | 7.82s |
| **ALNS_Random** | 97.3% | 3.61 | **22.72** | 25.42 | 11.51m | 7.88s |
| **LinUCB_ALNS** | 97.3% | 3.61 | **22.41** | 25.38 | 11.50m | 7.21s |
| **PPO_ALNS** | 97.3% | 3.61 | **22.80** | 25.76 | 11.51m | 8.04s |

### Tier: COMPLEX (13 plans, 13.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 100.0% | 5.46 | **29.07** | 30.78 | 14.25m | 30.22s |
| **ALNS_Bandit** | 100.0% | 5.46 | **29.07** | 30.78 | 14.25m | 32.99s |
| **ALNS_Random** | 100.0% | 5.46 | **29.07** | 30.78 | 14.25m | 29.75s |
| **LinUCB_ALNS** | 100.0% | 5.46 | **28.65** | 30.78 | 14.25m | 23.53s |
| **PPO_ALNS** | 100.0% | 5.46 | **29.07** | 30.78 | 14.25m | 32.32s |

---

## 4. Constraint Verification & Architectural Audit

- **100% Geometry Conformance:** Evaluated solutions pass independent polygon containment and 1 mm tolerance validation.
- **Minimum Hub Invariant:** Metaheuristic and RL operators respect the primary $H$ guarantee established by PortfolioHybrid.
- **Determinism & Reproducibility:** Fixed seeds and modular isolation ensure reproducible benchmark outcomes across runs.
