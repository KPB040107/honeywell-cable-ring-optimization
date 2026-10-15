# Phase 1 Plan 1: Environment, Configuration, and Shared Contracts

<objective>
Establish the Python 3.12 development environment, project dependencies, configuration profiles, and shared Pydantic v2 data models that define all inter-phase data contracts for the pipeline.
</objective>

<context>
@.gsd/SPEC.md
@prd_v1_control_panel_cable_routing_foundation.md
@PROJECT_RULES.md
</context>

<tasks>
<task id="1">
<title>Create pyproject.toml and virtual environment</title>
<description>
Define pyproject.toml with Python >= 3.12 and pinned dependencies:
- pydantic >= 2.6.0
- shapely >= 2.0.2
- networkx >= 3.2.1
- ortools >= 9.8.3296
- numpy >= 1.26.0
- scipy >= 1.12.0
- matplotlib >= 3.8.0
- pyyaml >= 6.0.1
- pytest >= 8.0.0

Create the virtual environment using /opt/homebrew/bin/python3.12 at .venv and install the dependencies in editable mode. Verify all imports succeed.
</description>
<verification>
.venv/bin/python -c "import pydantic, shapely, networkx, ortools, numpy, scipy, matplotlib, yaml, pytest; print('All core dependencies imported successfully')"
</verification>
</task>

<task id="2">
<title>Implement configuration system and profiles</title>
<description>
Create `configs/strict_v1.yaml` (default: max_area=100.0, max_nodes=5, eligible_rooms=[living, bedroom, kitchen, storage], excluded_rooms=[bathroom, balcony, parking, pool, stair, garden, land]) and `configs/ia_extended.yaml` (max_area=100.0, max_nodes=20).
Create `src/resplan_router/config.py` using Pydantic BaseSettings / BaseModel to load and validate these YAML configurations.
</description>
<verification>
.venv/bin/python -c "from resplan_router.config import load_config; cfg = load_config('configs/strict_v1.yaml'); assert cfg.max_nodes_per_hub == 5 and cfg.max_area_per_hub == 100.0; print('Config loaded successfully')"
</verification>
</task>

<task id="3">
<title>Define immutable Pydantic v2 data contracts</title>
<description>
Create `src/resplan_router/contracts.py` with strongly-typed, immutable models:
- Point2D: (x, y) coordinates with rounding/snapping helpers
- RoomEntity: room_id, category, polygon_wkt, area_m2, is_eligible, is_transit
- DoorEntity: door_id, polygon_wkt, is_front_door
- DeviceNode: node_id, host_room_id, coord: Point2D
- FloorPlanModel: plan_id, scale_factor, inner_polygon_wkt, obstacle_union_wkt, rooms, doors, nodes
- MacroTopologyModel, MicroRoutingIndex, ZonePartitionResult, RingRoutingResult, ValidatedSolutionBundle
Ensure clean JSON serialization and deserialization roundtrips.
</description>
<verification>
.venv/bin/python -c "from resplan_router.contracts import FloorPlanModel, RoomEntity, DeviceNode, Point2D; r = RoomEntity(room_id='living_0', category='living', polygon_wkt='POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))', area_m2=16.0, is_eligible=True, is_transit=False); n = DeviceNode(node_id='N1', host_room_id='living_0', coord=Point2D(x=2.0, y=2.0)); f = FloorPlanModel(plan_id='test', scale_factor=1.0, inner_polygon_wkt='POLYGON ((0 0, 4 0, 4 4, 0 4, 0 0))', obstacle_union_wkt='POLYGON EMPTY', rooms=[r], doors=[], nodes=[n]); dump = f.model_dump_json(); loaded = FloorPlanModel.model_validate_json(dump); assert loaded.plan_id == 'test'; print('Contracts verified')"
</verification>
</task>
</tasks>

<success_criteria>
- Virtual environment `.venv` active with Python 3.12 and all dependencies.
- `strict_v1.yaml` and `ia_extended.yaml` functional.
- `contracts.py` defined and verified with roundtrip serialization.
</success_criteria>
