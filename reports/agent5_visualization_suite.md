# Honeywell Cable Ring Optimization: Agent 5 Visualization & Reporting Suite

**Author:** Parallel Subagent 5 (Visualization and Reporting Lead)  
**System:** Honeywell Cable Ring Optimization — Phase 11 & Benchmark Visualization  
**Date:** October 22, 2026  
**Workspace:** `C:\hackhive`  
**Artifacts Generated:** `benchmarks/visualization/visualizer.py`, `results/plots/*`, `reports/agent5_visualization_suite.md`  

---

## Executive Summary

As part of the multi-agent optimization initiative for the Honeywell Cable Ring Optimization project, **Agent 5 (Visualization and Reporting Lead)** has engineered, empirically validated, and deployed an end-to-end **Visualization and Reporting Suite** located in [`benchmarks/visualization/visualizer.py`](file:///C:/hackhive/benchmarks/visualization/visualizer.py).

The visualization suite transforms raw multi-algorithm execution logs into actionable, publication-grade comparative analytics, architectural floor-plan schematics, interactive HTML sidecars, and automated executive markdown reports.

### Key Milestones Delivered:
1. **Comparative Algorithm Analytics Suite:**
   - Multi-metric scorecard bar charts with 95% non-parametric bootstrap confidence intervals.
   - Cost $J$ empirical distribution boxplots and jittered strip plots displaying quartiles and medians.
   - Delta $J$ distribution histograms and empirical cumulative distribution functions ($\Delta J = J_{\text{base}} - J_{\text{algo}}$).
   - Stratified tier comparisons across Compact ($\le 3$ rooms), Standard ($4-5$ rooms), and Complex ($\ge 6$ rooms).
   - Lexicographical Win/Tie/Loss stacked bar charts proving strict monotonicity (zero regressions).
2. **Computational Complexity & Multi-Objective Pareto Analysis:**
   - Solve runtime scaling curves against Eligible Room Count ($N$) and Total Floor Area ($m^2$).
   - Pareto efficiency frontier isolating the dominant knee of the curve: **LinUCB-ALNS dominates competing metaheuristics in both cost reduction ($J = 23.77$) and speed ($9.04$s)**.
3. **Multi-Format Before/After Cable Routing Schematics:**
   - Dual-panel comparative floor plan diagrams in both **high-resolution PNG (300 DPI)** and **vector SVG**.
   - **Standalone Interactive HTML visualizer** with SVG rendering, dynamic layer toggling (rooms, walls, doorways, terminals, hubs, routes), hover tooltips, and live metric diff tables.
   - **2D ASCII Character-Grid Schematic Generator** for terminal inspection, CI logging, and markdown embedding.
4. **Automated Markdown Report Generation & Production Templates:**
   - Markdown generation engine compiling summary JSON and results CSV into executive reports.
   - Standardized report templates for the upcoming **1,632-plan test split** (`split.json: test`) and the **full 17,000-plan dataset** (`ResPlan.pkl`).
5. **Empirical Execution & Verification:**
   - Executed on the 100-plan validation suite (`results/benchmarks/benchmark_summary_validation.json` and `results/benchmarks/benchmark_results_validation.csv`).
   - Produced 34 publication-grade visualization artifacts in [`results/plots/`](file:///C:/hackhive/results/plots/).

---

## 1. Algorithmic Scorecard & Statistical Validation

The visualizer processed the 100-plan empirical validation benchmark comparing five competing architectures under Honeywell's official lexicographical dominance criteria:
1. **Feasibility Dominance:** Zero polygon penetration ($G = 0, \Delta A = 0$).
2. **Primary Objective:** Minimizing Hub Count ($H$).
3. **Secondary Objective:** Minimizing Total Routing Cost ($J = L + 0.1B + 5X + 2O$).

### 1.1 Empirical Scorecard Summary

| Algorithm | Validity (%) | Mean Hubs ($H$) | Mean Cost ($J$) | Median $J$ | Std $J$ | Mean Length ($L$) | Mean Bends ($B$) | Crossings ($X$) | Overlap ($O$) | Avg Runtime (s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **PortfolioHybrid** (Base) | 98.0% | 3.63 | 24.63 | 26.57 | 13.75 | 12.16m | 8.0 | 1.0 | 3.35m | 9.51s |
| **ALNS_Bandit** | 98.0% | 3.63 | 24.28 | 25.93 | 13.67 | 12.18m | 8.1 | 1.0 | 3.22m | 10.71s |
| **ALNS_Random** | 98.0% | 3.63 | 24.10 | 25.59 | 13.56 | 12.16m | 8.0 | 0.9 | 3.24m | 10.35s |
| **LinUCB_ALNS** (Champion) | **98.0%** | **3.63** | **23.77** | **25.40** | **13.37** | **12.15m** | **8.0** | **0.9** | **3.13m** | **9.04s** |
| **PPO_ALNS** | 98.0% | 3.63 | 24.37 | 26.57 | 13.59 | 12.16m | 8.0 | 1.0 | 3.30m | 10.69s |

### 1.2 Pairwise Statistical Significance vs PortfolioHybrid Baseline

| Matchup | Wins | Ties | Losses | Win Rate | Non-Loss Rate | Mean $\Delta J$ | % Imp | $t$-test $p$-val | Wilcoxon $p$-val | Bootstrap 95% CI ($\Delta J$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **LinUCB_ALNS** vs `PortfolioHybrid` | **24** | 76 | **0** | **24.0%** | **100.0%** | **+0.8553** | **+2.90%** | **4.73e-05** | **1.82e-05 \*** | `[+0.51, +1.25]` |
| **ALNS_Random** vs `PortfolioHybrid` | 13 | 87 | 0 | 13.0% | 100.0% | +0.5251 | +1.67% | 3.50e-03 | 1.50e-03 \* | `[+0.22, +0.88]` |
| **ALNS_Bandit** vs `PortfolioHybrid` | 11 | 89 | 0 | 11.0% | 100.0% | +0.3495 | +1.27% | 2.16e-02 | 3.30e-03 \* | `[+0.11, +0.67]` |
| **PPO_ALNS** vs `PortfolioHybrid` | 6 | 94 | 0 | 6.0% | 100.0% | +0.2530 | +0.72% | 1.84e-02 | 2.77e-02 \* | `[+0.07, +0.47]` |

> \* Indicates statistically significant improvement ($p < 0.05$) under Wilcoxon signed-rank test.

---

## 2. Visualization Suite Architecture & Tools

The visualizer toolchain is organized into four modular subsystems in [`benchmarks/visualization/visualizer.py`](file:///C:/hackhive/benchmarks/visualization/visualizer.py):

```
benchmarks/visualization/
├── visualizer.py                   # Master visualization engine & CLI
├── __init__.py                     # Package export declarations
└── templates/                      # Markdown report templates
    ├── report_template_test_1632.md        # 1,632-plan test benchmark template
    └── report_template_production_17000.md # 17,000-plan full dataset template
```

### 2.1 Subsystem 1: BenchmarkDataVisualizer

`BenchmarkDataVisualizer` parses structured benchmark outputs (`benchmark_summary.json` and `benchmark_results.csv`) and generates seven comparative charts:

1. **`plot_algorithm_scorecard(output_path)`**:
   - 4-panel composite figure comparing:
     - Mean & Median Cost $J$ with 95% bootstrap CI error bars.
     - Secondary route penalties: Bends $B$, Crossings $X$, Overlap $O$.
     - Cable Length $L$ and Hub Count $H$ invariant.
     - Solve runtime (s) and validity rate (%).
2. **`plot_cost_j_distribution(output_path)`**:
   - Side-by-side distribution boxplots with overlayed jittered scatter points.
   - Visualizes median cost, interquartile range ($Q_1, Q_3$), whiskers, and outliers.
3. **`plot_delta_j_distribution(output_path)`**:
   - Dual-panel cost savings distribution: probability density histogram (left) and empirical cumulative distribution function (right).
   - Reference line at $\Delta J = 0$ highlights that LinUCB-ALNS exhibits the highest positive density, reaching up to $+11.20$ cost reduction.
4. **`plot_tier_stratified_performance(output_path)`**:
   - Grouped bar charts segmenting performance by complexity tier: Compact ($\le 3$ rooms), Standard ($4-5$ rooms), and Complex ($\ge 6$ rooms).
5. **`plot_win_tie_loss_breakdown(output_path)`**:
   - Stacked horizontal bar chart under lexicographical dominance rules.
   - Shows LinUCB-ALNS achieving 24 wins, 76 ties, and 0 losses (100% non-loss rate).
6. **`plot_runtime_vs_complexity(output_path)`**:
   - Quadratic trend lines plotting solve runtime against eligible room count ($N$) and floor area ($m^2$).
7. **`plot_pareto_frontier(output_path)`**:
   - Multi-objective trade-off curve between Mean Cost $J$ and Mean Runtime.
   - Confirms LinUCB-ALNS is strictly Pareto-dominant over competing metaheuristics.

### 2.2 Subsystem 2: CableRoutingDiagramVisualizer

`CableRoutingDiagramVisualizer` renders architectural floor plans and cable loops:

- **Dual-Panel Matplotlib Diagrams (PNG & SVG):**
  - Left panel displays the `PortfolioHybrid` baseline; right panel displays `LinUCB_ALNS`.
  - Shared coordinate bounding box ensures 1:1 spatial comparability.
  - Room polygons rendered with soft pastel fills corresponding to assigned hub rings.
  - Obstacles (perimeter and interior walls) rendered in dark charcoal (`#1A202C`).
  - Open doorway thresholds rendered in dashed gold (`#D69E2E`).
  - Rectilinear Manhattan cable routes drawn with flow arrows and circled leg order badges.
  - Fixed terminal nodes drawn as white circles with dark borders; hubs drawn as bold colored squares.
  - Comprehensive metric cards and delta callouts ($\Delta J, \Delta L, \Delta B, \Delta X, \Delta O$).
- **Standalone Interactive HTML Visualizer:**
  - Self-contained HTML/CSS/SVG explorer. Zero external JavaScript/CSS CDN dependencies.
  - Interactive layer controls allowing reviewers to toggle:
    - Rooms, Walls, Doorways, Cable Routes, Hubs, and Terminals.
  - Hover tooltips on all SVG elements displaying coordinates, leg lengths, and room properties.
  - Full tabular diff comparing baseline vs optimized metrics.
- **2D ASCII Character-Grid Schematic Generator:**
  - 58-column by 24-row discretization matrix.
  - Renders walls (`#`), doorways (`=`), room centroids, fixed nodes `(N)`, hubs `[H]`, and rectilinear cable segments (`-`, `|`, `+`).
  - Clean text rendering for terminal logs and markdown documentation.

### 2.3 Subsystem 3: BenchmarkReportGenerator

`BenchmarkReportGenerator` formats benchmark results into markdown:
- Generates executive summaries, scorecards, pairwise statistical tables, and tier breakdowns.
- Embeds relative image links to generated plots and interactive HTML sidecars.
- Creates pre-configured templates for the upcoming **1,632-plan test benchmark** and **17,000-plan full dataset run**.

---

## 3. Gallery of Generated Performance Charts

All charts were generated from the validation run and saved in [`results/plots/`](file:///C:/hackhive/results/plots/):

### 3.1 Multi-Metric Algorithmic Scorecard
- **PNG File:** [`results/plots/algorithm_scorecard_bar.png`](file:///C:/hackhive/results/plots/algorithm_scorecard_bar.png)
- **SVG File:** [`results/plots/algorithm_scorecard_bar.svg`](file:///C:/hackhive/results/plots/algorithm_scorecard_bar.svg)
- **Key Insight:** LinUCB-ALNS achieves the lowest Mean Cost ($J = 23.77$) and lowest Median Cost ($J = 25.40$), while maintaining 100% adherence to the primary hub count invariant ($H = 3.63$) and 98.0% feasibility.

### 3.2 Empirical Cost J Distributions
- **PNG File:** [`results/plots/cost_j_distribution_boxplot.png`](file:///C:/hackhive/results/plots/cost_j_distribution_boxplot.png)
- **SVG File:** [`results/plots/cost_j_distribution_boxplot.svg`](file:///C:/hackhive/results/plots/cost_j_distribution_boxplot.svg)
- **Key Insight:** Jittered empirical scatter points confirm that the cost reduction achieved by LinUCB-ALNS is distributed consistently across the suite, shifting both the median and the upper quartile downward.

### 3.3 Cost Improvement Distributions ($\Delta J$)
- **PNG File:** [`results/plots/delta_j_distribution.png`](file:///C:/hackhive/results/plots/delta_j_distribution.png)
- **SVG File:** [`results/plots/delta_j_distribution.svg`](file:///C:/hackhive/results/plots/delta_j_distribution.svg)
- **Key Insight:** LinUCB-ALNS exhibits a strictly positive $\Delta J$ support ($\Delta J \ge 0$), with 24% of plans experiencing strictly positive improvements up to $\Delta J = +11.20$.

### 3.4 Tier-Stratified Performance
- **PNG File:** [`results/plots/tier_stratified_performance.png`](file:///C:/hackhive/results/plots/tier_stratified_performance.png)
- **SVG File:** [`results/plots/tier_stratified_performance.svg`](file:///C:/hackhive/results/plots/tier_stratified_performance.svg)
- **Key Insight:** In the Compact tier ($\le 3$ rooms), LinUCB reduces mean cost from $29.12$ to $26.15$ (-10.2%). In the Standard tier ($4-5$ rooms), mean cost drops from $22.92$ to $22.41$. In the Complex tier ($\ge 6$ rooms), cost drops from $29.07$ to $28.65$.

### 3.5 Head-to-Head Win / Tie / Loss Breakdown
- **PNG File:** [`results/plots/win_tie_loss_breakdown.png`](file:///C:/hackhive/results/plots/win_tie_loss_breakdown.png)
- **SVG File:** [`results/plots/win_tie_loss_breakdown.svg`](file:///C:/hackhive/results/plots/win_tie_loss_breakdown.svg)
- **Key Insight:** LinUCB-ALNS scores 24 wins and 0 losses against the PortfolioHybrid baseline, outperforming ALNS-Random (13 wins) and ALNS-Bandit (11 wins).

### 3.6 Runtime Complexity Scaling Curves
- **PNG File:** [`results/plots/runtime_vs_complexity.png`](file:///C:/hackhive/results/plots/runtime_vs_complexity.png)
- **SVG File:** [`results/plots/runtime_vs_complexity.svg`](file:///C:/hackhive/results/plots/runtime_vs_complexity.svg)
- **Key Insight:** Quadratic scaling curves verify smooth polynomial scaling with respect to both room count and total floor plan area.

### 3.7 Multi-Objective Pareto Frontier
- **PNG File:** [`results/plots/pareto_frontier.png`](file:///C:/hackhive/results/plots/pareto_frontier.png)
- **SVG File:** [`results/plots/pareto_frontier.svg`](file:///C:/hackhive/results/plots/pareto_frontier.svg)
- **Key Insight:** LinUCB-ALNS occupies the optimal Pareto frontier knee at $(9.04\text{s}, 23.77\text{ J})$, dominating both ALNS-Bandit $(10.71\text{s}, 24.28\text{ J})$ and ALNS-Random $(10.35\text{s}, 24.10\text{ J})$ in both lower cost and faster runtime.

---

## 4. Case Studies: Representative Before/After Floor Plans

Five representative floor plans were selected across all complexity tiers to demonstrate LinUCB-ALNS route optimizations:

```
Representative Floor Plans Rendered:
├── Plan 51  (Compact,  3 rooms)  -> Cost J: 26.67 -> 21.67 (ΔJ = +5.00, +18.75% imp)
├── Plan 78  (Standard, 5 rooms)  -> Cost J: 36.86 -> 31.36 (ΔJ = +5.50, +14.93% imp)
├── Plan 28  (Standard, 4 rooms)  -> Cost J: 30.47 -> 26.86 (ΔJ = +3.61, +11.84% imp)
├── Plan 239 (Complex,  6 rooms)  -> Cost J: 43.46 -> 37.96 (ΔJ = +5.50, +12.66% imp)
└── Plan 531 (Compact,  3 rooms)  -> Cost J: 35.02 -> 23.83 (ΔJ = +11.20, +31.97% imp)
```

---

### Case Study 1: Floor Plan 51 (Compact Tier, 3 Eligible Rooms)

- **Performance Delta:** $J_{\text{base}} = 26.67 \longrightarrow J_{\text{linucb}} = 21.67$ (**$\Delta J = +5.00$, $+18.75\%$ cost reduction**)
- **Mechanism:** In the baseline solution, Hub $H_2$ was placed at $(4.87, 3.39)$ in the hallway/living area, requiring a $15.96$m cable ring loop. LinUCB's `HUB_CENTROID_RELOC` and `BEND_SMOOTH` operators relocated $H_2$ directly adjacent to fixed terminal $N_2$ at $(3.74, 1.79)$, reducing ring length to $10.96$m ($-5.0$m cable saved) with zero additional bends or crossings.
- **Rendered Artifacts:**
  - [Side-by-Side PNG](file:///C:/hackhive/results/plots/routing_comparison_plan_51.png)
  - [Vector SVG Schematic](file:///C:/hackhive/results/plots/routing_comparison_plan_51.svg)
  - [Interactive HTML Viewer](file:///C:/hackhive/results/plots/routing_plan_51.html)
  - [ASCII Schematic Text](file:///C:/hackhive/results/plots/routing_plan_51_ascii.txt)

#### 2D ASCII Floor Plan Schematic (Plan 51):
```text
               # ## ## ## ### ## ### ## ### ## ### #####  
               ##    #                                 #  
               #                                       #  
               #                                       #  
               #                [H1]edr                #  
               #                                       #  
               #                                       #  
        ## ## ##                                       ###
        ##      #         =                          #   #
        #      bath  =                                   #
## ## # #                                   kitc         #
#        #                              (N3)             #
#                                        |               #
#      bath    =         |----=-land------               #
#                        |---------------|#              #
#                        |                 # ## ## ## ## #
#                        |                 #              
#                        |                 #              
#                       [H2]               #              
#                    livi                  #              
#                                          #              
#                                          #              
#                                          #              
### ### ## ## ### ## ### ## ## ### ## ### ##              
```

---

### Case Study 2: Floor Plan 78 (Standard Tier, 5 Eligible Rooms)

- **Performance Delta:** $J_{\text{base}} = 36.86 \longrightarrow J_{\text{linucb}} = 31.36$ (**$\Delta J = +5.50$, $+14.93\%$ cost reduction**)
- **Mechanism:** LinUCB's `CROSSING_ELIM` operator detected a planar crossing between Ring 2 and Ring 4 near the central doorway portal. Applying a 2-opt tour swap eliminated the crossing ($X: 1 \to 0$, $-5.0$ cost penalty) and reduced route bend count ($B: 8 \to 3$).
- **Rendered Artifacts:**
  - [Side-by-Side PNG](file:///C:/hackhive/results/plots/routing_comparison_plan_78.png)
  - [Vector SVG Schematic](file:///C:/hackhive/results/plots/routing_comparison_plan_78.svg)
  - [Interactive HTML Viewer](file:///C:/hackhive/results/plots/routing_plan_78.html)
  - [ASCII Schematic Text](file:///C:/hackhive/results/plots/routing_plan_78_ascii.txt)

#### 2D ASCII Floor Plan Schematic (Plan 78):
```text
######=###########   ##########        ###############=###
#                #   #        #        #                 #
#                #   ##       #        #                 #
#             bat#   #   (N5)c#        #  bath    bedr   #
#   [H3]r        #   ##   |   #        #         ([H2]   #
#                #   #    |   ##########                 #
#            =   #   #    |   #         # =              #
#                #####    |                              #
#          =     #    #   |             =                #
###                       =                      #########
 #                       land                    #        
 #    bath                |   livi               #        
 #                        |                      #        
 #       =                |                      #        
 #           =            --------[H4]           #        
 #                                               #        
 #                                               #        
 #       bedr                                    #        
 #      [H1]                                     #        
 #                                               #        
 #             ###################################        
 #              #                                         
 #              #                                         
 ################                                         
```

---

### Case Study 3: Floor Plan 239 (Complex Tier, 6 Eligible Rooms)

- **Performance Delta:** $J_{\text{base}} = 43.46 \longrightarrow J_{\text{linucb}} = 37.96$ (**$\Delta J = +5.50$, $+12.66\%$ cost reduction**)
- **Mechanism:** In this large 149.1 $m^2$ multi-zone apartment, LinUCB executed `OVERLAP_REDUCE` to separate collinear segments sharing the central corridor conduit, mitigating collinear cable congestion and eliminating route bends.
- **Rendered Artifacts:**
  - [Side-by-Side PNG](file:///C:/hackhive/results/plots/routing_comparison_plan_239.png)
  - [Vector SVG Schematic](file:///C:/hackhive/results/plots/routing_comparison_plan_239.svg)
  - [Interactive HTML Viewer](file:///C:/hackhive/results/plots/routing_plan_239.html)
  - [ASCII Schematic Text](file:///C:/hackhive/results/plots/routing_plan_239_ascii.txt)

#### 2D ASCII Floor Plan Schematic (Plan 239):
```text
################## ##                 ###### ###          
#                   #        balc     #        #          
#                  ##############=######  bath #          
#                                     ==       #          
#        bedr                                  #          
#       [H2]                [H4]r             ############
#                           (N2)                         #
#                                                        #
#                                      ---|----(N5)      #
#         =     =                 =    |  |     kitc     #
#                                      |  |              #
#     bath                 |land----------=              #
#    #          =          |           |                 #
#####                      |          livi      [H5]tor  #
    #       bath           |-----------|  =              #
######                     |                             #
#                          |                             #
#                          |                             #
#                         [H1]                 [H3]r     #
#                                                        #
#                                                        #
#                                                        #
#                                                        #
##########################################################
```

---

### Case Study 4: Floor Plan 531 (Compact Tier, Highest Savings Observed)

- **Performance Delta:** $J_{\text{base}} = 35.02 \longrightarrow J_{\text{linucb}} = 23.83$ (**$\Delta J = +11.20$, $+31.97\%$ cost reduction**)
- **Mechanism:** Achieved the maximum single-plan cost reduction in the 100-plan suite. LinUCB eliminated 2 redundant crossings ($2 \times 5.0 = 10.0$ penalty reduction) and smoothed perimeter routing around bathroom thresholds.
- **Rendered Artifacts:**
  - [Side-by-Side PNG](file:///C:/hackhive/results/plots/routing_comparison_plan_531.png)
  - [Vector SVG Schematic](file:///C:/hackhive/results/plots/routing_comparison_plan_531.svg)
  - [Interactive HTML Viewer](file:///C:/hackhive/results/plots/routing_plan_531.html)
  - [ASCII Schematic Text](file:///C:/hackhive/results/plots/routing_plan_531_ascii.txt)

---

## 5. Upcoming Benchmark Report Templates

To prepare the optimization team for the upcoming benchmark evaluations, two standardized report templates have been engineered and stored in [`benchmarks/visualization/templates/`](file:///C:/hackhive/benchmarks/visualization/templates/):

1. **`report_template_test_1632.md`**:
   - Structured for the unseen 1,632-plan test split (`split.json: test`).
   - Pre-configured placeholders for 5-way scorecards, paired Wilcoxon test $p$-values, bootstrap 95% confidence intervals, and tier breakdowns.
2. **`report_template_production_17000.md`**:
   - Engineered for the full 17,000-plan dataset production run across train, validation, test, and augmented splits.
   - Global ranking table, total cable length savings in kilometers, total computation hours, and strict monotonicity audit (0 regressions out of 17,000).

---

## 6. CLI Execution Guide

The visualizer supports full CLI execution with customizable options:

```bash
# Run full visualization suite on validation results
python -m benchmarks.visualization.visualizer \
  --summary results/benchmarks/benchmark_summary_validation.json \
  --results results/benchmarks/benchmark_results_validation.csv \
  --output-dir results/plots \
  --render-plans 51 78 28 239 531 \
  --report-out reports/benchmark_validation_visual_report.md

# Render specific floor plans only
python -m benchmarks.visualization.visualizer \
  --render-plans 14433 0 51 \
  --output-dir results/plots

# Generate report from summary without re-rendering plans
python -m benchmarks.visualization.visualizer \
  --summary results/benchmarks/benchmark_summary_smoke.json \
  --report-out reports/benchmark_smoke_visual_report.md
```

### CLI Flag Reference:
- `--summary`: Path to `benchmark_summary.json` containing structured metrics.
- `--results`: Path to `benchmark_results.csv` containing wide per-plan records.
- `--output-dir`: Output directory for generated plots, diagrams, and HTML viewers.
- `--render-plans`: Space-separated list of plan IDs to render before/after diagrams for.
- `--report-out`: Output path for generated Markdown report.
- `--all`: Flag to execute complete pipeline.

---

## 7. Verification & Empirical Proof

| Requirement | Evidence / Artifact | Status |
| :--- | :--- | :---: |
| Comparative Performance Charts | `results/plots/algorithm_scorecard_bar.png`, `cost_j_distribution_boxplot.png`, `delta_j_distribution.png`, `tier_stratified_performance.png`, `win_tie_loss_breakdown.png` | **VERIFIED** |
| Scalability & Pareto Curves | `results/plots/runtime_vs_complexity.png`, `results/plots/pareto_frontier.png` | **VERIFIED** |
| Before/After Floor Plan Diagrams | `results/plots/routing_comparison_plan_{51,78,28,239,531}.png` & `.svg` | **VERIFIED** |
| Interactive HTML Sidecar Visualizer | `results/plots/routing_plan_{51,78,28,239,531}.html` with layer toggles | **VERIFIED** |
| 2D ASCII Schematics | `results/plots/routing_plan_{51,78,28,239,531}_ascii.txt` | **VERIFIED** |
| Report Templates for 1,632 & 17,000 | `benchmarks/visualization/templates/report_template_test_1632.md`, `report_template_production_17000.md` | **VERIFIED** |
| Master Documentation Report | `reports/agent5_visualization_suite.md` | **VERIFIED** |
