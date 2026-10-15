"""Unit test suite for Phase 4 Rule 6 hub candidate generation and data contracts."""

import json
from pathlib import Path
import pytest
import shapely
from shapely.geometry import Point

from resplan_router.contracts import (
    HubAssignment,
    HubZonePartition,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
)
from resplan_router.phase1_ingestion.parser import parse_floor_plan
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
from resplan_router.phase2_topology.portal_extractor import extract_all_portals
from resplan_router.phase4_partitioning.hub_candidates import (
    generate_all_room_hub_candidates,
    generate_hub_candidates,
)


@pytest.fixture
def slide5_plan_and_topology() -> tuple[MetricFloorPlan, PortalTopologyGraph]:
    """Fixture providing parsed Slide 5 plan and constructed topology graph."""
    fixture_path = Path(__file__).parent / "fixtures" / "slide5_schematic.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan = parse_floor_plan(data)
    portals = extract_all_portals(plan, data.get("doors"), data.get("front_door"))
    topology = MacroTopologyGraph.build_topology(plan, portals)
    return plan, topology


def test_contracts_capacity_invariants() -> None:
    """Verify that HubAssignment and HubZonePartition enforce Slide 3 invariants."""
    valid_loc = Point2D(x=2.0, y=2.0)

    # Valid HubAssignment
    hub = HubAssignment(
        hub_id="H1",
        room_id="R01",
        location=valid_loc,
        assigned_rooms=["R01", "R02"],
        assigned_nodes=["N1", "N2", "N3"],
        zone_area_m2=56.0,
    )
    assert hub.hub_id == "H1"
    assert hub.zone_area_m2 == 56.0

    # Invariant: zone_area_m2 <= 100.001
    with pytest.raises(ValueError, match="exceeds maximum capacity limit"):
        HubAssignment(
            hub_id="H1",
            room_id="R01",
            location=valid_loc,
            assigned_rooms=["R01"],
            assigned_nodes=["N1"],
            zone_area_m2=100.5,
        )

    # Invariant: len(assigned_nodes) <= 5
    with pytest.raises(ValueError, match="exceeds maximum capacity of 5 nodes"):
        HubAssignment(
            hub_id="H1",
            room_id="R01",
            location=valid_loc,
            assigned_rooms=["R01"],
            assigned_nodes=["N1", "N2", "N3", "N4", "N5", "N6"],
            zone_area_m2=50.0,
        )

    # Invariant: host room must be in assigned_rooms
    with pytest.raises(ValueError, match="must be in assigned_rooms"):
        HubAssignment(
            hub_id="H1",
            room_id="R03",
            location=valid_loc,
            assigned_rooms=["R01", "R02"],
            assigned_nodes=["N1"],
            zone_area_m2=50.0,
        )

    # Valid HubZonePartition
    partition = HubZonePartition(
        plan_id="slide5",
        hub_count=1,
        lower_bound_hubs=1,
        area_gap_m2=0.0,
        hubs=[hub],
    )
    assert partition.hub_count == 1

    # Invariant: hub_count >= lower_bound_hubs
    with pytest.raises(ValueError, match="cannot be less than lower bound"):
        HubZonePartition(
            plan_id="slide5",
            hub_count=1,
            lower_bound_hubs=2,
            hubs=[hub],
        )

    # Invariant: len(hubs) == hub_count
    with pytest.raises(ValueError, match="does not match number of hubs"):
        HubZonePartition(
            plan_id="slide5",
            hub_count=2,
            lower_bound_hubs=1,
            hubs=[hub],
        )


def test_slide5_candidates_count_and_eligibility(
    slide5_plan_and_topology: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Verify candidate generation counts and exclusion of non-eligible rooms on Slide 5."""
    plan, topology = slide5_plan_and_topology

    all_cands = generate_all_room_hub_candidates(plan, topology=topology)

    # Eligible rooms must produce candidates
    eligible_ids = ["R01", "R02", "R03", "R04", "R05", "R06"]
    for rid in eligible_ids:
        assert rid in all_cands
        assert len(all_cands[rid]) > 0, f"Eligible room {rid} must produce >= 1 candidate"

    # Excluded room (R07 bathroom) must produce 0 candidates
    assert "R07" in all_cands
    assert len(all_cands["R07"]) == 0, "Excluded room R07 (bathroom) must produce 0 hub candidates"

    # Direct function call on excluded room returns empty list
    direct_excluded = generate_hub_candidates(plan, "R07", topology=topology)
    assert direct_excluded == []

    # Non-existent room raises ValueError
    with pytest.raises(ValueError, match="not found in floor plan"):
        generate_hub_candidates(plan, "R999")


def test_slide5_candidates_strictly_inside_host_room(
    slide5_plan_and_topology: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Verify that 100% of candidate points lie strictly within their host room."""
    plan, topology = slide5_plan_and_topology

    all_cands = generate_all_room_hub_candidates(plan, topology=topology)
    total_checked = 0

    for rid, cands in all_cands.items():
        if not cands:
            continue
        room_poly = plan.rooms[rid].geometry
        for cand in cands:
            pt = cand.as_shapely()
            assert room_poly.contains(pt), (
                f"Candidate {cand.coords} is not strictly inside room {rid}"
            )
            total_checked += 1

    assert total_checked > 0, "Must have checked candidate points"


def test_slide5_candidates_rule6_wall_clearance(
    slide5_plan_and_topology: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Verify that for every candidate h, distance(h, obstacles) >= 0.50m - 0.001m (Rule 6)."""
    plan, topology = slide5_plan_and_topology

    all_cands = generate_all_room_hub_candidates(plan, topology=topology)
    obs = plan.obstacles_geom
    min_allowed_dist = 0.50 - 0.001  # 0.499m

    min_observed_dist = float("inf")
    total_checked = 0

    for rid, cands in all_cands.items():
        for cand in cands:
            pt = cand.as_shapely()
            dist = obs.distance(pt)
            if dist < min_observed_dist:
                min_observed_dist = dist
            assert dist >= min_allowed_dist, (
                f"Candidate {cand.coords} in {rid} violates Rule 6: dist={dist:.5f}m < {min_allowed_dist:.3f}m"
            )
            total_checked += 1

    assert total_checked > 0
    assert min_observed_dist >= min_allowed_dist
    print(f"\nRule 6 Wall Clearance Verified: {total_checked} candidates checked, min_dist={min_observed_dist:.4f}m")


def test_slide5_candidates_fixed_node_separation(
    slide5_plan_and_topology: tuple[MetricFloorPlan, PortalTopologyGraph],
) -> None:
    """Verify that distance from every candidate h to all fixed nodes >= 0.30m."""
    plan, topology = slide5_plan_and_topology

    all_cands = generate_all_room_hub_candidates(plan, topology=topology)
    nodes = list(plan.nodes.values())
    min_allowed_node_dist = 0.30 - 1e-4

    min_observed_node_dist = float("inf")
    total_checked = 0

    for rid, cands in all_cands.items():
        for cand in cands:
            cand_pt = cand.as_shapely()
            for node in nodes:
                node_pt = node.location.as_shapely()
                dist = cand_pt.distance(node_pt)
                if dist < min_observed_node_dist:
                    min_observed_node_dist = dist
                assert dist >= min_allowed_node_dist, (
                    f"Candidate {cand.coords} in {rid} is too close to node {node.node_id} ({node.location.coords}): dist={dist:.4f}m"
                )
            total_checked += 1

    assert total_checked > 0
    assert min_observed_node_dist >= min_allowed_node_dist
    print(f"\nFixed Node Separation Verified: {total_checked} candidates checked, min_node_dist={min_observed_node_dist:.4f}m")
