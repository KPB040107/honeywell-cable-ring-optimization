# Test Fixtures & Synthetic Benchmark Provenance

## 1. Provisional 6-Room Benchmark: `schematic_6room.json`

> **Status**: `PROVISIONAL BENCHMARK`  
> **Source Baseline**: Honeywell Technologies / UR Hackathon 2026H2 PS4 Problem Statement, PRD v1 Section 5.4.3 (Test 4.1) and Section 5.2.3 (Test 2.1).

### Verified Ground-Truth Properties from Authoritative PRD
1. **Total Eligible Room Area**: $118.0\text{ m}^2$
2. **Room Roster & Areas**:
   - `Living`: $40.0\text{ m}^2$ (Eligible)
   - `Bedroom 1`: $16.0\text{ m}^2$ (Eligible)
   - `Bedroom 2`: $16.0\text{ m}^2$ (Eligible, degree-1 leaf spur room)
   - `Bedroom 3`: $15.0\text{ m}^2$ (Eligible)
   - `Kitchen`: $16.0\text{ m}^2$ (Eligible)
   - `Storage`: $15.0\text{ m}^2$ (Eligible, degree-1 leaf spur room)
   - `Bathroom`: $9.0\text{ m}^2$ (Excluded room, receives 0 devices)
3. **Macro Dual-Graph Door Adjacencies**:
   - `Living` $\leftrightarrow$ `Bedroom 3`
   - `Bedroom 3` $\leftrightarrow$ `Bedroom 1`
   - `Living` $\leftrightarrow$ `Bedroom 1`
   - `Bedroom 2` $\leftrightarrow$ `Kitchen`
   - `Kitchen` $\leftrightarrow$ `Storage`
   - Solid wall: between `Living` and `Storage`; between `Bedroom 1` and `Bedroom 2`.
4. **Expected Partition**:
   - $K_{\min} = 2$, Hub count $K = 2$.
   - Zone 1: `{Living, Bedroom 1, Bedroom 3}` (Area: $71.0\text{ m}^2$, 3 devices).
   - Zone 2: `{Bedroom 2, Kitchen, Storage}` (Area: $47.0\text{ m}^2$, 3 devices).

### Provisional Geometric Assumptions (Unverified in Raw Source Material)
Because the official coordinate files for Slide 5/8 have not been provided in raw machine-readable JSON format, the following geometric details are modeled as provisional assumptions:
- **Room Shapes**: Modeled as orthogonal axis-aligned rectangles respecting the exact verified metric areas.
  - `Living`: $8.0\text{ m} \times 5.0\text{ m} = 40.0\text{ m}^2$
  - `Bedroom 1`: $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$
  - `Bedroom 2`: $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$
  - `Bedroom 3`: $5.0\text{ m} \times 3.0\text{ m} = 15.0\text{ m}^2$
  - `Kitchen`: $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$
  - `Storage`: $5.0\text{ m} \times 3.0\text{ m} = 15.0\text{ m}^2$
  - `Bathroom`: $3.0\text{ m} \times 3.0\text{ m} = 9.0\text{ m}^2$
- **Wall Thickness**: $0.15\text{ m}$ wall depth (`wall_depth: 0.15`).
- **Doorway Thresholds**: Width $w = 0.90\text{ m}$ placed at the center of the shared boundary interfaces.
- **Coordinate Units**: Explicitly declared as `coordinate_unit: "meter"`.

This fixture is marked with `"is_provisional": true` in its top-level metadata.
