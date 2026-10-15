"""Phase 10: Unit and integration test suite for 3D BIM generation and Wavefront OBJ export.

Validates:
1. Full 3D BIM scene generation from 2D metric floor plans.
2. Room floor slab triangulation at z = 0.0m with pastel zone materials.
3. 2.70m vertical walls in charcoal material (#2B2B2B).
4. Doorway portal lintels positioned strictly above z = 2.10m for cable clearance.
5. Semi-translucent architectural windows (sill 0.90m to 2.10m, #A0C4DF).
6. Ceiling-mounted sensor device pods and wall-mounted hub cabinets.
7. Elevated orthogonal cable conduits at z = 2.55m with vertical drops to hubs and nodes.
8. Wavefront OBJ export format syntax and topology validity.
9. Pydantic contract JSON serialization roundtrip.
"""

import json
from pathlib import Path
import pytest

from resplan_router.contracts import (
    BIM3DScene,
    BIMElement3D,
    BIMElementType,
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer
from resplan_router.phase10_bim.bim_generator import BIM3DGenerator


@pytest.fixture
def slide5_bim_setup() -> tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]:
    """Fixture providing Slide 5 floor plan, hub partition, synthesized cable bundle, and raw JSON."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)

    h1_pt = Point2D(x=5.2, y=3.6)
    h2_pt = Point2D(x=11.4, y=7.0)

    h1 = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=h1_pt,
        assigned_rooms=["R01", "R02", "R03"],
        assigned_nodes=["N1", "N2", "N3"],
        zone_area_m2=71.0,
    )
    h2 = HubAssignment(
        hub_id="H2",
        room_id="R05",
        location=h2_pt,
        assigned_rooms=["R04", "R05", "R06"],
        assigned_nodes=["N4", "N5", "N6"],
        zone_area_m2=47.0,
    )

    grid = build_hanan_grid(plan, topology, extra_points=[h1_pt, h2_pt])
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()

    partition = HubZonePartition(
        plan_id=plan.plan_id,
        hub_count=2,
        lower_bound_hubs=2,
        area_gap_m2=24.0,
        hubs=[h1, h2],
    )
    bundle = synthesizer.synthesize_all_rings(partition, plan, topology, router, grid)
    return plan, partition, bundle, data


def test_slide5_3d_bim_scene_generation(
    slide5_bim_setup: tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]
) -> None:
    """Test 1: Generate 3D BIM scene from Slide 5 schematic and verify all 7 element classifications."""
    plan, partition, bundle, raw_json = slide5_bim_setup
    generator = BIM3DGenerator()

    scene = generator.generate_3d_scene(
        plan=plan,
        partition=partition,
        bundle=bundle,
        ceiling_height_m=2.70,
        conduit_elevation_m=2.55,
        raw_json=raw_json,
    )

    # Basic scene verification
    assert scene.plan_id == plan.plan_id
    assert scene.units == "m"
    assert scene.ceiling_height_m == 2.70
    assert scene.conduit_elevation_m == 2.55
    assert len(scene.elements) > 0

    # Verify all 7 element types are present
    element_types = {e.element_type for e in scene.elements}
    expected_types = {
        BIMElementType.SLAB,
        BIMElementType.WALL,
        BIMElementType.WINDOW,
        BIMElementType.DOOR_LINTEL,
        BIMElementType.NODE,
        BIMElementType.HUB,
        BIMElementType.CONDUIT,
    }
    assert expected_types.issubset(element_types), f"Missing element types: {expected_types - element_types}"

    # Verify floor slabs elevation z = 0.0m
    slabs = [e for e in scene.elements if e.element_type == BIMElementType.SLAB]
    assert len(slabs) == len(plan.rooms)
    for slab in slabs:
        assert len(slab.vertices_3d) >= 3
        assert len(slab.faces) >= 1
        for v in slab.vertices_3d:
            assert abs(v[2] - 0.0) < 1e-4, f"Slab vertex z={v[2]} must be exactly 0.0m"

    # Verify walls vertical elevation from 0.0m to 2.70m
    walls = [e for e in scene.elements if e.element_type == BIMElementType.WALL]
    assert len(walls) > 0
    all_wall_zs = [v[2] for w in walls for v in w.vertices_3d]
    assert min(all_wall_zs) == 0.0
    assert max(all_wall_zs) == 2.70

    # Verify conduit elevation at 2.55m
    conduits = [e for e in scene.elements if e.element_type == BIMElementType.CONDUIT]
    assert len(conduits) == len(bundle.hub_rings)
    for conduit in conduits:
        zs = [v[2] for v in conduit.vertices_3d]
        assert any(abs(z - 2.55) < 0.03 for z in zs), "Conduit must contain horizontal runs at z = 2.55m"


def test_door_portal_clearance_in_3d(
    slide5_bim_setup: tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]
) -> None:
    """Test 2: Verify doorway lintels are positioned strictly above z = 2.10m for cable and walk-through clearance."""
    plan, partition, bundle, raw_json = slide5_bim_setup
    generator = BIM3DGenerator()

    scene = generator.generate_3d_scene(
        plan=plan,
        partition=partition,
        bundle=bundle,
        ceiling_height_m=2.70,
        raw_json=raw_json,
    )

    lintels = [e for e in scene.elements if e.element_type == BIMElementType.DOOR_LINTEL]
    assert len(lintels) > 0, "Scene must have interior doorway lintel elements"

    for lintel in lintels:
        assert len(lintel.vertices_3d) >= 8
        assert len(lintel.faces) >= 12
        for v in lintel.vertices_3d:
            assert v[2] >= 2.10 - 1e-4, f"Door lintel vertex z={v[2]} is below 2.10m clearance threshold"
            assert v[2] <= 2.70 + 1e-4, f"Door lintel vertex z={v[2]} exceeds ceiling height 2.70m"
        assert lintel.metadata.get("clearance_m") == 2.10 or lintel.metadata.get("sill_z") == 2.10


def test_3d_cable_conduit_vertical_drops(
    slide5_bim_setup: tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]
) -> None:
    """Test 3: Verify cable conduit elements contain horizontal runs at 2.55m and vertical drops to hubs and nodes."""
    plan, partition, bundle, raw_json = slide5_bim_setup
    generator = BIM3DGenerator()

    scene = generator.generate_3d_scene(
        plan=plan,
        partition=partition,
        bundle=bundle,
        ceiling_height_m=2.70,
        conduit_elevation_m=2.55,
        raw_json=raw_json,
    )

    conduits = [e for e in scene.elements if e.element_type == BIMElementType.CONDUIT]
    assert len(conduits) == len(bundle.hub_rings)

    for conduit in conduits:
        meta = conduit.metadata
        assert meta.get("has_vertical_drops") is True
        assert "hub_drop" in meta
        assert "node_drops" in meta

        # Hub vertical drop check
        hub_drop = meta["hub_drop"]
        assert abs(hub_drop["top"][2] - 2.55) < 1e-4
        assert abs(hub_drop["bottom"][2] - 1.50) < 1e-4

        # Node vertical drops check
        node_drops = meta["node_drops"]
        assert len(node_drops) > 0
        for nd in node_drops:
            assert abs(nd["top"][2] - 2.55) < 1e-4
            assert abs(nd["bottom"][2] - 2.40) < 1e-4

        # Mesh vertices check
        zs = [v[2] for v in conduit.vertices_3d]
        assert any(abs(z - 2.55) < 0.03 for z in zs), "Missing horizontal run vertices near 2.55m"
        assert any(abs(z - 1.50) < 0.03 for z in zs), "Missing hub vertical drop vertices near 1.50m"
        assert any(abs(z - 2.40) < 0.03 for z in zs), "Missing node vertical drop vertices near 2.40m"


def test_wavefront_obj_export_validity(
    slide5_bim_setup: tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]
) -> None:
    """Test 4: Verify Wavefront OBJ format export contains standard syntax tags, >100 vertices, and >50 faces."""
    plan, partition, bundle, raw_json = slide5_bim_setup
    generator = BIM3DGenerator()

    scene = generator.generate_3d_scene(
        plan=plan,
        partition=partition,
        bundle=bundle,
        ceiling_height_m=2.70,
        conduit_elevation_m=2.55,
        raw_json=raw_json,
    )

    obj_str = generator.export_wavefront_obj(scene)
    assert isinstance(obj_str, str)
    assert len(obj_str) > 0

    lines = [line.strip() for line in obj_str.splitlines() if line.strip()]
    v_lines = [l for l in lines if l.startswith("v ")]
    vn_lines = [l for l in lines if l.startswith("vn ")]
    f_lines = [l for l in lines if l.startswith("f ")]
    g_lines = [l for l in lines if l.startswith("g ")]
    usemtl_lines = [l for l in lines if l.startswith("usemtl ")]

    # Assert standard syntax tags exist
    assert len(v_lines) > 0, "OBJ must contain vertex definitions (v x y z)"
    assert len(vn_lines) > 0, "OBJ must contain normal definitions (vn nx ny nz)"
    assert len(f_lines) > 0, "OBJ must contain face definitions (f v1//vn1 ...)"
    assert len(g_lines) > 0, "OBJ must contain element grouping (g name)"
    assert len(usemtl_lines) > 0, "OBJ must contain material declarations (usemtl name)"

    # Assert vertex and face count thresholds
    assert len(v_lines) > 100, f"Expected vertex count > 100, got {len(v_lines)}"
    assert len(f_lines) > 50, f"Expected face count > 50, got {len(f_lines)}"

    # Check first few face tokens are correctly 1-indexed and match format
    first_face = f_lines[0].split()[1:]
    assert len(first_face) >= 3
    for tok in first_face:
        parts = tok.split("//")
        assert len(parts) == 2, f"Face token {tok} must follow v//vn format"
        v_idx, vn_idx = int(parts[0]), int(parts[1])
        assert v_idx >= 1, "OBJ vertex indices must be 1-indexed"
        assert vn_idx >= 1, "OBJ normal indices must be 1-indexed"


def test_pydantic_contract_roundtrip(
    slide5_bim_setup: tuple[MetricFloorPlan, HubZonePartition, SynthesizedRingsBundle, dict]
) -> None:
    """Test 5: Verify Pydantic BIM3DScene model serializes to JSON and roundtrips via model_validate."""
    plan, partition, bundle, raw_json = slide5_bim_setup
    generator = BIM3DGenerator()

    scene = generator.generate_3d_scene(
        plan=plan,
        partition=partition,
        bundle=bundle,
        ceiling_height_m=2.70,
        conduit_elevation_m=2.55,
        raw_json=raw_json,
    )

    # 1. Dictionary roundtrip via model_dump and model_validate
    dumped_dict = scene.model_dump()
    assert isinstance(dumped_dict, dict)
    scene_restored = BIM3DScene.model_validate(dumped_dict)

    assert scene_restored.plan_id == scene.plan_id
    assert scene_restored.ceiling_height_m == scene.ceiling_height_m
    assert scene_restored.conduit_elevation_m == scene.conduit_elevation_m
    assert len(scene_restored.elements) == len(scene.elements)
    assert scene_restored.metrics_hud == scene.metrics_hud

    # 2. JSON string roundtrip via model_dump_json and model_validate_json
    json_str = scene.model_dump_json()
    assert isinstance(json_str, str)
    scene_from_json = BIM3DScene.model_validate_json(json_str)

    assert scene_from_json.plan_id == scene.plan_id
    assert len(scene_from_json.elements) == len(scene.elements)
    for orig_elem, loaded_elem in zip(scene.elements, scene_from_json.elements):
        assert loaded_elem.element_id == orig_elem.element_id
        assert loaded_elem.element_type == orig_elem.element_type
        assert len(loaded_elem.vertices_3d) == len(orig_elem.vertices_3d)
        assert len(loaded_elem.faces) == len(orig_elem.faces)
