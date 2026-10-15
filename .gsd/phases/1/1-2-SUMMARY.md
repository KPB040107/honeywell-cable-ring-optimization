# Phase 1 Plan 2 Summary: Provisional Benchmark Fixture, ResPlan Parser, Scale Normalization & Deterministic Polylabel Node Placement

## Objective
Construct the synthetic 6-room benchmark fixture based on PRD Section 5.4.3 area and topology requirements, explicitly declaring all unverified coordinates as provisional. Implement the ResPlan geometry parser with documented schema definitions, targeted repair logging, the metric scale normalizer, and the deterministic polylabel device node placement engine with strict 0.20 m post-snap clearance enforcement. Verify all Phase 1 gates via automated pytest suite.

## Deliverables & Changes
1. **Provisional Benchmark Fixture & Provenance Documentation**:
   - `tests/fixtures/schematic_6room.json`: Synthetic benchmark representing the 6 eligible rooms (118 m²: Living 40 m², Bed 1 16 m², Bed 2 16 m², Bed 3 15 m², Kitchen 16 m², Storage 15 m²) + 1 excluded Bathroom, with `is_provisional: true`.
   - `tests/fixtures/README.md`: Documents all verified facts from PRD v1 and explicitly lists all unverified coordinate and threshold assumptions.
2. **Schema Ingestion & Targeted Repair (`phase1_ingestion/parser.py`)**:
   - Supported schemas documented: `resplan_wkt_v1` and `resplan_geo_v1`.
   - Explicit unit handling: Rejects missing/ambiguous coordinate units with `AmbiguousCoordinateUnitError`.
   - Targeted repair: Avoids unconditional healing; only tests `geom.is_valid` and applies `make_valid` if invalid, rejecting excessive area drift (>1%) with `GeometryRepairError`.
3. **Metric Scale Calibration (`phase1_ingestion/scale_normalizer.py`)**:
   - Explicit calibration based on declared metadata; rejects ambiguous units.
4. **Deterministic Safe Node Placement (`phase1_ingestion/node_placer.py`)**:
   - 0.25 m candidate erosion providing 0.05 m buffer margin before 0.05 m grid snapping.
   - Independent post-snap clearance validator enforcing $\ge 0.20\text{ m}$ wall clearance.
   - Deterministic 8-neighbor grid search recovery; raises `InfeasibleNodePlacementError` if clearance is unachievable (e.g. 0.30 m corridor).
5. **Phase 1 Automated Test Suite (`tests/test_phase1_ingestion.py`)**:
   - 8 automated unit tests covering all Phase 1 requirements, schema rejections, repair drift, non-convex polylabel, clearance, narrow room infeasibility, and full serialization roundtrips.

## Empirical Verification
- Command: `.venv/bin/pytest tests/test_phase1_ingestion.py -v`
- Result: **8 passed in 0.12s (100% pass rate)**.
- Ingestion benchmark: Verified 7 rooms (6 eligible, 1 excluded) and 6 safely placed nodes with zero clearance violations.

## Status
- **Phase 1 Plan 2 Status**: `COMPLETE`
- **Milestone Phase 1 Status**: `COMPLETE`
