# ResPlan Cable Ring Router

A Python project for placing control hubs and routing closed cable rings through a residential floor plan. It reads a plan as JSON, chooses hub zones and cable paths, checks the challenge rules, and exports the solution in the Slide 8 JSON format. A local browser studio adds live inspection and interactive demonstrations.

The repository contains the six-phase routing foundation and the Phase 7–10 interactive extension described in [v2.md](v2.md). The [challenge deck](Cable%20Ring%20Challenge%20-%20Problem%20Statement4.pptx) defines the competition rules. The [Phase 1–7 explainer](docs/Phase_1_to_7_Explained.docx) is a presentation guide with diagrams and judge questions.

## The challenge in brief

Each eligible room has a fixed device node. A hub serves a group of rooms, and its cable must leave the hub, visit each assigned node once, and return to that same hub. Routes must stay in free space, use horizontal and vertical segments, and pass between rooms through doors or open passages.

A valid submission must satisfy six hard rules:

1. Cover every eligible room and node.
2. Keep each hub at or below **100 m²** and **five nodes**.
3. Give each hub a door-connected group of rooms.
4. Make each hub's route one closed, nonbranching ring.
5. Avoid walls and windows with axis-aligned cable paths.
6. Place each hub in an assigned eligible room at least **0.50 m** from walls and windows.

Solutions are ranked first by the number of hubs **H**, then by **J = L + 5X + O + 0.5B**, then by the area gap between hub zones. Here **L** is cable length in metres, **X** is crossings, **O** is shared overlap in metres, and **B** is bends. The checker uses a 1 mm geometry tolerance.

## What is implemented

| Phases | Component | Role |
| --- | --- | --- |
| 1–2 | Plan ingestion and room topology | Scale drawing coordinates to metres, identify rooms and device nodes, and find legal door connections. |
| 3 | Rectilinear micro-routing | Build a sparse horizontal and vertical grid and find obstacle-aware paths with bend costs. |
| 4 | Hub and zone selection | Generate legal hub sites and use OR-Tools CP-SAT to select connected room groups within capacity. |
| 5 | Ring synthesis | Choose node visit orders and produce closed route polylines. |
| 6 | Checker and export | Independently recalculate the six rules and score, write Slide 8 JSON, and draw a PNG overlay. |
| 7 | Interactive studio | Serve a browser canvas with drag feedback, live metrics, and JSON export. |
| 8 | Architectural copilot | Suggest doorway and wall changes through the studio. |
| 9 | Wire-cut simulation | Show device connectivity after a simulated cable cut. |
| 10 | 2D/3D view | Display the plan and an extruded 3D model together, with OBJ export. |

The batch solver accepts a plan JSON file. The browser studio currently starts from the Slide 5 fixture and updates its in-memory state. Its interactive controls do not provide a general plan-import and full reoptimization workflow. The project searches for low-cost valid solutions; it does **not** certify a global optimum for every floor plan.

## Requirements and installation

- Python **3.10 or newer**
- A browser with JavaScript and WebGL for the interactive 3D view

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install fastapi uvicorn
```

The core solver dependencies are declared in `pyproject.toml`. FastAPI and Uvicorn are required for `--serve` and currently installed separately. On Windows, activate the environment with `.venv\Scripts\activate`.

## Run the batch solver

The Slide 5 schematic is a developer-created fixture based on the deck's illustration:

```bash
mkdir -p output
python -m resplan_router.cli \
  -i tests/fixtures/slide5_schematic.json \
  -o output/slide5_solution.json \
  -v output/slide5_overlay.png
```

The command prints a reviewer scorecard with the six pass/fail results, hub count, cable length, bends, crossings, overlap, area gap, total cost, and runtime. The `-o` file contains hub assignments, closed node sequences, and route polylines; `-v` creates an image overlay. To exercise the general solver path on a separate example, replace the input with `tests/fixtures/new_four_room_plan.json`. The Slide 5 case has specialized route synthesis code, so use both fixtures when demonstrating generality.

## Run the browser studio

```bash
python -m resplan_router.cli --serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The studio offers a 2D canvas, live rule and score display, node and hub dragging, copilot suggestions, wire-cut simulation, a synchronized 3D view, Slide 8 JSON export, and OBJ export. Use `--port` to choose another port. It runs locally and loads the Slide 5 baseline on startup.

## Test the project

```bash
python -m pip install pytest httpx
python -m pytest tests/ -q
```

The phase tests are in `tests/`; example inputs are in `tests/fixtures/`. The latest project verification recorded 107 passing tests across Phases 1–10. That record does not replace rerunning the suite in your own environment.

## Repository map

- `src/resplan_router/cli.py`: command-line entry point.
- `src/resplan_router/phase1_ingestion/` through `phase5_ring_sequencing/`: geometry, room graph, pathfinding, zone selection, and ring construction.
- `src/resplan_router/phase6_output/`: checker, JSON exporter, visualizer, and end-to-end pipeline.
- `src/resplan_router/phase7_interactive/`: live engine, FastAPI server, browser assets, and later studio integrations.
- `src/resplan_router/phase8_copilot/`, `phase9_survivability/`, and `phase10_bim/`: interactive extension modules.
- `tests/`: unit and integration tests and fixtures.
- `docs/`: runbook and explanatory material.
- `.agents/`, `.agent/`, `.gemini/`, `.gsd/`, and `PROJECT_RULES.md`: GSD planning, workflow, and plugin support retained with the project.
- `v2.md`: build plan and requirements narrative.

## Data and reproducibility notes

The fixtures in this repository are examples made for implementation and tests. They are not official organizer-provided input JSON or reference solutions. The local ResPlan archive and extracted dataset are not committed because the ZIP exceeds GitHub's ordinary 100 MB file limit. Keep the archive locally and adapt its plans to the input contract before using them with the batch solver.

The challenge deck is the rule source. The software checker validates the rules it implements, but a passing scorecard is not a proof of globally optimal routing. For the algorithm, file-level explanation, and known Phase 7 limitations, see the [Word guide](docs/Phase_1_to_7_Explained.docx).
