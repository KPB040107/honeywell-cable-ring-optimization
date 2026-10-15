---
phase: 4
plan: 1
wave: 1
gap_closure: false
---

# Plan 4.1: Hub Candidate Generation & Data Contracts

## Objective
Define the Phase 4 partition data contracts (`HubAssignment`, `HubZonePartition`) and implement the Rule 6 compliant hub candidate generator (`hub_candidates.py`) that samples $0.5\text{ m}$ wall-cleared coordinates inside eligible rooms, aligned with Hanan grid lines and maintaining $\ge 0.30\text{ m}$ separation from fixed nodes.

## Context
- `v2.md` (Sections 2.1, 2.2, 4, 5.4)
- `.gsd/SPEC.md`
- `.gsd/phases/4/RESEARCH.md`
- `src/resplan_router/contracts.py`

## Tasks

<task type="auto">
  <name>Phase 4 Partition Data Contracts</name>
  <files>
    src/resplan_router/contracts.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` to add Phase 4 contracts:
       - `HubAssignment`:
         - `hub_id: str`: unique hub identifier (e.g. `H1`, `H2`).
         - `room_id: str`: host eligible room containing the hub.
         - `location: Point2D`: metric coordinates satisfying $\ge 0.5\text{ m}$ wall clearance.
         - `assigned_rooms: list[str]`: eligible rooms assigned to this hub.
         - `assigned_nodes: list[str]`: fixed nodes served by this hub.
         - `zone_area_m2: float`: total eligible area of assigned rooms ($\le 100.0\text{ m}^2$).
         - `estimated_ring_cost_J: float`: pre-routing estimated closed-ring cost.
       - `HubZonePartition`:
         - `plan_id: str`.
         - `hub_count: int`: total selected hubs $H$.
         - `lower_bound_hubs: int`: $H_{\min} = \max(\lceil \text{area}/100 \rceil, \lceil \text{nodes}/5 \rceil)$.
         - `area_gap_m2: float`: $\max_k A_k - \min_k A_k$.
         - `hubs: list[HubAssignment]`.
    2. Add validators verifying `zone_area_m2 <= 100.001` and `len(assigned_nodes) <= 5`.

    AVOID: Unvalidated partition structures.
    USE: Immutable Pydantic models enforcing Slide 3 capacity invariants.
  </action>
  <verify>
    python -c "from resplan_router.contracts import HubAssignment, HubZonePartition; print('Phase 4 contracts valid')"
  </verify>
  <done>
    `HubAssignment` and `HubZonePartition` models import and validate successfully.
  </done>
</task>

<task type="auto">
  <name>Rule 6 Compliant Hub Candidate Generator</name>
  <files>
    src/resplan_router/phase4_partitioning/__init__.py
    src/resplan_router/phase4_partitioning/hub_candidates.py
  </files>
  <action>
    1. Create `src/resplan_router/phase4_partitioning/__init__.py`.
    2. Implement `src/resplan_router/phase4_partitioning/hub_candidates.py`:
       - Function `generate_hub_candidates(plan: MetricFloorPlan, room_id: str, margin_m: float = 0.505, min_node_dist: float = 0.30) -> list[Point2D]`:
         - Validates that `room_id` is an eligible room in `plan.rooms`.
         - Computes safe placement polygon: $P_{\text{safe}} = R_{\text{geom}}.difference(\text{plan.obstacles_geom.buffer}(margin_m))$.
         - If $P_{\text{safe}}$ is empty or invalid: fallback to safe interior pole via `shapely.ops.polylabel(R_{\text{geom}}.difference(plan.obstacles_geom.buffer(0.495)), tolerance=0.001)`.
         - Extracts candidate $(x, y)$ coordinates inside $P_{\text{safe}}$ aligned with room node coordinates and doorway lines.
         - Filters candidate points to ensure:
           - $\text{distance}(h, \text{plan.obstacles_geom}) \ge 0.50\text{ m} - 0.001\text{ m}$.
           - $\text{distance}(h, N_i) \ge min_node_dist$ ($0.30\text{ m}$).
           - Point lies strictly within the room polygon.
         - Returns non-empty list of candidate `Point2D` locations.
       - Function `generate_all_room_hub_candidates(plan: MetricFloorPlan) -> dict[str, list[Point2D]]`.

    AVOID: Generating candidate hub points closer than $0.5\text{ m}$ to walls/windows.
    USE: Inset buffer $0.505\text{ m}$ with strict validation against obstacles.
  </action>
  <verify>
    python -c "from resplan_router.phase4_partitioning.hub_candidates import generate_all_room_hub_candidates; print('hub_candidates imported successfully')"
  </verify>
  <done>
    `hub_candidates.py` generates valid $0.5\text{ m}$ wall-cleared candidate points for all eligible rooms.
  </done>
</task>

<task type="auto">
  <name>Hub Candidate Unit Tests</name>
  <files>
    tests/test_phase4_candidates.py
  </files>
  <action>
    1. Create `tests/test_phase4_candidates.py` testing:
       - Candidate generation on Slide 5 schematic rooms.
       - Verification that 100% of candidate points lie strictly inside their host room.
       - Verification that for every candidate $h$, $\text{distance}(h, \text{obstacles}) \ge 0.50\text{ m} - 0.001\text{ m}$ (Rule 6).
       - Verification that distance to fixed node $\ge 0.30\text{ m}$.
       - Verification that excluded rooms (`bathroom`) produce 0 hub candidates.

    AVOID: Skipping wall clearance assertions.
    USE: Strict numerical distance calculations against obstacle union.
  </action>
  <verify>
    pytest tests/test_phase4_candidates.py -v
  </verify>
  <done>
    Hub candidate unit tests pass 100% with Rule 6 compliance verified.
  </done>
</task>

## Must-Haves
- [ ] 100% of candidate hub coordinates satisfy $\ge 0.50\text{ m} - 0.001\text{ m}$ wall clearance.
- [ ] All candidates lie strictly inside host eligible rooms.
- [ ] Candidates maintain $\ge 0.30\text{ m}$ separation from room fixed nodes.

## Success Criteria
- [ ] `pytest tests/test_phase4_candidates.py` passes 100%.
- [ ] Excluded rooms receive 0 hub candidates.
