# Troubleshooting & Geometry Healing FAQ

This guide provides troubleshooting procedures for common runtime issues, platform-specific operating nuances, and an engineering FAQ regarding CAD geometry healing and the ResPlan dataset.

---

## 1. Common Runtime Issues & Solutions

### Issue 1: "Rule 6 Violation: Hub Clearance < 0.50 m"

#### Symptom
```text
[FAIL] Rule 6: Hub Clearance (>= 0.50 m from walls/windows)
- [Rule 6] Hub Clearance Violation at (12.40, 5.20): Distance to Wall W04 is 0.42 m (< 0.50 m required)
```

#### Cause
Every placed hub must maintain at least a **0.50 m clearance zone** (1.0 m diameter circle) from all wall obstacles and outer boundary lines. If a room is very narrow (e.g., hallway or utility closet under 1.0 m width), no valid hub placement position exists inside that room.

#### Solution
1. In the dual-graph partitioning phase, ensure narrow non-viable rooms are grouped with adjacent living/bed rooms whose largest inscribed circle has radius $r \ge 0.50\text{ m}$.
2. If the entire floor plan consists of rooms under 1.0 m width, the plan is classified as **physically infeasible** (see [The 22 Infeasible Plans](#the-22-physically-infeasible-plans)).
3. Enable geometry recovery preprocessing:
   ```python
   from resplan_router.phase1_ingestion.geometry_healing import recover_and_certify_plan
   healed_plan, report = recover_and_certify_plan(raw_plan_data)
   ```

---

### Issue 2: "Rule 5 Violation: Cable penetrates wall or window"

#### Symptom
```text
[FAIL] Rule 5: Free Space (axis-aligned, no wall/window crossing)
- [Rule 5] Wall Collision at segment [(25.0, 10.0) -> (25.0, 30.0)] intersects Wall W02 by 0.08 m
```

#### Cause
Rectilinear cable paths must travel exclusively within the interior polygon `inner` and through registered door openings (`portals`). A collision occurs if:
1. A wall obstacle geometry is missing a registered door opening in the JSON schematic.
2. The Hanan grid coordinates did not project a grid line aligned with the door portal midpoint.

#### Solution
- Verify that door portal coordinates align with the door opening gaps in the wall segment list.
- Use `extract_all_portals(plan, raw_doors)` to automatically compute portal midpoints and project them into the directional Hanan grid before A* pathfinding.

---

### Issue 3: "Rule 3 Violation: Doorway Contiguity Failure"

#### Symptom
```text
[FAIL] Rule 3: Doorway Contiguity (rooms door-connected)
- [Rule 3] Multi-room zone {R01, R04} has no door connecting R04 to the zone component
```

#### Cause
Rooms assigned to the same control hub must be reachable by walking through legal interior doorways or open passages without passing through solid walls or outdoor space. If two rooms share a partition wall with no door, grouping them violates Rule 3.

#### Solution
- Check the macro dual-graph topology with `MacroTopologyGraph.build_topology(plan, portals)`. Ensure that every room assigned to a zone has a valid path in the dual graph within that zone's subgraph.

---

### Issue 4: "Solver Timeout on High-Density Floor Plans"

#### Symptom
Optimization exceeds 10 seconds or memory consumption spikes on plans with $> 20$ rooms or dense obstacle walls.

#### Solution
- Use the **`ProductionFallbackOptimizer`**, which uses the fast LinUCB-ALNS v2 algorithm with precomputed Hanan grids:
  ```python
  from resplan_router.optimizers.production_fallback import ProductionFallbackOptimizer
  opt = ProductionFallbackOptimizer(timeout_s=5.0)
  result = opt.optimize(plan)
  ```
- Adjust ALNS iterations from the default 50 to 25 (`--config configs/fast.yaml`).

---

## 2. Platform-Specific Nuances

### Windows 10 & 11

#### Console Character Encoding (Garbled `ΔA_gap` or `90°`)
On Windows Command Prompt or legacy PowerShell, default code pages (like `cp1252`) display unicode mathematical characters as `?` or distorted bytes.
- **Fix:** Run `chcp 65001` before executing CLI commands, or use modern **Windows Terminal**. The `resplan_router` entrypoints automatically attempt to reconfigure `sys.stdout` to UTF-8 with error replacement.

#### Path Length Limitations (`MAX_PATH = 260`)
If cloning the repository into deeply nested folders, Windows may throw `FileNotFoundError` during file creation.
- **Fix:** Enable Long Path support in Windows PowerShell (Admin):
  ```powershell
  Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1
  ```

#### Shapely / GEOS C-Extensions
`resplan_router` requires `shapely >= 2.0.0` (which utilizes the vectorized GEOS C API).
- **Fix:** Do not install Shapely 1.8.x. Always run `pip install --upgrade "shapely>=2.0.0"`.

---

### Linux (Ubuntu / Headless Servers)

#### Matplotlib GUI Backend Warnings
When running batch benchmarks over SSH on a headless server without an X11 server, Matplotlib may warn: `Cannot connect to display`.
- **Fix:** Set the headless backend before running commands:
  ```bash
  export MPLBACKEND=Agg
  ```

---

## 3. Geometry Healing FAQ

### Q1: What was the "6-Node Anomaly" (Plan 64)?
**Answer:** In raw ResPlan CAD files, the outer cadastral land boundary was tagged with the polygon name `"land"` ($232.96\text{ m}^2$). Because `"land"` enveloped the entire building, unhealed geometry checkers treated it as an interior room that intersected all perimeter walls and doorways. In `portal_extractor.py`, strict checks requiring `len(matching_rooms) == 2` failed because `"land"` added a third matching room, causing all doors to be dropped and forcing every room to receive its own hub ($H = 6$).
- **The Fix:** The Phase 1/2 ingestion pipeline explicitly purges `"land"` cadastral plots, identifies true interior rooms, and correctly routes Plan 64 with **$H^* = 3$ hubs** (100% valid).

### Q2: Why is "Doorway Wall-Bridging" necessary?
**Answer:** Architectural CAD plans often model walls with physical thickness (typically $0.16\text{ m}$ to $0.25\text{ m}$). Consequently, the polygons of adjacent rooms do not share a zero-thickness mathematical boundary; they are separated by a solid wall corridor. Zero-width polyline door symbols often terminate at room edges without penetrating through the full wall thickness.
- **The Fix:** The `portal_extractor` constructs an epsilon-inflated bridge ($0.05\text{ m}$ buffer) across partition wall gaps, connecting room interior spaces and building a topologically sound dual graph.

### Q3: What are the "22 Physically Infeasible Plans"?
**Answer:** Across the 17,000 ResPlan dataset, rigorous audit proved that **exactly 22 floor plans cannot be solved by any valid algorithm** under the Honeywell competition rules due to corrupt architectural source data:
1. **14 Plans with Inadequate Clearance ($< 0.50\text{ m}$):** Plans where every room has a width $< 1.00\text{ m}$ or obstacles occupy the entire interior, making 0.50 m clearance mathematically impossible (e.g. Plan IDs `5460`, `15000`, `15678`, `145814`, `345536`).
2. **8 Plans with Disconnected Degree-0 Rooms:** Plans containing rooms completely isolated by solid un-windowed walls with zero doors (e.g. Plan IDs `452`, `778`, `4048`, `7447`, `8272`).
The production runner catalogs these 22 plans in `PROVEN_PHYSICAL_INFEASIBLE_IDS` and flags them with diagnostic certificates rather than halting batch execution.

### Q4: Why is a strict 1.0 mm tolerance enforced?
**Answer:** Real-world cable installation allows zero tolerance for physical collisions with structural walls and windows. An independent adversarial oracle (`FloorPlanChecker`) checks every cable segment using high-precision GEOS intersection predicates with a $1.0\text{ mm}$ ($0.001\text{ m}$) threshold. Any route clipping an obstacle boundary by more than $1\text{ mm}$ is immediately flagged as invalid.

---

## 4. Getting Help & Reporting Issues

If you encounter unexpected routing behavior or suspect a corrupt CAD floor plan:
1. Run the independent oracle checker:
   ```bash
   python -m resplan_router.cli -i <your_plan.json> -v debug_overlay.png
   ```
2. Inspect the generated `debug_overlay.png` to visually locate flagged collisions or clearance buffer overlap.
3. Consult the complete engineering discovery report: [`reports/final_technical_research_report.md`](file:///c:/hackhive/reports/final_technical_research_report.md).
