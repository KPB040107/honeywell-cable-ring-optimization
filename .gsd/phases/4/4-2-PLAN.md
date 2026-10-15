---
phase: 4
plan: 2
wave: 2
gap_closure: false
depends_on:
  - 4-1-PLAN.md
---

# Plan 4.2: Lexicographic Contiguous Partitioner & Verification Suite

## Objective
Implement the exact contiguous zone partitioning engine (`zone_milp.py`) that minimizes hub count $H \ge H_{\min} = \max(\lceil \text{area}/100 \rceil, \lceil \text{nodes}/5 \rceil)$, ensures door-connected contiguity across rooms (Rule 3), respects capacity bounds ($\le 100\text{ m}^2, \le 5$ nodes per hub, Rule 2), prices zones using exact ring tour costs, and applies the area-gap tie-breaker ($\min \Delta A_{\text{gap}}$), verified on the Slide 5 schematic.

## Context
- `v2.md` (Sections 2.1, 2.2, 2.3, 5.4)
- `.gsd/SPEC.md`
- `.gsd/phases/4/RESEARCH.md`
- `.gsd/phases/4/4-1-PLAN.md`
- `tests/fixtures/slide5_schematic.json`

## Tasks

<task type="auto">
  <name>Exact Contiguous Zone Partitioner</name>
  <files>
    src/resplan_router/phase4_partitioning/zone_milp.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase4_partitioning/zone_milp.py`:
       - Class `ZonePartitioner`:
         - Method `compute_lower_bound_hubs(total_area_m2: float, node_count: int, max_area: float = 100.0, max_nodes: int = 5) -> int`: returns $\max(\lceil \text{area} / \text{max\_area} \rceil, \lceil \text{node\_count} / \text{max\_nodes} \rceil)$.
         - Method `enumerate_connected_zones(topology: PortalTopologyGraph, plan: MetricFloorPlan, max_area: float = 100.0, max_nodes: int = 5) -> list[dict]`:
           - Explores all connected subgraphs $Z \subseteq R_{\text{elig}}$ on $G_{\text{macro}}$ using breadth-first search or recursive expansion.
           - Filters out any candidate subset where $|Z| > max_nodes$ or $\sum_{r \in Z} \text{area}(r) > max_area$.
           - Validates that $G_{\text{macro}}[Z]$ is connected via doors/open passages.
         - Method `evaluate_zone_exact_ring(zone_rooms: list[str], plan: MetricFloorPlan, cost_tensor: PairwiseCostTensor, candidates: dict[str, list[Point2D]]) -> tuple[Point2D, str, float]`:
           - For candidate zone $Z$ with nodes $N(Z)$ and candidate hub sites $\mathcal{H}_Z$:
           - Evaluates node permutations to find optimal hub $h^*$ and best closed-ring tour cost $J^*(Z)$.
         - Method `solve_partition(plan: MetricFloorPlan, topology: PortalTopologyGraph, cost_tensor: PairwiseCostTensor, candidates: dict[str, list[Point2D]]) -> HubZonePartition`:
           - Starts at $H = H_{\min}$.
           - Formulates Set Partitioning model (using OR-Tools CP-SAT or exact integer program):
             - Decision variables: $x_z \in \{0, 1\}$ for each candidate valid zone $z$.
             - Exact coverage constraint: each eligible room $r \in R_{\text{elig}}$ is covered by exactly one selected zone: $\sum_{z: r \in z} x_z = 1$.
             - Hub count constraint: $\sum_z x_z = H$.
             - Objective: minimize $\sum_z J^*(z) \cdot x_z + 10^{-3} \cdot (\max_k A_k - \min_k A_k)$.
           - If infeasible at $H$, increment $H \leftarrow H + 1$ and repeat.
           - Maps solution into validated `HubZonePartition` model.

    AVOID: Allowing non-contiguous room assignments across walls or entrance.
    USE: Exact connected-subgraph enumeration with CP-SAT set partitioning.
  </action>
  <verify>
    python -c "from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner; print('zone_milp imported successfully')"
  </verify>
  <done>
    `ZonePartitioner` solves exact contiguous room partition with minimal hubs and area-gap tie-breaker.
  </done>
</task>

<task type="auto">
  <name>Slide 5 Benchmark & Phase 4 Verification Suite</name>
  <files>
    tests/test_phase4_partitioning.py
  </files>
  <action>
    1. Build `tests/test_phase4_partitioning.py` implementing the official verification gates from `v2.md` Section 5.4.3:
       - **Test 4.1 (`test_slide5_6room_exact_partition`)**:
         - Run partitioner on Slide 5 schematic ($118\text{ m}^2$, 6 nodes).
         - Assert `hub_count == 2` (matching lower bound $H_{\min} = 2$).
         - Assert Zone `H1` consists of `{Living, Bedroom 1, Bedroom 3}` ($71\text{ m}^2$, 3 nodes).
         - Assert Zone `H2` consists of `{Bedroom 2, Kitchen, Storage}` ($47\text{ m}^2$, 3 nodes).
       - **Test 4.2 (`test_rule6_hub_05m_wall_clearance`)**:
         - For every selected hub $H_k$, assert `host_room.contains(H_k)` is True.
         - Assert `host_room` is an eligible room assigned to $H_k$.
         - Assert $\text{distance}(H_k, \text{obstacles}) \ge 0.50\text{ m} - 0.001\text{ m}$.
       - **Test 4.3 (`test_area_gap_tie_breaker`)**:
         - On a symmetric 4-room plan where two partitions have equal routing cost $J = 30.0$, one with $(80\text{ m}^2, 20\text{ m}^2)$ and one with $(50\text{ m}^2, 50\text{ m}^2)$, assert the solver selects the balanced $(50, 50)$ partition ($\Delta A_{\text{gap}} = 0$).
       - **Test 4.4 (`test_phase1_to_phase4_regression`)**:
         - Re-verify end-to-end integration across Phases 1, 2, 3, and 4.

    AVOID: Permitting partitions that violate capacity or contiguity.
    USE: Strict verification of all 6 hard rules on the partitioned output.
  </action>
  <verify>
    pytest tests/test_phase4_partitioning.py -v
  </verify>
  <done>
    Phase 4 test suite passes 100%, exactly replicating Slide 5 2-hub partition and tie-breaker.
  </done>
</task>

## Must-Haves
- [ ] Optimal hub count matches theoretical lower bound $H_{\min}$ on Slide 5 ($H = 2$).
- [ ] Slide 5 zones match `{Living, Bed 1, Bed 3}` and `{Bed 2, Kitchen, Storage}`.
- [ ] Every hub is placed $\ge 0.50\text{ m} - 0.001\text{ m}$ from obstacles inside its assigned room.
- [ ] Balanced area-gap tie-breaker selected when routing costs are equal.

## Success Criteria
- [ ] `pytest tests/test_phase4_partitioning.py` passes 100%.
- [ ] Zero capacity or contiguity violations across all zones.
- [ ] End-to-end Phase 1–4 regression passes cleanly.
