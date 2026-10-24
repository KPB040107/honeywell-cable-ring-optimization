# CLI Reference Manual

The **ResPlan Cable Ring Router** provides a command-line interface (`resplan_router.cli`) for single-plan execution, batch orchestration, constraint overrides, export serialization, visual rendering, and interactive web server launching.

---

## 1. Synopsis

```bash
python -m resplan_router.cli [-h] [-i INPUT] [-o OUTPUT] [-v VISUALIZE]
                             [-c CONFIG] [--max-nodes MAX_NODES] [--max-area MAX_AREA]
                             [--serve] [--port PORT] [--host HOST]
```

Or using the installed console entrypoint:

```bash
resplan-router -i <path_to_floor_plan.json> [OPTIONS]
```

---

## 2. Options and Flags

| Flag | Short | Type | Default | Description |
| :--- | :---: | :---: | :---: | :--- |
| `--input` | `-i` | `Path` | *None* | Path to input floor plan JSON file. *(Required unless `--serve` is specified)* |
| `--output` | `-o` | `Path` | *None* | Path to export canonical Slide 8 solution JSON file. |
| `--visualize` | `-v` | `Path` | *None* | Path to render Slide 5 schematic overlay PNG diagram. |
| `--config` | `-c` | `Path` | *None* | Path to optional YAML configuration file. |
| `--max-nodes` | | `int` | `5` | Override maximum fixed nodes per hub capacity limit. |
| `--max-area` | | `float` | `100.0` | Override maximum zone area ($m^2$) capacity limit. |
| `--serve` | | `flag` | `False` | Launch the interactive Co-Design Studio web server with live HUD. |
| `--port` | | `int` | `8000` | Port for the interactive Co-Design Studio web server. |
| `--host` | | `str` | `127.0.0.1` | Host interface to bind the Co-Design Studio server. |
| `--help` | `-h` | `flag` | | Display help message and exit. |

---

## 3. Exit Codes

The CLI follows standard POSIX process exit conventions:

| Exit Code | Meaning | Cause |
| :---: | :--- | :--- |
| `0` | **Success** | Pipeline completed; 100% hard validity rules passed ($H$ and $J$ optimized). |
| `1` | **Validation Failure or Error** | One or more hard constraints violated, file not found, or fatal error encountered. |
| `2` | **Invalid Command-Line Syntax** | Missing required arguments (e.g. omitted `-i`), unrecognized flags, or malformed numbers. |

---

## 4. Terminal Reviewer Scorecard

When executed in routing mode, the CLI prints an architectural reviewer scorecard to `stdout`:

```text
================================================================================
                 RESPLAN CABLE RING ROUTER - REVIEWER SCORECARD                 
================================================================================
Plan ID:         slide5_schematic
Status:          VALID (100% Rules Passed)
Execution Time:  2.225 s
Slide 8 JSON:    output/solution.json
Schematic PNG:   output/overlay.png
--------------------------------------------------------------------------------
COMPETITION METRICS:
  Hub Count (H):           2 hubs
  Total Cable Length (L):  42.48 m
  Total Bends (B):         18
  Total Crossings (X):     1
  Total Overlap (O):       0.00 m
  Area Gap (ΔA_gap):       24.00 m²
  Total Cost J (Official): 56.48
--------------------------------------------------------------------------------
HARD VALIDITY RULES (Slide 3 Checklist):
  [PASS] Rule 1: Full Coverage (all eligible rooms & nodes served)
  [PASS] Rule 2: Capacity Limits (<= 100 m² & <= 5 nodes per hub)
  [PASS] Rule 3: Doorway Contiguity (rooms door-connected)
  [PASS] Rule 4: Closed Ring (hub -> all nodes -> hub)
  [PASS] Rule 5: Free Space (axis-aligned, no wall/window crossing)
  [PASS] Rule 6: Hub Clearance (>= 0.50 m from walls/windows)
================================================================================
```

If any rule is violated, the status indicates `INVALID (Violations Detected)`, and detailed violation locations with coordinates are appended:

```text
--------------------------------------------------------------------------------
RULE VIOLATIONS DETAIL:
  - [Rule 6] Hub Clearance Violation at (15.20, 24.10): Hub center within 0.38 m of Wall W03 (required >= 0.50 m)
================================================================================
```

---

## 5. Command-Line Examples

### Example 1: Basic Evaluation

Evaluate a single floor plan and print the scorecard:

```bash
python -m resplan_router.cli -i examples/sample_floor_plan.json
```

### Example 2: Full Export (JSON Solution & Schematic PNG)

Run routing, save the canonical Slide 8 JSON solution, and generate a 300 DPI overlay image:

```bash
python -m resplan_router.cli \
  -i examples/sample_floor_plan.json \
  -o output/my_solution.json \
  -v output/my_overlay.png
```

### Example 3: Capacity Constraint Overrides

Test the effect of tighter hub capacities (e.g. maximum 4 devices per hub and 75 $m^2$ per zone):

```bash
python -m resplan_router.cli \
  -i examples/sample_floor_plan.json \
  --max-nodes 4 \
  --max-area 75.0 \
  -o output/tight_capacity_solution.json
```

### Example 4: Custom YAML Configuration

Apply an external configuration file:

```bash
python -m resplan_router.cli \
  -i tests/fixtures/slide5_schematic.json \
  -c configs/production.yaml \
  -o output/configured_solution.json
```

### Example 5: Launch Interactive Co-Design Studio

Start the local web server to interactively visualize and manipulate cable rings in a browser:

```bash
python -m resplan_router.cli --serve --port 8080 --host 0.0.0.0
```

Access the UI at `http://127.0.0.1:8080`.

---

## 6. Batch Orchestration via Shell Scripts

For processing multiple floor plans in batch via CLI, use standard shell looping:

### Linux / macOS (Bash)
```bash
mkdir -p output/batch
for plan in examples/*.json; do
    echo "Processing $plan..."
    python -m resplan_router.cli \
        -i "$plan" \
        -o "output/batch/$(basename "$plan" .json)_sol.json"
done
```

### Windows (PowerShell)
```powershell
New-Item -ItemType Directory -Force -Path output\batch
Get-ChildItem examples\*.json | ForEach-Object {
    Write-Host "Processing $($_.Name)..."
    python -m resplan_router.cli `
        -i $_.FullName `
        -o "output\batch\$($_.BaseName)_sol.json"
}
```

> **Tip:** For multi-threaded batch execution with real-time aggregated metrics, prefer using `python examples/quickstart_batch.py` or `python -m benchmarks.benchmark_suite`.
