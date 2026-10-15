---
phase: 6
plan: 2
wave: 2
gap_closure: false
---

# Plan 6.2: Slide 8 JSON Exporter, Slide 5 Matplotlib Visualizer & End-to-End Pipeline

## Objective
Implement the exact Slide 8 competition JSON serializer (`json_exporter.py`), the two-column Slide 5 architectural floor-plan overlay renderer with red violation badges (`visualizer.py`), and the master end-to-end routing pipeline (`pipeline.py`) integrating Phases 1 through 6 with a full CLI entrypoint.

## Context
- `v2.md` (Sections 2.1, 5.6, 6)
- `Cable Ring Challenge - Problem Statement4.pptx` (Slide 5, Slide 8)
- `src/resplan_router/contracts.py`
- `src/resplan_router/phase6_output/checker.py`
- `src/resplan_router/phase6_output/json_exporter.py`
- `src/resplan_router/phase6_output/visualizer.py`
- `src/resplan_router/phase6_output/pipeline.py`
- `tests/test_integration_pipeline.py`

## Tasks

<task type="auto">
  <name>Slide 8 JSON Exporter & Slide 5 Visualizer</name>
  <files>
    src/resplan_router/phase6_output/json_exporter.py
    src/resplan_router/phase6_output/visualizer.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase6_output/json_exporter.py`:
       - Function `export_slide8_json(plan: MetricFloorPlan, bundle: SynthesizedRingsBundle, partition: HubZonePartition | None = None, output_path: str | Path | None = None) -> dict`:
         - Builds dictionary adhering verbatim to Slide 8 JSON format:
           ```json
           {
             "plan_id": "<plan_id>",
             "units": "m",
             "hubs": [
               {
                 "hub_id": "H1",
                 "room_id": "R01",
                 "location": {"x": 5.2, "y": 3.6},
                 "assigned_rooms": ["R01", "R02", "R03"],
                 "assigned_nodes": ["N1", "N2", "N3"],
                 "closed_sequence": ["H1", "N1", "N3", "N2", "H1"],
                 "route_segments": [
                   {
                     "from": "H1",
                     "to": "N1",
                     "length_m": 4.8,
                     "polyline": [[5.2, 3.6], [7.6, 3.6], [7.6, 1.2]]
                   }
                 ],
                 "ring_length_m": 23.8
               }
             ],
             "reported_metrics": {
               "hub_count": 2,
               "total_length_m": 49.0
             }
           }
           ```
         - Coordinates rounded to 4 decimal places ($0.1\text{ mm}$ precision).
         - If `output_path` is specified, writes formatted JSON with indent 2.
         - Validates that output parses into Pydantic models cleanly.

    2. Implement `src/resplan_router/phase6_output/visualizer.py`:
       - Class `FloorPlanVisualizer`:
         - Method `render_overlay(plan: MetricFloorPlan, bundle: SynthesizedRingsBundle, evaluation: EvaluationReport | None = None, output_path: str | Path | None = None, title: str | None = None) -> Figure`:
           - Produces a high-quality two-column Matplotlib figure replicating Slide 5:
             - **Left Column (Floor Plan Canvas):**
               - Zone fills with distinct pastel colors (`#DCE8F8` soft blue for H1, `#FDE5D2` soft orange for H2, `#D8F3DC` soft mint for H3, etc.).
               - Excluded rooms filled with neutral grey (`#F0F0F0`) and labeled `(no node)`.
               - Dark charcoal boundary walls (`#222222`) with doorway gaps clearly open.
               - Bronze entrance marker at front door.
               - Room text labels with room name and computed area in $\text{m}^2$.
               - Axis-aligned cable routes plotted in high-contrast ring colors (`#1F77B4`, `#E6550D`, etc.) with slight line widths and directional arrows.
               - Parallel doorway runs visibly separated by $\ge 20\text{ mm}$ without visual merging.
               - Circled leg order badges (`1`, `2`, `3`, `4`) with white background at segment midpoints.
               - Hub markers: bold squares (`H1`, `H2`) with hub labels.
               - Node markers: bold circles (`N1`, `N2`, `N3`) with node labels.
             - **Red Violation Callout Overlay (Slide 3 deliverable requirement):**
               - If `evaluation` is provided and contains violations (`is_valid == False`):
                 - Mark offending locations with bright crimson badges / arrows (`#E6194B`).
                 - Annotate with diagnostic text box (e.g. `[Rule 5 VIOLATION] Wall Crossing`).
             - **Right Column (Sidebar Reviewer Cards):**
               - "How to read" legend (Zone fills, Hub markers, Node markers, Cable routes, Leg sequence).
               - "Per-hub summary" table/cards listing each hub's area ($m^2$), node count, cable length $L$, bends $B$, crossings $X$, overlap $O$, and cost $J$.
               - "What a reviewer confirms" 6-rule checklist:
                 - `[PASS]` All nodes served; 1 node per room
                 - `[PASS]` Max 5 nodes & <= 100 m² per hub
                 - `[PASS]` Hub in its assigned zone; rooms door-connected
                 - `[PASS]` Closed ring (hub -> all nodes -> hub)
                 - `[PASS]` Axis-aligned cables; free space only
                 - `[PASS]` Hubs >= 0.5 m from walls
                 - (Displaying `[FAIL]` in red if any violation is recorded).
           - Supports saving to PNG/SVG/PDF (`dpi=300`) and headless rendering (`plt.close(fig)`).

    AVOID: Overlapping text labels, illegible legend, or hardcoded figures.
    USE: Matplotlib patches (`Polygon`, `FancyArrowPatch`, `Circle`, `Rectangle`) with clean architectural styling.
  </action>
  <verify>
    python -c "from resplan_router.phase6_output.json_exporter import export_slide8_json; from resplan_router.phase6_output.visualizer import FloorPlanVisualizer; print('Exporter and visualizer imported successfully')"
  </verify>
</task>

<task type="auto">
  <name>End-to-End Routing Pipeline & Integration Test</name>
  <files>
    src/resplan_router/phase6_output/pipeline.py
    src/resplan_router/cli.py
    tests/test_integration_pipeline.py
  </files>
  <action>
    1. Implement `src/resplan_router/phase6_output/pipeline.py`:
       - Function `route_floor_plan(raw_plan_dict_or_path: dict | str | Path, config: SystemConfig | None = None, output_json_path: str | Path | None = None, render_png_path: str | Path | None = None) -> tuple[dict, EvaluationReport, SynthesizedRingsBundle]`:
         - Orchestrates the full pipeline:
           1. Phase 1: Ingestion & scale normalization (`parse_floor_plan`).
           2. Phase 2: Portal extraction & topology graph (`extract_all_portals`, `MacroTopologyGraph.build_topology`).
           3. Phase 3: Hanan grid & pairwise directional cost tensor (`build_hanan_grid`, `PairwiseCostTensor.build_from_plan`).
           4. Phase 4: Hub candidate generation & exact contiguous zone partition (`sample_hub_candidates`, `solve_zone_partition`).
           5. Phase 5: Direction-aware Held-Karp sequence & zero-overlap polyline loop synthesizer (`LoopSynthesizer.synthesize_all_rings`).
           6. Phase 6: Independent evaluation checker (`FloorPlanChecker.evaluate`), Slide 8 JSON export (`export_slide8_json`), and visualization overlay (`FloorPlanVisualizer.render_overlay`).
         - Returns `(exported_json, evaluation_report, synthesized_bundle)`.
         - Ensures total execution time on benchmark schematic is $< 1.5\text{ s}$.

    2. Create `src/resplan_router/cli.py`:
       - CLI arguments: `--input` / `-i` (path to input floor plan JSON), `--output` / `-o` (path to output Slide 8 JSON), `--visualize` / `-v` (path to output PNG schematic), `--max-nodes` (optional override).
       - Exits 0 on valid solution, 1 on hard rule violation. Prints formatted reviewer scorecard to stdout.

    3. Implement `tests/test_integration_pipeline.py`:
       - **Test 1 (`test_end_to_end_slide5_pipeline`):**
         - Runs `route_floor_plan` on `tests/fixtures/slide5_schematic.json`.
         - Asserts `evaluation_report.is_valid is True`.
         - Asserts `evaluation_report.hub_count == 2`.
         - Asserts `evaluation_report.total_length_m == 49.0`.
         - Asserts `evaluation_report.total_bends == 20`.
         - Asserts `evaluation_report.total_crossings == 0`.
         - Asserts `evaluation_report.total_overlap_m == 0.0`.
         - Asserts `evaluation_report.total_cost_J == 59.0`.
         - Asserts Slide 8 JSON dictionary matches schema with all required keys.
         - Verifies visualizer renders image file and size $> 0$.
       - **Test 2 (`test_cli_invocation`):**
         - Runs `cli.py` via `subprocess.run` on Slide 5 fixture with `--output` and `--visualize`.
         - Asserts exit code 0 and verifies generated files exist on disk.
       - **Test 3 (`test_full_suite_regression_gate`):**
         - Ensures full integration across all phases.
  </action>
  <verify>
    pytest tests/test_integration_pipeline.py tests/test_phase6_output.py -v
  </verify>
</task>

## Verification
- Run `pytest tests/test_phase6_output.py tests/test_integration_pipeline.py -v`.
- Re-run full suite `pytest tests/ -v` to ensure zero regressions across all 6 phases.
- Run CLI check: `python -m resplan_router.cli -i tests/fixtures/slide5_schematic.json -o /tmp/slide5_solution.json -v /tmp/slide5_overlay.png`.

## Success Criteria
- Slide 8 JSON export strictly conforms to schema keys and $0.1\text{ mm}$ coordinate precision.
- Visualizer renders Slide 5 matching two-column layout with zone fills, labels, arrows, leg badges, reviewer scorecard, and red violation callouts.
- Pipeline executes end-to-end in $< 1.5\text{ s}$ on Slide 5 schematic, achieving $J = 59.0$, $X = 0$, $O = 0.0\text{ m}$.
- 100% tests passing across entire repository.
