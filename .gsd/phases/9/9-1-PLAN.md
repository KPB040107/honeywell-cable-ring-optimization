---
phase: 9
plan: 1
wave: 1
gap_closure: false
---

# Plan 9.1: NFPA 72 Class A Circuit Simulator & Survivability Engine

## Objective
Implement the NFPA 72 Class A wire-cut survivability simulation engine (`circuit_simulator.py`) and data contracts (`contracts.py`). The engine models the bidirectional electrical circuit topology of closed low-voltage loops, simulates single and multiple open-circuit wire cuts on arbitrary cable segments, calculates split-branch path lengths, and empirically proves the 100% device survivability theorem for closed rings.

## Context
- `v2.md` (Section 7, Phase 9)
- `.gsd/SPEC.md`
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase5_ring_sequencing/exact_ring_tsp.py`
- `src/resplan_router/phase5_ring_sequencing/loop_synthesizer.py`

## Tasks

<task type="auto">
  <name>NFPA 72 Class A Survivability Contracts & Circuit Simulator Core</name>
  <files>
    src/resplan_router/contracts.py
    src/resplan_router/phase9_survivability/__init__.py
    src/resplan_router/phase9_survivability/circuit_simulator.py
  </files>
  <action>
    1. Update `src/resplan_router/contracts.py` with Phase 9 survivability contracts:
       - `PowerSource(str, Enum)`: `PRIMARY_OUTBOUND = "primary_outbound"`, `RETURN_TERMINAL = "return_terminal"`, `OFFLINE = "offline"`.
       - `WireCutLocation`:
         - `hub_id: str`.
         - `leg_index: int`.
         - `from_node: str`.
         - `to_node: str`.
         - `cut_point: Point2D`.
         - `distance_along_leg_m: float`.
       - `NodeSurvivabilityStatus`:
         - `node_id: str`.
         - `is_online: bool`.
         - `power_source: PowerSource`.
         - `path_length_m: float`.
         - `hop_count: int`.
       - `ClassASimulationReport`:
         - `plan_id: str`.
         - `hub_id: str`.
         - `is_severed: bool`.
         - `cut_location: WireCutLocation | None`.
         - `total_nodes: int`.
         - `online_nodes: int`.
         - `survivability_pct: float` (e.g. 100.0%).
         - `primary_branch_nodes: list[str]`.
         - `return_branch_nodes: list[str]`.
         - `offline_nodes: list[str]`.
         - `node_statuses: dict[str, NodeSurvivabilityStatus]`.
         - `nfpa72_class_a_compliant: bool` (True iff 100% nodes remain online under any single cut).
         - `max_latency_path_m: float`.

    2. Create `src/resplan_router/phase9_survivability/__init__.py`.

    3. Implement `src/resplan_router/phase9_survivability/circuit_simulator.py`:
       - Class `ClassACircuitSimulator`:
         - Method `simulate_wire_cut(ring: CompletedHubRing, cut_leg_index: int, cut_fraction: float = 0.5) -> ClassASimulationReport`:
           - Identifies severed segment between `ring.closed_sequence[cut_leg_index]` and `ring.closed_sequence[cut_leg_index + 1]`.
           - Computes cut point coordinate along the leg's polyline at `cut_fraction`.
           - Decomposes the closed cycle $H_k \to N_1 \to \dots \to N_m \to H_k$ into two independent path branches:
             - **Primary Outbound Branch:** $H_k \to N_1 \to \dots \to N_c$ (nodes upstream of cut).
             - **Inbound Return Terminal Branch:** $H_k \to N_m \to \dots \to N_{c+1}$ (nodes downstream of cut, powered in reverse).
           - Computes exact cable path length and hop count from $H_k$ to each node along its active branch.
           - Evaluates node online status:
             - For any single cut on a simple closed cycle, every node belongs to either the primary or return branch. Thus `survivability_pct == 100.0%` and `nfpa72_class_a_compliant == True`.
         - Method `simulate_arbitrary_cut_point(ring: CompletedHubRing, cut_x: float, cut_y: float) -> ClassASimulationReport`:
           - Finds closest segment in `ring.route_segments` to $(x, y)$, projects $(x, y)$ onto the polyline, and executes `simulate_wire_cut`.
         - Method `simulate_multi_cut(ring: CompletedHubRing, cut_leg_indices: list[int]) -> ClassASimulationReport`:
           - Simulates $k \ge 2$ simultaneous cuts; identifies isolated sub-paths (nodes severed from both $H_k$ terminals) and computes degraded survivability (e.g. 66.7%).
         - Method `verify_loop_resilience_exhaustively(ring: CompletedHubRing) -> dict`:
           - Iterates through EVERY leg of the closed ring $[0, \dots, m]$, runs `simulate_wire_cut`, and asserts that in 100% of single-cut scenarios, survivability is 100.0%.

    AVOID: Simple boolean flags; calculate precise path lengths and signal propagation branches.
    USE: Graph path traversal on the directed loop decomposition.
  </action>
  <verify>
    python -c "from resplan_router.phase9_survivability.circuit_simulator import ClassACircuitSimulator; print('Circuit simulator imported successfully')"
  </verify>
</task>

<task type="auto">
  <name>Exhaustive Resilience Tests & Single-Door Lane Fault Isolation</name>
  <files>
    tests/test_phase9_survivability.py
  </files>
  <action>
    Implement `tests/test_phase9_survivability.py`:
    1. **Test 1 (`test_slide5_exhaustive_single_cut_survivability`):**
       - Evaluates Hub 1 (`H1 -> N1 -> N3 -> N2 -> H1`) and Hub 2 (`H2 -> N4 -> N5 -> N6 -> H2`) from Slide 5 schematic.
       - Runs `verify_loop_resilience_exhaustively()` across every single leg of both rings.
       - Asserts that for all cuts:
         - `survivability_pct == 100.0%`.
         - `offline_nodes == []`.
         - `nfpa72_class_a_compliant is True`.
    2. **Test 2 (`test_single_door_leaf_room_fault_isolation`):**
       - Specifically tests a cut on the outbound leg entering single-door leaf room `Bedroom 2` (Door Lane A).
       - Asserts that because Door Lane B is physically separated by $\ge 200\text{ mm}$, Lane B is NOT severed; the hub return terminal successfully powers the node inside `Bedroom 2` from the reverse direction!
    3. **Test 3 (`test_cut_at_arbitrary_spatial_coordinate`):**
       - Simulates a cut at physical coordinate $(6.8, 1.2)$ along segment $N_1 \to N_3$.
       - Verifies that $N_1$ is powered by Primary Outbound and $N_3, N_2$ are powered by Return Terminal.
       - Verifies hop count and path lengths.
    4. **Test 4 (`test_multi_cut_degradation_detection`):**
       - Simulates 2 simultaneous cuts on legs 0 and 2.
       - Asserts simulator accurately detects that intermediate nodes between the two cuts are severed from both terminals and marked `OFFLINE`.
       - Asserts `nfpa72_class_a_compliant is False` for multiple cuts.
    5. **Test 5 (`test_contract_serialization_roundtrip`):**
       - Validates Pydantic serialization of `ClassASimulationReport` to JSON and back.
  </action>
  <verify>
    pytest tests/test_phase9_survivability.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase9_survivability.py -v`.
- Re-run full regression suite `pytest tests/ -v`.

## Success Criteria
- 100% device survivability mathematically proven and empirically verified for all single wire cuts.
- Accurate split-branch attribution (Primary Outbound vs. Return Terminal) with path lengths.
- Multi-cut fault isolation accurately identifies offline sub-paths.
- All unit tests pass with 100% success rate.
