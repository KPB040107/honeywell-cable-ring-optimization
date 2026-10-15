"""Master End-to-End Cable Ring Routing Pipeline (Phases 1 through 6).

Integrates floor plan parsing, portal extraction, topological dual graph, Hanan grid,
pairwise directional cost tensor, contiguous zone partitioning, closed-ring synthesis,
independent 1 mm evaluation checker, Slide 8 JSON export, and visual overlay rendering.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Dict, Optional, Tuple, Union

from resplan_router.config import SystemConfig
from resplan_router.contracts import (
    EvaluationReport,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase4_partitioning.hub_candidates import (
    generate_all_room_hub_candidates,
)
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner
from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer
from resplan_router.phase6_output.checker import FloorPlanChecker
from resplan_router.phase6_output.json_exporter import export_slide8_json
from resplan_router.phase6_output.visualizer import FloorPlanVisualizer


def route_floor_plan(
    raw_plan_dict_or_path: Union[dict[str, Any], str, Path],
    config: Optional[SystemConfig] = None,
    output_json_path: Optional[Union[str, Path]] = None,
    render_png_path: Optional[Union[str, Path]] = None,
) -> Tuple[dict[str, Any], EvaluationReport, SynthesizedRingsBundle]:
    """Execute complete end-to-end cable routing pipeline across all 6 phases.

    Args:
        raw_plan_dict_or_path: Input floor plan dictionary or path to JSON file.
        config: Optional SystemConfig containing tolerances, capacities, and weights.
        output_json_path: Optional file path to export Slide 8 JSON solution.
        render_png_path: Optional file path to render Slide 5 schematic overlay PNG.

    Returns:
        Tuple of (exported_slide8_json_dict, evaluation_report, synthesized_rings_bundle).
    """
    sys_config = config or SystemConfig()

    # 1. Ingestion & Scale Normalization (Phase 1)
    if isinstance(raw_plan_dict_or_path, (str, Path)):
        p = Path(raw_plan_dict_or_path)
        with open(p, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    elif isinstance(raw_plan_dict_or_path, dict):
        raw_data = raw_plan_dict_or_path
    else:
        raise TypeError(f"Expected dict, str, or Path, got {type(raw_plan_dict_or_path)}")

    plan: MetricFloorPlan = parse_floor_plan(raw_data)

    # 2. Portal Extraction & Macro Topology Dual Graph (Phase 2)
    doors_data = raw_data.get("doors")
    front_door_data = raw_data.get("front_door")
    portals = extract_all_portals(
        plan=plan,
        raw_doors=doors_data,
        raw_entrance=front_door_data,
    )
    topology = MacroTopologyGraph.build_topology(plan=plan, portals=portals)

    # 3. Hanan Grid & Pairwise Directional Cost Tensor (Phase 3)
    grid = build_hanan_grid(plan=plan, topology=topology)
    cost_tensor = PairwiseCostTensor.build_from_plan(
        plan=plan,
        topology=topology,
        grid=grid,
        include_portals=False,
    )

    # 4. Hub Candidate Generation & Contiguous Zone Partitioning (Phase 4)
    candidates = generate_all_room_hub_candidates(
        plan=plan,
        margin_m=sys_config.wall_clearance_m + 0.005,
        topology=topology,
    )
    partition = ZonePartitioner.solve_partition(
        plan=plan,
        topology=topology,
        cost_tensor=cost_tensor,
        candidates=candidates,
        max_area=sys_config.max_area_m2,
        max_nodes=sys_config.max_nodes_per_hub,
    )

    # 5. Held-Karp Ring Sequencing & Zero-Overlap Loop Synthesis (Phase 5)
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()
    bundle = synthesizer.synthesize_all_rings(
        partition=partition,
        plan=plan,
        topology=topology,
        router=router,
        grid=grid,
        cost_tensor=cost_tensor,
    )

    # 6. Evaluation Checker, Slide 8 JSON Export & Visualizer (Phase 6)
    evaluation = FloorPlanChecker.evaluate(
        plan=plan,
        raw_submission=bundle,
        topology=topology,
        tolerance_m=sys_config.checker_tolerance_m,
        max_area_m2=sys_config.max_area_m2,
        max_nodes_per_hub=sys_config.max_nodes_per_hub,
    )

    slide8_dict = export_slide8_json(
        plan=plan,
        bundle=bundle,
        partition=partition,
        output_path=output_json_path,
    )

    if render_png_path is not None:
        FloorPlanVisualizer.render_overlay(
            plan=plan,
            bundle=bundle,
            evaluation=evaluation,
            output_path=render_png_path,
        )

    return slide8_dict, evaluation, bundle
