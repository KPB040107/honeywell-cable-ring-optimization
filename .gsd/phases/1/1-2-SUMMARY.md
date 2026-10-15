# Phase 1 Plan 2 Summary: Provisional Benchmark Fixture, ResPlan Parser, Scale Normalization & Deterministic Polylabel Node Placement

## Objective
Construct the synthetic 6-room benchmark fixture based on PRD Section 5.4.3 area and topology requirements, explicitly declaring all unverified coordinates as provisional. Implement the ResPlan geometry parser with documented schema definitions, targeted repair logging, the metric scale normalizer, and the deterministic polylabel device node placement engine with strict 0.20 m post-snap clearance enforcement. Verify all Phase 1 gates via automated pytest suite.

## Deliverables & Audited Correctness
1. **Provisional Benchmark Fixture & Exact Metric Geometry**:
   - `tests/fixtures/schematic_6room.json`: Validated synthetic benchmark where eligible room areas sum to **exactly $118.0000\text{ m}^2$**:
     - `Living`: $40.0\text{ m}^2$
     - `Kitchen`: $16.0\text{ m}^2$
     - `Storage`: $15.0\text{ m}^2$
     - `Bedroom 1`: $16.0\text{ m}^2$
     - `Bedroom 2`: $16.0\text{ m}^2$
     - `Bedroom 3`: $15.0\text{ m}^2$
     - `Bathroom`: $8.1\text{ m}^2$ (excluded)
   - Verified that the 5 doors connect the exact intended room pairs (`Living`–`Bed 3`, `Bed 3`–`Bed 1`, `Living`–`Bed 1`, `Bed 2`–`Kitchen`, `Kitchen`–`Storage`) and satisfy all solid-wall exclusions.
   - `tests/fixtures/README.md` documents every coordinate vertex, room dimension, and provisional assumption.
2. **Schema Ingestion & Consistent Geometric Scaling (`phase1_ingestion/parser.py`)**:
   - Enforces supported schema versions (`resplan_wkt_v1`, `resplan_geo_v1`) and rejects unknown versions.
   - Requires explicit `coordinate_unit` (`"meter"` or `"pixel"`).
   - Consistent scaling: Applies metric scale factor $s = \sqrt{A_{\text{metadata}} / A_{\text{raw}}}$ to **all** geometry primitives (rooms, doors, front doors, inner bounds, and obstacle masks), not just room areas.
   - Targeted repair: Avoids unconditional repair; only triggers `make_valid` on invalid geometries and rejects area drift $> 1\%$.
3. **Metric Scale Calibration (`phase1_ingestion/scale_normalizer.py`)**:
   - Explicit calibration based on declared metadata; isolates `AmbiguousCoordinateUnitError`.
4. **Deterministic Safe Node Placement (`phase1_ingestion/node_placer.py`)**:
   - $0.25\text{ m}$ candidate erosion margin, $0.05\text{ m}$ grid snapping, and independent $\ge 0.20\text{ m}$ clearance verification.
   - Deterministic 8-neighbor recovery (tested against obstacle obstruction).
   - Narrow room test confirms explicit `InfeasibleNodePlacementError` specifically for clearance violation.
5. **Automated Pytest Suite (`tests/test_phase1_ingestion.py`)**:
   - 8 comprehensive unit and integration tests passing with 100% compliance.

## Empirical Verification
- **All Phase 1 Verification Commands Passed**:
  1. `Task 1.1`: Dependencies imported successfully.
  2. `Task 1.2`: Config profiles loaded and validated.
  3. `Task 1.3`: Contracts serialized/deserialized cleanly.
  4. `Task 2.1`: Parser successfully ingested provisional fixture (7 rooms, exact areas).
  5. `Task 2.2`: Node placer placed 6 safe nodes with clearance $\ge 0.20\text{ m}$.
- **Focused Pytest Suite**:
  - Command: `.venv/bin/pytest tests/test_phase1_ingestion.py -v`
  - Result: **8 passed in 0.07s (100% pass rate)**.

## Status
- **Phase 1 Status**: `AUDITED & FULLY COMPLETE`
- **Next Step**: Phase 2 Planning (`/plan 2`).
