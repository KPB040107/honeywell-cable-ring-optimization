# Test Fixtures & Synthetic Benchmark Provenance

## 1. Provisional 6-Room Benchmark: `schematic_6room.json`

> **Status**: `PROVISIONAL BENCHMARK`  
> **Source Baseline**: Honeywell Technologies / UR Hackathon 2026H2 PS4 Problem Statement, PRD v1 Section 5.4.3 (Test 4.1) and Section 5.2.3 (Test 2.1).

### Verified Ground-Truth Properties from Authoritative PRD
1. **Total Eligible Room Area**: Exactly $118.0000\text{ m}^2$
2. **Room Roster & Exact Areas**:
   - `Living`: $40.0\text{ m}^2$ (Eligible)
   - `Kitchen`: $16.0\text{ m}^2$ (Eligible)
   - `Bedroom 1`: $16.0\text{ m}^2$ (Eligible)
   - `Bedroom 2`: $16.0\text{ m}^2$ (Eligible, degree-1 leaf spur room)
   - `Bedroom 3`: $15.0\text{ m}^2$ (Eligible)
   - `Storage`: $15.0\text{ m}^2$ (Eligible, degree-1 leaf spur room)
   - `Bathroom`: $8.1\text{ m}^2$ (Excluded room, receives 0 devices)
   - **Sum of eligible areas**: $40.0 + 16.0 + 16.0 + 16.0 + 15.0 + 15.0 = 118.0\text{ m}^2$.
3. **Macro Dual-Graph Door Adjacencies**:
   - `Living` $\leftrightarrow$ `Bedroom 3`
   - `Bedroom 3` $\leftrightarrow$ `Bedroom 1`
   - `Living` $\leftrightarrow$ `Bedroom 1`
   - `Bedroom 2` $\leftrightarrow$ `Kitchen`
   - `Kitchen` $\leftrightarrow$ `Storage`
4. **Specified Solid-Wall Exclusions**:
   - Solid wall (no door): between `Living` and `Storage`
   - Solid wall (no door): between `Bedroom 1` and `Bedroom 2`
   - Solid wall (no door): between `Living` and `Bedroom 2`
5. **Expected Contiguous Partition**:
   - $K_{\min} = 2$, Hub count $K = 2$.
   - Zone 1: `{Living, Bedroom 1, Bedroom 3}` (Area: $71.0\text{ m}^2$, 3 devices).
   - Zone 2: `{Bedroom 2, Kitchen, Storage}` (Area: $47.0\text{ m}^2$, 3 devices).

### Provisional Geometric Assumptions (Unverified in Raw Source Material)
Because the official coordinate files for Slide 5/8 have not been provided in raw machine-readable JSON format, the following geometric details are modeled as provisional assumptions:
- **Room Shapes**: Modeled as orthogonal rectangles placed to satisfy all area and door adjacency requirements without self-intersection:
  - `Living`: $8.0\text{ m} \times 5.0\text{ m} = 40.0\text{ m}^2$, bounds $[0, 8] \times [0, 5]$.
  - `Bedroom 1` (Bed 16m): $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$, bounds $[0, 4] \times [5.15, 9.15]$.
  - `Bedroom 3` (Bed 15m): $5.0\text{ m} \times 3.0\text{ m} = 15.0\text{ m}^2$, bounds $[4.15, 9.15] \times [5.15, 8.15]$.
  - `Kitchen`: $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$, bounds $[9.45, 13.45] \times [0, 4.0]$.
  - `Bedroom 2` (Bed 16m): $4.0\text{ m} \times 4.0\text{ m} = 16.0\text{ m}^2$, bounds $[9.45, 13.45] \times [4.15, 8.15]$.
  - `Storage`: $3.75\text{ m} \times 4.0\text{ m} = 15.0\text{ m}^2$, bounds $[13.60, 17.35] \times [0, 4.0]$.
  - `Bathroom`: $2.7\text{ m} \times 3.0\text{ m} = 8.1\text{ m}^2$, bounds $[13.60, 16.30] \times [4.15, 7.15]$.
- **Wall Thickness**: $0.15\text{ m}$ wall depth separating interior rooms.
- **Doorway Thresholds**: Width $w = 1.0\text{ m}$ (or $0.15\text{ m} \times 1.0\text{ m}$ rectangle) bridging the $0.15\text{ m}$ wall gap between adjacent rooms.
- **Coordinate Units**: Declared explicitly as `coordinate_unit: "meter"`.

This fixture is marked with `"is_provisional": true` in its top-level metadata.
