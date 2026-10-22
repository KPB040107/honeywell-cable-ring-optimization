=== PAGE 1 ===
Honeywell Cable Ring Optimization
Product Requirements Document | Correctness-first algorithm research and evaluation
Version 1.0  •  23 October 2026  •  Scope: optimization engine, experimentation and reproducibility only
1. Executive decision
Build a mathematically faithful optimizer for joint control-panel placement, eligible-room assignment, node 
sequencing and closed-loop cable routing. Select the best validated algorithm or algorithm portfolio by 
lexicographic feasibility and performance: first minimum hub count H, then minimum routing cost J. Do not 
predetermine that RL must win.
Decision: rebuild or repair the geometry/constraint foundation first; reuse verified modules and historical 
data as assets; rerun benchmarks whenever underlying semantics change. UI, dashboards and presentation 
polish are explicitly deferred.
2. Source of truth and official mathematical model
Authoritative source: supplied 8-slide “Cable Ring Challenge - Problem Statement4(1).pptx”, especially slides 
2–8. Mentor Q&A is secondary; conflicts must be logged and resolved against the written rules/checker 
rather than assumed away.
Requirement Acceptance interpretation
Eligible nodes One fixed input node in each eligible living, bedroom, kitchen or storage room; no moving input nodes.
Hub capacity At most 100 m² assigned eligible-room area AND at most 5 nodes.
Zone connectivity
Assigned eligible rooms form one connected territory via 
doors/open passages; walls, windows and entrance do not 
establish adjacency.
Hub placement Within an assigned eligible room; 0.5 m from walls; may ≥share a room with a node.
Ring One closed nonbranching H N1 … Nn H route per hub; → → → →each assigned node visited once.
Routing
Axis-aligned, inside inner boundary, no 
wall/window/external crossings; between rooms only 
through doors/open passages. Single-door retracing 
permitted.
Objective Minimize lexicographically (H, J, area-gap tie-break), J = L + 5X + O + 0.5B; 1 mm tolerance.
Units Per-plan scale = sqrt(inner.area / area); convert geometry before area, distance and clearance checks.
Output
Per-plan valid JSON with hubs, assignments, node sequence, 
full polylines, return leg and independently recomputed 
metrics.
Specification nuance: slides describe approximately 90% benchmark filtering and reference-solver 
feasibility, whereas the raw 17,000 ResPlan records include anomalous and potentially infeasible cases. 
Distinguish raw-data processing from official benchmark eligibility. Mentor suggestion about exceptional 
=== PAGE 2 ===
excluded-area traversal is not an unconditional permission; treat as unresolved exception requiring formal 
evidence.
3. What exists today: assets and measured history
Asset / evidence Reuse decision
17,000 ResPlan records; inventory, split manifests, 
anomaly audits
Reuse raw source and audit scripts; regenerate derived 
splits/eligibility from canonical rules.
GreedyBaseline, BitmaskDP, CP-SAT Hanan/MultiPath Reuse algorithms and interfaces after candidate completeness and geometry verification.
PortfolioHybrid, ALNS, repaired 8 operators Reuse as reference/starting implementation; independently validate hub assignments and ring reuse.
LinUCB (8 arms, 24 features, Sherman–Morrison updates) Reuse implementation and tests; reset learned statistics if state/action/reward semantics change.
PPO implementation, RTX 4050 CUDA, checkpoints Archive as reproducible baseline; retrain only after corrected environment and positive headroom evidence.
FloorPlanChecker and 179 reported passing tests
Reuse regression suite, but independently implement oracle 
tests; pass counts alone do not establish specification 
correctness.
SQLite baseline cache and benchmark runner Reuse mechanisms; invalidate entries with versioned geometry, checker, objective and solver hashes.
Historical 100-plan LinUCB result: J 23.77 vs 24.63 baseline Retain as historical result, not definitive after a specification change; rerun paired benchmarks.
4. What went wrong or remains unproven
 Potentially incomplete room-door adjacency or candidate-zone generation may overestimate minimum 
hubs. The six-node/six-hub image is a trigger for investigation, not proof of a defect.
 “Exact optimal hub count” only holds relative to correctly enumerated feasible candidate zones and 
validated geometric placement/routing. It is not a global cable-cost proof.
 Prior reports used inconsistent interpretations (initial 20-node statement versus authoritative 5-node 
limit; possible confusion between cable traversal and zone connectivity).
 Original PPO learned a near no-op action (99.18% reported selection), entropy collapsed, and a five-plan 
validation set stayed flat. Reward design favored safe non-improvement.
 PPO environment stepping was sequential despite multiple configured environments; hidden validation 
steps and repeated routing caused ~12-hour pilot duration.
 Previously reported algorithm comparisons used differing sample sizes, feasible subsets and budgets; 
historical scores are not directly interchangeable.
 Infeasible/anomalous raw records were sometimes over-quarantined; 95–100 m² rooms are not 
inherently infeasible. Dataset partition accounting must be regenerated and reconciled.
 Visual overlays may not faithfully reflect underlying hub and ring JSON; visual correctness must be 
independently checked at the final stage.
5. Non-goals, principles and success criteria
 No UI, frontend, dashboard, styling or presentation deliverables until after final algorithm selection.
 No forced use of RL, GPU or a particular optimizer; choose based on unbiased cost, feasibility and 
runtime evidence.
 No weakening official checker or constraints to improve reported scores.
=== PAGE 3 ===
 No claim of global optimality without proof/certificate; distinguish proven H, best-found J, lower bounds 
and timeouts.
Success gates: (a) official-rule conformance on independent adversarial fixtures; (b) exact or explicitly 
bounded minimum H for benchmark-eligible plans; (c) valid closed rings and reproducible J; (d) strongest 
feasible J within a stated wall-clock budget; (e) robust held-out performance with uncertainty and failure 
accounting; (f) resumable evaluation across all raw records.
6. End-to-end phased implementation plan
Phase 0 — Freeze and provenance
Work: Snapshot repository, commit, old results and model states. Inventory raw inputs, JSON schemas, 
dataset IDs, version hashes, baseline cache and historical evaluation protocols.
Deliverables: Reproducibility manifest, asset register, old-vs-new rules diff.
Exit gate: Every artifact traceable; no destructive edits.
Phase 1 — Formal specification and independent oracle
Work: Translate slides 2–8 into machine-checkable invariants; test 5-node capacity, 100 m², fixed nodes, 
scale, contiguity, 0.5 m clearance, door-only traversal, ring closure and J. Build tiny handcrafted 
counterexamples.
Deliverables: Specification matrix, independent validator/oracle, property and negative tests.
Exit gate: Independent oracle and official checker agree on curated fixtures; disagreements documented.
Phase 2 — Dataset and geometry foundation
Work: Reparse all 17k; repair polygons deterministically; derive inner/free space, walls/windows, door gaps, 
eligible rooms and fixed node coordinates; separate traversability graph from zone adjacency; log 
ambiguous passages.
Deliverables: Versioned geometry IR, geometry tests, eligibility ledger and stratified split manifests.
Exit gate: 17k IDs reconciled exactly; units and door graphs pass geometric fixtures; no silent exclusions.
Phase 3 — Proven minimum-hub search
Work: Enumerate connected room subsets (up to documented small-N limits); filter area/node capacity; 
compute placement feasibility and route feasibility; solve minimum set partition with bitmask DP/CP-SAT; 
use counterexample-guided refinement for unrealizable candidates.
Deliverables: Certified H bounds, candidate-zone catalog, witnesses, infeasibility/timeout categories.
Exit gate: For small synthetic instances exhaustive reference agrees; suspicious six-node plan independently 
checked.
Phase 4 — Feasible closed-ring engine
Work: For each feasible zone, generate legal hub points, axis-aligned route graph through door gaps, node 
order (Held–Karp where practical), complete H-return ring; preserve alternative equal-H partitions.
Deliverables: Validated initial solutions, route certificates, deterministic fallback.
=== PAGE 4 ===
Exit gate: Independent oracle passes full JSON and polylines on representative layouts.
Phase 5 — Cost-optimal search portfolio
Work: Optimize J among equal-H feasible solutions using exact small-instance search, route rerouting, hub-
location refinement, partition swaps, ALNS, CP-SAT where tractable and portfolio scheduling. Track best 
incumbent and lower bounds.
Deliverables: Portfolio of algorithms with shared interface and budget control.
Exit gate: Paired, equal-budget tests show best-found J and no H regression; timeouts reported.
Phase 6 — Learning research: LinUCB and PPO
Work: Audit corrected action space and improvement headroom; compare random, adaptive ALNS, LinUCB 
and PPO. Reuse old training examples only after semantic verification; regenerate invalid examples. Train 
shared policies on training partition, validate separately.
Deliverables: Learning baselines, ablations, trained/checkpointed models, reward and action diagnostics.
Exit gate: Learning method must outperform simpler baseline or offer proven speed/robustness benefit to 
be retained.
Phase 7 — Model/algorithm selection
Work: Freeze all configurations; repeated seeds, matched wall-clock and operator budgets, complexity-
stratified validation; evaluate held-out test once for final claims; report paired J, H, feasibility, runtime and 
confidence intervals.
Deliverables: Champion selection report, statistical analysis and version-locked run manifest.
Exit gate: Chosen optimizer/portfolio wins by predefined lexicographic decision policy; no test-set tuning.
Phase 8 — Full 17,000-plan operational evaluation
Work: Run selected method over all raw IDs with deterministic status categories, per-plan output JSON, 
checker metrics, checkpoints, resume, resource telemetry and audit sampling. Keep held-out report 
separate.
Deliverables: Full results ledger, validated solution archive, failures, throughput and cost distribution.
Exit gate: Every input accounted for; each feasible output independently checked; no missing IDs.
Phase 9 — Final research consolidation
Work: Compare certified H, best-found J, ablation value of each solver, latency and generalization; publish 
limitations, reproducibility instructions and future work. UI is a separate subsequent project.
Deliverables: Technical report, reproducible code, final algorithm specification.
Exit gate: Independent rerun on representative subset reproduces key findings.
7. RL decision: train on all 17,000?
Not as one undifferentiated training corpus. The 17,000 records are the complete raw dataset, not 17,000 
independent models. Create grouped, leakage-resistant training/validation/held-out partitions of eligible 
=== PAGE 5 ===
plans. A shared LinUCB or PPO policy can encounter training plans repeatedly; validation informs selection; 
held-out test is never used for tuning. After selection, evaluate the frozen method operationally on all 17,000 
raw records, including explicit invalid/infeasible statuses.
Recommended experiment ladder: (1) confirm nontrivial improvement headroom using all operators on a 
stratified sample; (2) random/adaptive ALNS baselines; (3) LinUCB online learning on training plans; (4) 
supervised operator pretraining only if labels are genuinely useful; (5) corrected PPO pilot with measured 
SPS, entropy, action diversity and paired J; (6) scale only if validation beats simpler methods. PPO training is 
optional evidence-based research, not a mandatory production dependency.
Reuse old PPO checkpoint only as historical comparison; do not resume it into changed 
observation/action/reward semantics. Reuse LinUCB update code, not necessarily its learned arm matrices. 
Old per-plan solutions can seed search only after revalidation with current geometry and objective; old 
labels/rewards must be recomputed if semantics changed.
8. Resource reuse policy: no compromises, no wasted work
Resource Keep / regenerate rule
Raw ResPlan.pkl / official slide deck Immutable canonical sources; always reuse.
Parsed geometry and adjacency Reuse only if coordinate scaling, polygon repair, door interpretation and hash match current specification.
Baseline JSON and SQLite cache Revalidate; key by plan, geometry, checker, objective, solver and parameters; invalidate stale entries.
ALNS operators, DP, CP-SAT, A* Reuse tested implementation, but re-run correctness and comparative performance after foundational changes.
Historical benchmark CSVs
Archive for regression/history; never merge old scores into 
new claims without identical semantics and evaluation 
protocol.
LinUCB 24-feature encoder / bandit matrices Reuse encoder if feature semantics stable; otherwise rederive and reset matrices. Relearn only on training split.
PPO policy weights and trajectories Use as baseline or pretraining candidate only after schema and reward compatibility checks; otherwise train afresh.
GPU RTX 4050 and CPU i5-13450HX CPU for geometry/ALNS/DP; GPU for batched PPO learning if justified. Profile end-to-end, not utilization alone.
9. Experimental design and decision rules
 Primary ranking is lexicographic: validity first; then H; then J; then area-gap tie-break. Never report 
“lower J wins” if H differs.
 Use same plans, initial incumbents, legal constraints, random seeds and matched wall-clock budgets; 
additionally report fixed-operator-budget experiments.
 Report feasibility denominators (raw, eligible, solvable, timeout, malformed) and per-plan paired 
differences, not only mean J.
 Report success rate for proving H, relative J improvement, median and P95 runtime, memory, operator 
no-op rate, and per-complexity results.
 Keep training/validation/test grouping immutable; online bandit updates in evaluation require 
separately labeled predeclared protocol. Frozen-policy evaluation is primary for generalization.
 For small synthetic instances use exhaustive independent solver as oracle; for large plans report lower 
bounds and optimality gaps where computable.
=== PAGE 6 ===
 Choose a portfolio if different solvers dominate different plan classes; dispatch rules must be learned on 
training/validation only and included in runtime accounting.
10. Parallel subagents and critical path
Run independent work concurrently but respect dependencies: Agent A formal spec/oracle; B 
geometry/data; C hub exact optimization; D route engine; E benchmarking/reproducibility; F learning 
research. A and B begin together; C/D use agreed interfaces and fixtures; E builds harness in parallel; F 
initially audits historical data and waits for validated environments before retraining. Use worktrees, file 
ownership, shared schema contracts, and a single heavy benchmark scheduler to avoid laptop contention.
The lead orchestrator owns integration and gate sign-off. Do not launch full training or all-17k runs 
automatically before Phase 2–5 correctness gates. Measure throughput on representative easy/medium/hard 
plans before estimating calendar time.
11. Risks, open questions and mitigations
 Door/passage representation may be ambiguous in ResPlan: produce geometric evidence and uncertain-
case status; never silently invent connections.
 Official example indicates six nodes can use two hubs, but this does not establish the minimum for the 
earlier screenshot; identify that exact plan ID and produce a witness or infeasibility explanation.
 Slides state fixed node points; mentor says teams generate placements. Use detailed slide as canonical 
for benchmark inputs, document conflict and test input contract.
 Raw 17k contains oversized/anomalous plans; distinguish official filtered benchmark from full raw 
corpus and retain audit trail.
 “Optimal” J can be intractable: report proven hub optimality separately from heuristic routing-cost best-
known.
 Potential data leakage from precomputed baselines or online adaptation: version cache and enforce 
partition controls.
 CPU-heavy route synthesis may dominate GPU training: use profiling, caching and bounded episodes 
before scaling.
12. Acceptance checklist and final deliverables
 Formal spec and independent validator with adversarial examples
 17k raw-record reconciliation, eligibility ledger and frozen split manifests
 Geometry and door-connectivity graph verified on real and synthetic cases
 Minimum-hub solver with explicit proof status and witness solutions
 Complete legal closed-ring generator and official J recomputation
 Fair classical + ALNS + LinUCB + PPO research comparison
 Held-out evaluation with confidence intervals and no leakage
 Best algorithm/portfolio chosen by predefined criteria
 Resumable 17k operational run with every record accounted for
 Reproducible code, tests, configs, logs and final technical report
 UI/visual product layer deferred to a separate post-research phase