---
phase: 2
plan: 1
wave: 1
gap_closure: false
---

# Plan 2.1: Door & Open-Passage Portal Extraction & Contracts

## Objective
Define the Phase 2 data contracts (`PortalType`, `DoorOrPassagePortal`, `PortalTopologyGraph`) and implement the geometric portal extraction engine (`portal_extractor.py`) that identifies interior doors and open passages ($\ge 0.50\text{ m}$ width) between rooms, strictly rejecting solid walls, windows, and the building entrance (`front_door`).

## Context
- `v2.md` (Sections 2.1, 2.2, 4, 5.2)
- `.gsd/SPEC.md`
- `.gsd/phases/2/RESEARCH.md`
- `src/resplan_router/contracts.py`

## Tasks

<task type="auto">
  <name>Phase 2 Pydantic Data Contracts</name>
  <files>
    src/resplan_router/contracts.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` to add Phase 2 contracts:
       - `PortalType(str, Enum)`: `DOOR = "door"`, `OPEN_PASSAGE = "open_passage"`.
       - `DoorOrPassagePortal`:
         - `portal_id: str`: unique ID (e.g. `P01`, `P02`).
         - `portal_type: PortalType`.
         - `room1_id: str`, `room2_id: str`: connected room IDs.
         - `center_point: Point2D`: midpoint of threshold.
         - `threshold_wkt: str`: LineString representing the threshold segment.
         - `width_m: float`: width of doorway gap in metres ($> 0.0$).
         - `lane_a_point: Point2D`: offset parallel waypoint for incoming transit.
         - `lane_b_point: Point2D`: offset parallel waypoint for outgoing transit.
         - `step_room1: Point2D`: orthogonal room step point inside room1.
         - `step_room2: Point2D`: orthogonal room step point inside room2.
         - `lateral_offset_m: float`: offset distance $\ge 0.02\text{ m}$ ($20\text{ mm}$).
       - `PortalTopologyGraph`:
         - `plan_id: str`.
         - `eligible_room_ids: list[str]`.
         - `portals: list[DoorOrPassagePortal]`.
         - `adjacency: dict[str, list[str]]`.
         - `is_fully_connected: bool`.
         - `leaf_room_ids: list[str]`: rooms with degree 1 in topology.
    2. Add validators verifying threshold WKT is non-empty LineString and lateral offset $\ge 0.02\text{ m}$.

    AVOID: Ad-hoc dictionaries for portal waypoints.
    USE: Immutable Pydantic models with type annotations and WKT validation.
  </action>
  <verify>
    python -c "from resplan_router.contracts import DoorOrPassagePortal, PortalType; print('Contracts syntax valid')"
  </verify>
  <done>
    `PortalTopologyGraph` and `DoorOrPassagePortal` models import and validate successfully.
  </done>
</task>

<task type="auto">
  <name>Door & Open-Passage Portal Extractor</name>
  <files>
    src/resplan_router/phase2_topology/__init__.py
    src/resplan_router/phase2_topology/portal_extractor.py
  </files>
  <action>
    1. Create `src/resplan_router/phase2_topology/__init__.py`.
    2. Implement `src/resplan_router/phase2_topology/portal_extractor.py`:
       - Function `extract_door_portals(plan: MetricFloorPlan, raw_doors: list[Polygon], raw_entrance: Optional[Polygon] = None) -> list[DoorOrPassagePortal]`:
         - Exclude doors within $0.05\text{ m}$ of `plan.inner` boundary or intersecting `raw_entrance`.
         - For each interior door: buffer by $0.05\text{ m}$ and query `STRtree` over `plan.rooms.values()`.
         - If exactly 2 rooms intersect: compute threshold LineString = shared boundary $\cap$ door polygon.
         - Calculate threshold midpoint $p_0$, tangent vector $\hat{t}$, normal vector $\hat{n}$.
         - Calculate $\delta = \min(0.10, w / 4) \ge 0.02\text{ m}$.
         - Compute $p_A = p_0 - \delta \hat{t}$, $p_B = p_0 + \delta \hat{t}$, and step points $p_0 \pm 0.15\hat{n}$.
       - Function `extract_open_passages(plan: MetricFloorPlan, existing_portals: list[DoorOrPassagePortal]) -> list[DoorOrPassagePortal]`:
         - For each pair of adjacent rooms $(R_a, R_b)$: compute shared boundary $\Gamma_{ab} = \partial R_a \cap \partial R_b$.
         - Clip shared boundary against `plan.free_space_geom` and subtract existing door portals.
         - If remaining contiguous segment length $w \ge 0.50\text{ m}$, construct an `OPEN_PASSAGE` portal with parallel lanes.
       - Function `extract_all_portals(plan: MetricFloorPlan, raw_doors: list[Polygon], raw_entrance: Optional[Polygon] = None) -> list[DoorOrPassagePortal]`.

    AVOID: Connecting rooms across solid walls, windows, or building entrances.
    USE: Strict intersection with door polygons or verified free-space gaps $\ge 0.50\text{ m}$.
  </action>
  <verify>
    python -c "from resplan_router.phase2_topology.portal_extractor import extract_all_portals; print('portal_extractor imported successfully')"
  </verify>
  <done>
    `portal_extractor.py` detects interior door gaps and open passages with valid geometry.
  </done>
</task>

<task type="auto">
  <name>Portal Extraction Unit Tests</name>
  <files>
    tests/test_phase2_portals.py
  </files>
  <action>
    1. Create `tests/test_phase2_portals.py` testing:
       - Extraction of doors on synthetic two-room and three-room floor plans.
       - Verification that doors intersecting the entrance (`front_door`) or outer wall boundary are ignored.
       - Verification that shared solid walls without doors produce 0 portals.
       - Verification that shared boundaries with open gaps $\ge 0.50\text{ m}$ generate `OPEN_PASSAGE` portals.
       - Verification that threshold segments lie inside free space.

    AVOID: Testing only the happy path.
    USE: Negative assertions for wall-only adjacency and window adjacency.
  </action>
  <verify>
    pytest tests/test_phase2_portals.py -v
  </verify>
  <done>
    Portal extractor unit tests pass 100% with solid wall/window rejections confirmed.
  </done>
</task>

## Must-Haves
- [ ] Doors buffered by $0.05\text{ m}$ and matched to intersecting room pairs via STRtree.
- [ ] Open passages detected for shared boundaries inside free space with width $\ge 0.50\text{ m}$.
- [ ] Windows, solid walls, and front doors produce 0 portal connections.

## Success Criteria
- [ ] `pytest tests/test_phase2_portals.py` passes 100%.
- [ ] All generated portals have threshold line strings inside `MetricFloorPlan.free_space_wkt`.
