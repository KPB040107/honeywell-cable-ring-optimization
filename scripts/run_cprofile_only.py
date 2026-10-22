"""Run cProfile only on slide5_schematic and dataset_plan_14433 to extract exact bottleneck functions."""

import cProfile
import io
import json
from pathlib import Path
import pstats
import time

from resplan_router.contracts import MetricFloorPlan
from resplan_router.phase1_ingestion.dataset_repository import DatasetRepository
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase1_ingestion.resplan_adapter import adapt_resplan_record
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase3_micro_routing.astar_rectilinear import DirectionStateRouter
from resplan_router.phase3_micro_routing.cost_matrix import PairwiseCostTensor
from resplan_router.phase3_micro_routing.rectilinear_grid import build_hanan_grid
from resplan_router.phase4_partitioning.hub_candidates import generate_all_room_hub_candidates
from resplan_router.phase4_partitioning.zone_milp import ZonePartitioner
from resplan_router.phase5_ring_sequencing.loop_synthesizer import LoopSynthesizer
from resplan_router.phase6_output.checker import FloorPlanChecker


def profile_plan(plan: MetricFloorPlan, raw_doors=None, raw_front=None) -> str:
    pr = cProfile.Profile()
    pr.enable()

    portals = extract_all_portals(plan, raw_doors=raw_doors, raw_entrance=raw_front)
    topology = MacroTopologyGraph.build_topology(plan, portals)
    grid = build_hanan_grid(plan, topology)
    cost_tensor = PairwiseCostTensor.build_from_plan(plan, topology, grid, include_portals=False)
    candidates = generate_all_room_hub_candidates(plan, margin_m=0.505, topology=topology)
    partition = ZonePartitioner.solve_partition(plan, topology, cost_tensor, candidates)
    selected_hubs = {h.hub_id: h.location for h in partition.hubs}
    ring_grid = build_hanan_grid(plan, topology, extra_points=list(selected_hubs.values()))
    ring_cost_tensor = PairwiseCostTensor.build_from_plan(
        plan, topology, ring_grid, candidate_hubs=selected_hubs, include_portals=False
    )
    router = DirectionStateRouter()
    synthesizer = LoopSynthesizer()
    bundle = synthesizer.synthesize_all_rings(partition, plan, topology, router, ring_grid, ring_cost_tensor)
    report = FloorPlanChecker.evaluate(plan, bundle, topology=topology)

    pr.disable()
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats(pstats.SortKey.CUMULATIVE)
    ps.print_stats(30)
    return s.getvalue()


def main():
    print("Running cProfile on slide5_schematic.json...")
    slide5_raw = json.loads(Path("tests/fixtures/slide5_schematic.json").read_text(encoding="utf-8"))
    plan_slide5 = parse_floor_plan(slide5_raw, strict_clearance=False)
    prof_slide5 = profile_plan(plan_slide5, slide5_raw.get("doors"), slide5_raw.get("front_door"))

    print("Running cProfile on dataset_plan_14433...")
    repo = DatasetRepository.get_instance()
    rec = repo.get_record(14433)
    plan_14433 = adapt_resplan_record(rec)
    prof_14433 = profile_plan(plan_14433)

    out = {
        "slide5_schematic": prof_slide5,
        "dataset_plan_14433": prof_14433,
    }
    Path("reports/cprofile_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("Done! Results written to reports/cprofile_results.json")


if __name__ == "__main__":
    main()
