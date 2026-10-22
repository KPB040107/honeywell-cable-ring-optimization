# ResPlan Optimization Benchmark Report: SMOKE
**Execution Timestamp:** 2026-10-21T09:51:55Z
**Stage:** `smoke` | **Plans Evaluated:** 5 | **Algorithms:** GreedyBaseline, BitmaskDP, CPSAT_Hanan, CPSAT_MultiPath, ALNS, GeneticAlgorithm, PortfolioHybrid
**Hardware Environment:** Intel Core i5-13450HX (10 cores, 16 threads), Concurrency: 4

---
## 1. Executive Performance Summary
| Algorithm | Total Plans | Valid Solutions | Validity Rate | Mean Hubs (H) | Mean Length L (m) | Mean Bends (B) | Mean Crossings (X) | Mean Overlap O (m) | Mean Score (J) | Mean Runtime (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| **`GreedyBaseline`** | 5 | 5 | **100.0%** | **4.2** | 13.48 | 7.2 | 1.4 | 4.91 | **28.99** | 10.953s |
| **`BitmaskDP`** | 5 | 5 | **100.0%** | **4.2** | 13.13 | 6.8 | 1.0 | 5.04 | **26.57** | 10.644s |
| **`CPSAT_Hanan`** | 5 | 5 | **100.0%** | **4.2** | 13.13 | 6.8 | 1.0 | 5.04 | **26.57** | 10.561s |
| **`CPSAT_MultiPath`** | 5 | 5 | **100.0%** | **4.2** | 13.13 | 6.8 | 1.0 | 5.04 | **26.57** | 11.826s |
| **`ALNS`** | 5 | 5 | **100.0%** | **4.2** | 13.48 | 7.0 | 1.0 | 4.91 | **26.89** | 18.311s |
| **`GeneticAlgorithm`** | 5 | 5 | **100.0%** | **4.2** | 13.48 | 7.0 | 1.0 | 4.91 | **26.89** | 43.69s |
| **`PortfolioHybrid`** | 5 | 5 | **100.0%** | **4.2** | 13.13 | 6.8 | 1.0 | 5.04 | **26.57** | 29.907s |

---
## 2. Head-to-Head Lexicographical Comparison
Under the official rules:
1. **Primary criterion:** Fewer hubs $H$ strictly wins.
2. **Secondary criterion:** For equal $H$, lower objective cost $J = L + 5X + O + 0.5B$ wins.
3. **Tertiary criterion:** For equal $H$ and $J$, smaller assigned area gap $\Delta A_{\text{gap}}$ wins.

| Metric Comparison | `GreedyBaseline` Wins | `BitmaskDP` Wins | `CPSAT_Hanan` Wins | `CPSAT_MultiPath` Wins | `ALNS` Wins | `GeneticAlgorithm` Wins | `PortfolioHybrid` Wins | Ties |
|---|---|---|---|---|---|---|---|---|
| **`GreedyBaseline` vs `BitmaskDP`** | **1** | **2** | 2 |

---
## 3. Plan-by-Plan Detailed Scorecard
| Plan ID | Eligible Rooms | Area (m²) | Algorithm | Hubs (H) | Length L (m) | Crossings (X) | Overlap O (m) | Bends (B) | Score (J) | Area Gap (m²) | Valid? | Runtime (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `6361` | 3 | 53.42 | `GreedyBaseline` | **2** | 20.1258 | 2 | 8.5719 | 9 | **43.1977** | 19.1763 | **PASS** | 0.8014s |
| `6361` | 3 | 53.42 | `BitmaskDP` | **2** | 18.4068 | 2 | 9.2028 | 8 | **41.6096** | 19.1763 | **PASS** | 0.8009s |
| `6361` | 3 | 53.42 | `CPSAT_Hanan` | **2** | 18.4068 | 2 | 9.2028 | 8 | **41.6096** | 19.1763 | **PASS** | 0.8471s |
| `6361` | 3 | 53.42 | `CPSAT_MultiPath` | **2** | 18.4068 | 2 | 9.2028 | 8 | **41.6096** | 19.1763 | **PASS** | 0.7664s |
| `6361` | 3 | 53.42 | `ALNS` | **2** | 20.1258 | 2 | 8.5719 | 9 | **43.1977** | 19.1763 | **PASS** | 9.7948s |
| `6361` | 3 | 53.42 | `GeneticAlgorithm` | **2** | 20.1258 | 2 | 8.5719 | 9 | **43.1977** | 19.1763 | **PASS** | 11.5855s |
| `6361` | 3 | 53.42 | `PortfolioHybrid` | **2** | 18.4068 | 2 | 9.2028 | 8 | **41.6096** | 19.1763 | **PASS** | 2.0017s |
| `3854` | 4 | 104.52 | `GreedyBaseline` | **3** | 13.43 | 1 | 6.7117 | 4 | **27.1417** | 34.4181 | **PASS** | 1.4295s |
| `3854` | 4 | 104.52 | `BitmaskDP` | **3** | 13.43 | 1 | 6.7124 | 4 | **27.1424** | 34.4181 | **PASS** | 1.4147s |
| `3854` | 4 | 104.52 | `CPSAT_Hanan` | **3** | 13.43 | 1 | 6.7124 | 4 | **27.1424** | 34.4181 | **PASS** | 1.42s |
| `3854` | 4 | 104.52 | `CPSAT_MultiPath` | **3** | 13.43 | 1 | 6.7124 | 4 | **27.1424** | 34.4181 | **PASS** | 1.5428s |
| `3854` | 4 | 104.52 | `ALNS` | **3** | 13.43 | 1 | 6.7124 | 4 | **27.1424** | 34.4181 | **PASS** | 9.987s |
| `3854` | 4 | 104.52 | `GeneticAlgorithm` | **3** | 13.43 | 1 | 6.7124 | 4 | **27.1424** | 34.4181 | **PASS** | 21.4378s |
| `3854` | 4 | 104.52 | `PortfolioHybrid` | **3** | 13.43 | 1 | 6.7117 | 4 | **27.1417** | 34.4181 | **PASS** | 4.3097s |
| `112` | 5 | 119.66 | `GreedyBaseline` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 4.461s |
| `112` | 5 | 119.66 | `BitmaskDP` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 4.4225s |
| `112` | 5 | 119.66 | `CPSAT_Hanan` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 4.3975s |
| `112` | 5 | 119.66 | `CPSAT_MultiPath` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 4.3945s |
| `112` | 5 | 119.66 | `ALNS` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 13.0795s |
| `112` | 5 | 119.66 | `GeneticAlgorithm` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 56.1857s |
| `112` | 5 | 119.66 | `PortfolioHybrid` | **4** | 15.7486 | 1 | 7.8722 | 6 | **31.6208** | 30.8509 | **PASS** | 11.0413s |
| `3083` | 6 | 161.65 | `GreedyBaseline` | **4** | 18.063 | 3 | 1.413 | 13 | **40.976** | 47.3181 | **PASS** | 6.8512s |
| `3083` | 6 | 161.65 | `BitmaskDP` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 6.1842s |
| `3083` | 6 | 161.65 | `CPSAT_Hanan` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 6.3534s |
| `3083` | 6 | 161.65 | `CPSAT_MultiPath` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 7.1367s |
| `3083` | 6 | 161.65 | `ALNS` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 13.8823s |
| `3083` | 6 | 161.65 | `GeneticAlgorithm` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 87.4827s |
| `3083` | 6 | 161.65 | `PortfolioHybrid` | **4** | 18.0582 | 1 | 1.4104 | 12 | **30.4686** | 47.3181 | **PASS** | 17.7505s |
| `5211` | 8 | 301.01 | `GreedyBaseline` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 41.2217s |
| `5211` | 8 | 301.01 | `BitmaskDP` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 40.4002s |
| `5211` | 8 | 301.01 | `CPSAT_Hanan` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 39.7885s |
| `5211` | 8 | 301.01 | `CPSAT_MultiPath` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 45.2918s |
| `5211` | 8 | 301.01 | `ALNS` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 44.8105s |
| `5211` | 8 | 301.01 | `GeneticAlgorithm` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 41.7567s |
| `5211` | 8 | 301.01 | `PortfolioHybrid` | **8** | 0.0136 | 0 | 0.0 | 4 | **2.0136** | 86.224 | **PASS** | 114.4343s |
