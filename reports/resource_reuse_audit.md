# Resource Reuse and Asset Audit

**Project:** Honeywell Cable Ring Optimization  
**Date:** 23 October 2026  
**Status:** Canonical Asset Baseline Established  
**Branch:** `prd-optimization-engine`

---

## 1. Executive Policy

In accordance with PRD Section 8 and Part 3 of the Master Prompt:
- **Reuse verified code wherever possible.** Do not reinvent working algorithms or proven infrastructure without a technical reason.
- **Strictly invalidate stale caches.** Old cached routing solutions, benchmark scores, or training trajectories computed with incompatible geometry, outdated constraints, or unverified objectives are classified as historical artifacts only.
- **Preserve historical evidence.** Previous benchmark CSVs and PPO logs are archived for baseline comparison, never silently merged into current claims.
- **Relearn/Retrain only when justified.** Bandit matrices and neural network weights must be reset or relearned whenever environment dynamics, feature representations, or reward formulations change.

---

## 2. Comprehensive Asset Inventory and Classification

Every existing codebase module, algorithm, data artifact, and test suite is categorized into one of five definitive classes:
1. **Reuse Unchanged**: Mathematically sound, verified against official rules, and directly usable.
2. **Reuse After Verification**: Working implementation requiring verification against official slides and edge cases.
3. **Refactor**: Functional foundation present, but needs interface alignment, caching overhaul, or performance hardening.
4. **Reimplement**: Concept valid but existing implementation violates constraints or contains critical flaws.
5. **Archive**: Preserved strictly for historical reference and regression auditing.

| Category | Artifact / File | Description | Classification | Action & Governance Rule |
| :--- | :--- | :--- | :--- | :--- |
| **Data Source** | `ResPlan.pkl` | 17,000 raw residential floor plans from Kaggle/GitHub | **Reuse Unchanged** | Canonical immutable source dataset. |
| **Data Source** | `docs/Cable Ring Challenge - Problem Statement4.pptx` | Authoritative 8-slide challenge specification | **Reuse Unchanged** | Primary authoritative source of truth. |
| **Data Source** | `split.json` | Train/validation/test split manifest | **Reuse After Verification** | Verify leakage resistance and room-size eligibility criteria. |
| **Ingestion** | `src/resplan_router/phase1_ingestion/scale.py` | Scale calculation `scale = sqrt(inner.area / area)` | **Reuse Unchanged** | Verified strictly matches Slide 4; ignores `net_area`. |
| **Ingestion** | `src/resplan_router/phase1_ingestion/resplan_adapter.py` | Floor plan geometry normalization, fixed nodes, rooms | **Reuse After Verification** | Ensure 0.5m setback and fixed node immutability are respected. |
| **Topology** | `src/resplan_router/phase2_topology/portals.py` | Door and open-passage extraction | **Reuse After Verification** | Verify doors vs walls/windows/front_door; ensure valid doors are not missed. |
| **Topology** | `src/resplan_router/phase2_topology/macro_topology.py` | Macro room adjacency graph | **Reuse After Verification** | Ensure graph is strictly door-connected (Rule 3). |
| **Micro-Routing** | `src/resplan_router/phase3_micro_routing/hanan_grid.py` | Obstacle-avoiding Hanan grid construction | **Reuse After Verification** | Validated rectilinear grid in free space; verify 1mm tolerance. |
| **Micro-Routing** | `src/resplan_router/phase3_micro_routing/astar_router.py` | A* pathfinding through door portals | **Reuse After Verification** | Verify non-wall penetration, axis-alignment, and clearance. |
| **Partitioning** | `src/resplan_router/phase4_partitioning/candidate_generator.py` | Door-connected candidate zone enumeration | **Reuse After Verification** | Audit candidate-zone catalog completeness for <= 100m² and <= 5 nodes. |
| **Partitioning** | `src/resplan_router/phase4_partitioning/partition_solver.py` | Set partitioning solver for minimal hubs | **Reuse After Verification** | Check solver optimality, ties, and tie-breaking area gap. |
| **Sequencing** | `src/resplan_router/phase5_ring_sequencing/tsp_solver.py` | TSP sequencing (Held-Karp dynamic programming) | **Reuse After Verification** | Held-Karp exact for <= 5 nodes (trivial n <= 5). Verify loop closure. |
| **Sequencing** | `src/resplan_router/phase5_ring_sequencing/ring_builder.py` | Physical closed-ring polyline synthesis | **Reuse After Verification** | Ensure exact 1mm continuity, H-start, H-return, and no branching. |
| **Validation** | `src/resplan_router/phase6_output/checker.py` (`FloorPlanChecker`) | Independent 1mm competition metric and rule evaluator | **Reuse Unchanged** | Recomputes all 6 Hard Rules and J = L + 5X + O + 0.5B at 1mm tolerance. |
| **Algorithms** | `src/resplan_router/optimizers/greedy_baseline.py` | Fast greedy baseline solver | **Reuse After Verification** | Serves as fast canonical baseline for comparisons. |
| **Algorithms** | `src/resplan_router/optimizers/bitmask_dp.py` | Exact Bitmask Dynamic Programming for set partitioning | **Reuse After Verification** | Exact minimum-hub optimizer; verify candidate-catalog completeness. |
| **Algorithms** | `src/resplan_router/optimizers/cpsat_hanan.py` | CP-SAT solver for joint Hanan routing | **Reuse After Verification** | OR-Tools CP-SAT formulation; profile timeout and tractability. |
| **Algorithms** | `src/resplan_router/optimizers/cpsat_multipath.py` | Multi-path CP-SAT formulation | **Reuse After Verification** | Profile tractability against BitmaskDP and A*. |
| **Algorithms** | `src/resplan_router/optimizers/alns_optimizer.py` | Adaptive Large Neighborhood Search core | **Reuse After Verification** | Verify operator library and simulated annealing schedule. |
| **Algorithms** | `src/resplan_router/optimizers/alns_operators.py` | 8 repaired ALNS destroy and repair operators | **Reuse After Verification** | Ensure operators preserve feasibility and never introduce invalid rings. |
| **Algorithms** | `src/resplan_router/optimizers/portfolio_hybrid.py` | Hybrid portfolio combining exact DP + ALNS + A* | **Reuse After Verification** | Core candidate for champion portfolio. |
| **Learning** | `rl/linucb.py` / `src/resplan_router/optimizers/alns_linucb.py` | Contextual Bandit with 24-feature state and Sherman-Morrison | **Reuse After Verification** | Reset learned arm matrices if feature semantics or rewards change. |
| **Learning** | `rl/environment.py`, `rl/rewards.py`, `rl/observations.py` | RL environment and reward functions | **Refactor** | Eliminate no-op reward bias; align rewards strictly with J improvement. |
| **Learning** | `rl/train_ppo.py`, `rl/policy.py` | PPO training pipeline (PyTorch + CUDA) | **Refactor** | Retain for research exploration; address entropy collapse and step latency. |
| **Learning** | `checkpoints/` | Historical PPO checkpoints | **Archive** | Keep for historical comparison; do not use as primary production model. |
| **Cache** | `rl/baseline_cache.py` / SQLite cache | Cached baseline solutions | **Refactor** | Upgrade to versioned, content-addressed cache keys (invalidating stale runs). |
| **Tests** | `tests/unit/test_constraint_audit.py` (55 tests) | Unit constraint audit suite | **Reuse Unchanged** | 100% passing; enforces all 17 competition constraints. |
| **Tests** | `tests/unit/test_validator_negative.py` | Negative fixture suite for all 6 Hard Rules | **Reuse Unchanged** | Passes adversarial corruption tests for Rule 1 to 6. |
| **Reports** | `reports/` (historical benchmark and diagnostic reports) | 22 historical audit, diagnostic, and benchmark markdown files | **Archive** | Preserved as baseline evidence; will not be overwritten. |

---

## 3. Storage and Cache Invalidation Policy

1. **Stale Cache Elimination:**
   - Any cache entry lacking a version hash corresponding to the current `FloorPlanChecker` and geometry parser will be flagged as stale and excluded from benchmark results.
   - Cache keys must be formed as:
     `Key = SHA256(plan_id + geometry_hash + checker_version + solver_id + solver_params)`
2. **Deterministic Reproducibility:**
   - All random seeds for ALNS, LinUCB, and PPO sampling are explicitly recorded and frozen.
   - All test partitions remain strictly immutable.
