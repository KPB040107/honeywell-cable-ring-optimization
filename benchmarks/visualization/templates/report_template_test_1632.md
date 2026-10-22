# Honeywell Cable Ring Optimization: 1,632-Plan Test Split Benchmark Report

**Benchmark Mode:** `test_1632` (Unseen Test Set from `split.json: test`)  
**Evaluated Plans:** 1,632 floor plans  
**Baseline Reference:** `PortfolioHybrid`  
**Champion Candidate:** `LinUCB_ALNS`  

---

## 1. Executive Summary & Scorecard

| Algorithm | Validity (%) | Mean Hubs ($H$) | Mean Cost ($J$) | Median $J$ | Std $J$ | Mean Length ($L$) | Mean Bends ($B$) | Crossings ($X$) | Overlap ($O$) | Avg Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** | {{PH_VAL}}% | {{PH_H}} | {{PH_J}} | {{PH_MED_J}} | {{PH_STD_J}} | {{PH_L}}m | {{PH_B}} | {{PH_X}} | {{PH_O}}m | {{PH_TIME}}s |
| **ALNS_Bandit** | {{AB_VAL}}% | {{AB_H}} | {{AB_J}} | {{AB_MED_J}} | {{AB_STD_J}} | {{AB_L}}m | {{AB_B}} | {{AB_X}} | {{AB_O}}m | {{AB_TIME}}s |
| **ALNS_Random** | {{AR_VAL}}% | {{AR_H}} | {{AR_J}} | {{AR_MED_J}} | {{AR_STD_J}} | {{AR_L}}m | {{AR_B}} | {{AR_X}} | {{AR_O}}m | {{AR_TIME}}s |
| **LinUCB_ALNS** | {{LU_VAL}}% | {{LU_H}} | **{{LU_J}}** | {{LU_MED_J}} | {{LU_STD_J}} | {{LU_L}}m | {{LU_B}} | {{LU_X}} | {{LU_O}}m | {{LU_TIME}}s |
| **PPO_ALNS** | {{PP_VAL}}% | {{PP_H}} | {{PP_J}} | {{PP_MED_J}} | {{PP_STD_J}} | {{PP_L}}m | {{PP_B}} | {{PP_X}} | {{PP_O}}m | {{PP_TIME}}s |

---

## 2. Statistical Significance on Test Split

| Matchup | Wins | Ties | Losses | Win Rate | Mean $\Delta J$ | % Imp | $p$-value (Wilcoxon) | 95% Bootstrap CI |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **LinUCB_ALNS** vs `PortfolioHybrid` | {{LU_W}} | {{LU_T}} | {{LU_L}} | {{LU_WR}}% | +{{LU_DJ}} | +{{LU_PCT}}% | {{LU_PVAL}} | [{{LU_CI_LOW}}, {{LU_CI_HIGH}}] |

---

## 3. Stratified Breakdown by Tier

- Compact (≤ 3 rooms): {{TIER_COMPACT_SUMMARY}}
- Standard (4-5 rooms): {{TIER_STANDARD_SUMMARY}}
- Complex (≥ 6 rooms): {{TIER_COMPLEX_SUMMARY}}
