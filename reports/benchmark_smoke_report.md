# Honeywell Cable Ring Optimization: 5-Way Empirical Benchmark Report

**Generated:** 2026-10-22 17:54:39 UTC  
**Benchmark Mode:** `smoke`  
**Evaluated Plans:** 25 floor plans  
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
| **PortfolioHybrid** | 100.0% | 3.48 | **22.94** | 26.03 | 16.15 | 10.95m | 7.8 | 1.0 | 2.89m | 4.29s |
| **ALNS_Bandit** | 100.0% | 3.48 | **22.55** | 25.96 | 15.88 | 11.06m | 8.1 | 1.0 | 2.65m | 5.51s |
| **ALNS_Random** | 100.0% | 3.48 | **22.28** | 23.93 | 15.79 | 10.95m | 7.8 | 1.0 | 2.63m | 5.58s |
| **LinUCB_ALNS** | 100.0% | 3.48 | **21.60** | 23.93 | 15.23 | 10.90m | 7.8 | 0.8 | 2.60m | 3.52s |
| **PPO_ALNS** | 100.0% | 3.48 | **22.42** | 26.03 | 15.73 | 10.95m | 7.9 | 1.0 | 2.71m | 5.04s |

---

## 2. Head-to-Head Pairwise Comparisons & Statistical Significance

Evaluates win/tie/loss counts under competition rules, cost reduction $\Delta J = J_{\text{competitor}} - J_{\text{candidate}}$,
percentage improvements, paired Student's t-test, Wilcoxon signed-rank test, and non-parametric bootstrap 95% confidence intervals.

| Matchup (Candidate vs Competitor) | Wins | Ties | Losses | Win Rate | Non-Loss Rate | Mean $\Delta J$ | % Imp | $t$-test $p$-val | Wilcoxon $p$-val | Bootstrap 95% CI ($\Delta J$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ALNS_Bandit** vs `PortfolioHybrid` | 5 | 20 | 0 | **20.0%** | 100.0% | +0.3963 | +1.16% | 0.0620 | 0.0431 * | `[+0.08, +0.84]` |
| **ALNS_Random** vs `PortfolioHybrid` | 6 | 19 | 0 | **24.0%** | 100.0% | +0.6637 | +2.10% | 0.0405 | 0.0277 * | `[+0.16, +1.31]` |
| **LinUCB_ALNS** vs `PortfolioHybrid` | 11 | 14 | 0 | **44.0%** | 100.0% | +1.3420 | +4.70% | 0.0036 | 0.0033 * | `[+0.67, +2.15]` |
| **PPO_ALNS** vs `PortfolioHybrid` | 3 | 22 | 0 | **12.0%** | 100.0% | +0.5211 | +1.40% | 0.0858 | 0.1088 | `[+0.14, +1.14]` |
| **LinUCB_ALNS** vs `ALNS_Bandit` | 8 | 17 | 0 | **32.0%** | 100.0% | +0.9457 | +3.56% | 0.0163 | 0.0117 * | `[+0.36, +1.69]` |
| **PPO_ALNS** vs `ALNS_Bandit` | 2 | 21 | 2 | **8.0%** | 92.0% | +0.1248 | +0.24% | 0.4938 | 0.4652 | `[-0.18, +0.49]` |
| **PPO_ALNS** vs `LinUCB_ALNS` | 0 | 17 | 8 | **0.0%** | 68.0% | -0.8208 | -3.88% | 0.0284 | 0.0117 | `[-1.54, -0.28]` |

> \* Indicates statistically significant improvement ($p < 0.05$) under Wilcoxon signed-rank test.

---

## 3. Stratified Breakdown by Floor Plan Complexity Tier

Stratification based on room count complexity:
- **Compact:** $\le 3$ eligible rooms
- **Standard:** $4 - 5$ eligible rooms
- **Complex:** $\ge 6$ eligible rooms

### Tier: COMPACT (5 plans, 20.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 100.0% | 2.20 | **31.43** | 26.67 | 14.85m | 1.91s |
| **ALNS_Bandit** | 100.0% | 2.20 | **30.15** | 26.67 | 14.85m | 3.94s |
| **ALNS_Random** | 100.0% | 2.20 | **29.93** | 23.93 | 14.85m | 3.78s |
| **LinUCB_ALNS** | 100.0% | 2.20 | **29.05** | 23.93 | 14.85m | 2.69s |
| **PPO_ALNS** | 100.0% | 2.20 | **30.54** | 26.67 | 14.85m | 2.49s |

### Tier: STANDARD (19 plans, 76.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 100.0% | 3.74 | **19.63** | 21.00 | 9.38m | 4.71s |
| **ALNS_Bandit** | 100.0% | 3.74 | **19.45** | 21.00 | 9.52m | 5.80s |
| **ALNS_Random** | 100.0% | 3.74 | **19.15** | 21.00 | 9.38m | 5.93s |
| **LinUCB_ALNS** | 100.0% | 3.74 | **18.78** | 21.00 | 9.31m | 3.66s |
| **PPO_ALNS** | 100.0% | 3.74 | **19.18** | 21.00 | 9.38m | 5.60s |

### Tier: COMPLEX (1 plans, 4.0% of suite)

| Algorithm | Validity (%) | Mean $H$ | Mean $J$ | Median $J$ | Mean Length ($L$) | Mean Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | 100.0% | 5.00 | **43.46** | 43.46 | 21.30m | 8.30s |
| **ALNS_Bandit** | 100.0% | 5.00 | **43.46** | 43.46 | 21.30m | 7.86s |
| **ALNS_Random** | 100.0% | 5.00 | **43.46** | 43.46 | 21.30m | 7.98s |
| **LinUCB_ALNS** | 100.0% | 5.00 | **37.96** | 37.96 | 21.30m | 5.02s |
| **PPO_ALNS** | 100.0% | 5.00 | **43.46** | 43.46 | 21.30m | 7.28s |

---

## 4. Constraint Verification & Architectural Audit

- **100% Geometry Conformance:** Evaluated solutions pass independent polygon containment and 1 mm tolerance validation.
- **Minimum Hub Invariant:** Metaheuristic and RL operators respect the primary $H$ guarantee established by PortfolioHybrid.
- **Determinism & Reproducibility:** Fixed seeds and modular isolation ensure reproducible benchmark outcomes across runs.
