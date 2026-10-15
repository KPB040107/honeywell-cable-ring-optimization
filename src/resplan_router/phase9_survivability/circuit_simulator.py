"""NFPA 72 Class A wire-cut survivability simulation engine.

Simulates bidirectional electrical signal propagation in closed low-voltage loops,
models single and multiple wire cuts, calculates exact branch path lengths, and
empirically validates the 100% device survivability theorem.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple
from shapely.geometry import LineString, Point

from resplan_router.contracts import (
    ClassASimulationReport,
    CompletedHubRing,
    NodeSurvivabilityStatus,
    Point2D,
    PowerSource,
    WireCutLocation,
)


class ClassACircuitSimulator:
    """NFPA 72 Class A bidirectional circuit loop survivability simulation engine."""

    def __init__(self) -> None:
        pass

    def _compute_cut_point_on_leg(
        self,
        ring: CompletedHubRing,
        leg_index: int,
        cut_fraction: float,
    ) -> Tuple[Point2D, float]:
        """Compute physical 2D coordinates and metric distance along polyline for a leg cut."""
        if leg_index < len(ring.route_segments) and len(ring.route_segments[leg_index].polyline) >= 2:
            seg = ring.route_segments[leg_index]
            pts = [(p.x, p.y) for p in seg.polyline]
            ls = LineString(pts)
            tot_len = ls.length
            dist = cut_fraction * tot_len
            pt = ls.interpolate(dist)
            return Point2D(x=round(pt.x, 4), y=round(pt.y, 4)), round(dist, 4)

        # Fallback to hub location or origin
        return ring.location, 0.0

    def simulate_wire_cut(
        self,
        ring: CompletedHubRing,
        cut_leg_index: int,
        cut_fraction: float = 0.5,
        plan_id: str = "",
    ) -> ClassASimulationReport:
        """Sever a single cable leg at specified fraction along its polyline.

        Decomposes the closed cycle H_k -> N_1 -> ... -> N_m -> H_k into two independent branches:
        - Primary Outbound Branch: H_k -> N_1 -> ... -> N_c (upstream of cut)
        - Inbound Return Terminal Branch: H_k -> N_m -> ... -> N_{c+1} (downstream of cut, reverse)
        """
        num_legs = len(ring.closed_sequence) - 1
        if not (0 <= cut_leg_index < num_legs):
            raise ValueError(
                f"cut_leg_index {cut_leg_index} is out of bounds for ring with {num_legs} legs"
            )
        cut_fraction = max(0.0, min(1.0, float(cut_fraction)))
        cut_pt, dist_along = self._compute_cut_point_on_leg(ring, cut_leg_index, cut_fraction)
        from_node = ring.closed_sequence[cut_leg_index]
        to_node = ring.closed_sequence[cut_leg_index + 1]
        cut_loc = WireCutLocation(
            hub_id=ring.hub_id,
            leg_index=cut_leg_index,
            from_node=from_node,
            to_node=to_node,
            cut_point=cut_pt,
            distance_along_leg_m=round(dist_along, 4),
        )
        return self._simulate_ring_state(ring, [cut_loc], plan_id=plan_id)

    def simulate_arbitrary_cut_point(
        self,
        ring: CompletedHubRing,
        cut_x: float,
        cut_y: float,
        plan_id: str = "",
    ) -> ClassASimulationReport:
        """Find closest segment in ring.route_segments to (cut_x, cut_y), project onto polyline, and sever."""
        cut_target = Point(cut_x, cut_y)
        num_legs = len(ring.closed_sequence) - 1
        if num_legs <= 0:
            raise ValueError("Ring closed sequence contains no legs")

        best_leg_index = 0
        best_dist = float("inf")
        best_proj_dist = 0.0
        best_cut_pt = Point2D(x=cut_x, y=cut_y)

        for idx in range(num_legs):
            pts: list[tuple[float, float]] = []
            if idx < len(ring.route_segments) and len(ring.route_segments[idx].polyline) >= 2:
                pts = [(p.x, p.y) for p in ring.route_segments[idx].polyline]

            if len(pts) >= 2:
                ls = LineString(pts)
                dist = ls.distance(cut_target)
                if dist < best_dist:
                    best_dist = dist
                    best_leg_index = idx
                    best_proj_dist = ls.project(cut_target)
                    proj_pt = ls.interpolate(best_proj_dist)
                    best_cut_pt = Point2D(x=round(proj_pt.x, 4), y=round(proj_pt.y, 4))

        from_node = ring.closed_sequence[best_leg_index]
        to_node = ring.closed_sequence[best_leg_index + 1]
        cut_loc = WireCutLocation(
            hub_id=ring.hub_id,
            leg_index=best_leg_index,
            from_node=from_node,
            to_node=to_node,
            cut_point=best_cut_pt,
            distance_along_leg_m=round(best_proj_dist, 4),
        )
        return self._simulate_ring_state(ring, [cut_loc], plan_id=plan_id)

    def simulate_multi_cut(
        self,
        ring: CompletedHubRing,
        cut_leg_indices: list[int],
        plan_id: str = "",
    ) -> ClassASimulationReport:
        """Simulate simultaneous wire cuts across multiple legs, detecting isolated sub-paths."""
        num_legs = len(ring.closed_sequence) - 1
        cut_locs: list[WireCutLocation] = []

        seen_legs = set()
        for leg_idx in cut_leg_indices:
            if not (0 <= leg_idx < num_legs):
                raise ValueError(
                    f"cut_leg_index {leg_idx} out of bounds for ring with {num_legs} legs"
                )
            if leg_idx in seen_legs:
                continue
            seen_legs.add(leg_idx)
            cut_pt, dist_along = self._compute_cut_point_on_leg(ring, leg_idx, 0.5)
            cut_locs.append(
                WireCutLocation(
                    hub_id=ring.hub_id,
                    leg_index=leg_idx,
                    from_node=ring.closed_sequence[leg_idx],
                    to_node=ring.closed_sequence[leg_idx + 1],
                    cut_point=cut_pt,
                    distance_along_leg_m=round(dist_along, 4),
                )
            )
        return self._simulate_ring_state(ring, cut_locs, plan_id=plan_id)

    def verify_loop_resilience_exhaustively(
        self,
        ring: CompletedHubRing,
        plan_id: str = "",
    ) -> dict:
        """Iterate through every leg of the closed ring, run simulate_wire_cut, and verify 100% survivability."""
        num_legs = len(ring.closed_sequence) - 1
        reports: list[ClassASimulationReport] = []
        min_surv = 100.0
        max_surv = 0.0

        for idx in range(num_legs):
            report = self.simulate_wire_cut(ring, cut_leg_index=idx, cut_fraction=0.5, plan_id=plan_id)
            assert report.survivability_pct == 100.0, (
                f"Hub {ring.hub_id} leg {idx} ({report.cut_location.from_node} -> {report.cut_location.to_node}) "
                f"failed 100% survivability: got {report.survivability_pct}%"
            )
            assert len(report.offline_nodes) == 0, (
                f"Hub {ring.hub_id} leg {idx} left offline nodes: {report.offline_nodes}"
            )
            assert report.nfpa72_class_a_compliant is True, (
                f"Hub {ring.hub_id} leg {idx} failed NFPA 72 Class A compliance"
            )
            reports.append(report)
            min_surv = min(min_surv, report.survivability_pct)
            max_surv = max(max_surv, report.survivability_pct)

        return {
            "hub_id": ring.hub_id,
            "legs_tested": len(reports),
            "all_single_cuts_survived": True,
            "min_survivability_pct": min_surv,
            "max_survivability_pct": max_surv,
            "reports": reports,
        }

    def _simulate_ring_state(
        self,
        ring: CompletedHubRing,
        cut_locations: list[WireCutLocation],
        plan_id: str = "",
    ) -> ClassASimulationReport:
        """Internal graph path traversal computing branch attribution, path lengths, and node statuses."""
        seq = ring.closed_sequence
        num_legs = len(seq) - 1
        device_nodes = seq[1:-1]
        total_nodes = len(device_nodes)

        # 1. Determine leg lengths
        leg_lengths: list[float] = []
        for j in range(num_legs):
            if j < len(ring.route_segments):
                seg = ring.route_segments[j]
                if seg.length_m > 0:
                    leg_lengths.append(seg.length_m)
                elif len(seg.polyline) >= 2:
                    ls = LineString([(p.x, p.y) for p in seg.polyline])
                    leg_lengths.append(round(ls.length, 4))
                else:
                    leg_lengths.append(0.0)
            else:
                leg_lengths.append(0.0)

        cut_indices = set(loc.leg_index for loc in cut_locations)

        # 2. Trace Primary Outbound Branch (H_k -> N_1 -> ... -> N_c)
        primary_branch_nodes: list[str] = []
        node_statuses: dict[str, NodeSurvivabilityStatus] = {}

        accumulated_outbound_len = 0.0
        for j in range(num_legs):
            if j in cut_indices:
                break
            accumulated_outbound_len += leg_lengths[j]
            reached_terminal = seq[j + 1]
            if reached_terminal != ring.hub_id:
                primary_branch_nodes.append(reached_terminal)
                node_statuses[reached_terminal] = NodeSurvivabilityStatus(
                    node_id=reached_terminal,
                    is_online=True,
                    power_source=PowerSource.PRIMARY_OUTBOUND,
                    path_length_m=round(accumulated_outbound_len, 4),
                    hop_count=len(primary_branch_nodes),
                )

        # 3. Trace Inbound Return Terminal Branch (H_k -> N_m -> ... -> N_{c+1})
        return_branch_nodes: list[str] = []
        accumulated_return_len = 0.0

        for k in range(num_legs - 1, -1, -1):
            if k in cut_indices:
                break
            accumulated_return_len += leg_lengths[k]
            reached_terminal = seq[k]
            if reached_terminal != ring.hub_id:
                if reached_terminal in node_statuses:
                    # Loop intact and reached node already powered by primary outbound
                    break
                return_branch_nodes.append(reached_terminal)
                node_statuses[reached_terminal] = NodeSurvivabilityStatus(
                    node_id=reached_terminal,
                    is_online=True,
                    power_source=PowerSource.RETURN_TERMINAL,
                    path_length_m=round(accumulated_return_len, 4),
                    hop_count=len(return_branch_nodes),
                )

        # 4. Identify Offline / Severed Nodes
        offline_nodes: list[str] = []
        for node_id in device_nodes:
            if node_id not in node_statuses:
                offline_nodes.append(node_id)
                node_statuses[node_id] = NodeSurvivabilityStatus(
                    node_id=node_id,
                    is_online=False,
                    power_source=PowerSource.OFFLINE,
                    path_length_m=0.0,
                    hop_count=0,
                )

        # 5. Composite Metrics
        online_nodes = len(primary_branch_nodes) + len(return_branch_nodes)
        survivability_pct = (
            round((online_nodes / total_nodes) * 100.0, 2) if total_nodes > 0 else 100.0
        )
        is_severed = len(cut_locations) > 0
        nfpa72_class_a_compliant = (
            len(cut_locations) <= 1 and online_nodes == total_nodes and total_nodes > 0
        )
        max_latency_path_m = round(
            max(
                (status.path_length_m for status in node_statuses.values() if status.is_online),
                default=0.0,
            ),
            4,
        )

        return ClassASimulationReport(
            plan_id=plan_id,
            hub_id=ring.hub_id,
            is_severed=is_severed,
            cut_location=cut_locations[0] if cut_locations else None,
            cut_locations=cut_locations,
            total_nodes=total_nodes,
            online_nodes=online_nodes,
            survivability_pct=survivability_pct,
            primary_branch_nodes=primary_branch_nodes,
            return_branch_nodes=return_branch_nodes,
            offline_nodes=offline_nodes,
            node_statuses=node_statuses,
            nfpa72_class_a_compliant=nfpa72_class_a_compliant,
            max_latency_path_m=max_latency_path_m,
        )
