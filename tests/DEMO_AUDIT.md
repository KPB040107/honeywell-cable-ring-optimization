# Cable Ring Challenge demo audit

These are **synthetic test floor plans**, separate from the ResPlan dataset. Each JSON in `fixtures/` has fixed input nodes, explicit rooms, walls, doorways, and an inner boundary. The matching solution JSON and overlay PNG are in `demo_outputs/`.

## Run a demo

From the repository root:

```sh
PYTHONPATH=src python3 -m resplan_router.cli \
  -i tests/fixtures/demo_three_rooms_window.json \
  -o /tmp/demo_three_rooms_solution.json \
  -v /tmp/demo_three_rooms_overlay.png
```

Replace the fixture name with `demo_six_rooms_capacity` or `demo_disconnected_zones`. Run the independent rule audit with:

```sh
PYTHONPATH=src python3 -m pytest tests/test_ppt_demo_audit.py -q
```

## Results

| Floor plan | What it exercises | H | Proof of minimum H | L (m) | B | X | O (m) | J |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| `demo_three_rooms_window` | Three eligible rooms, excluded bathroom, wall and window avoidance | 1 | Three nodes and 60 m² eligible area fit one hub | 18.40 | 8 | 0 | 0.00 | 22.40 |
| `demo_six_rooms_capacity` | Six eligible rooms and 120 m², forcing two hubs | 2 | Both six nodes and 120 m² exceed one-hub limits | 26.08 | 8 | 0 | 0.00 | 30.08 |
| `demo_disconnected_zones` | Three eligible rooms but two door-connected components | 2 | A hub cannot span the solid wall between components | 11.05 | 10 | 2 | 0.52 | 26.57 |

All three generated submissions passed the six hard validity rules in the checker, including exact fixed-node endpoints and 1 mm geometry tolerance. `tests/test_ppt_demo_audit.py` also checks coverage, room ownership, capacity, door connectivity, hub clearance, route continuity, free space, and recomputed length and cost. Crossings and overlap are *penalties* under the PowerPoint scoring rule, not hard invalidity conditions. The official secondary score is `J = L + 5X + O + 0.5B`, after minimum hub count.

## Optimality finding

The minimum **hub count** is proven for these three plans by the node, area, and door-graph lower bounds above. The generated cable cost **is not globally optimal** on the disconnected demo. `demo_outputs/demo_disconnected_cheaper_valid.json` is an independently checked submission with the same two hubs and the same fixed nodes. It passes all six rules and has `L=11.65 m`, `B=8`, `X=0`, `O=0.325 m`, so `J=15.975`, which is lower than the generated `J=26.57`. This is a concrete counterexample to a global optimum claim. The solver's partition and route heuristics need further optimization and an exact or bounded comparison before such a claim can be made.

The existing `slide5_schematic` fixture is an illustrative reconstruction, not a supplied ResPlan plan. Its previously hardcoded route coordinates missed several actual fixed nodes. The benchmark shortcut was removed; the generic router now uses the input node coordinates. Passing these synthetic demos does not establish success on unseen plans from the supplied ResPlan dataset.
