"""Official 1 mm Independent Evaluation Checker (Slides 3, 6, and 7).

Independently verifies all 6 Hard Validity Rules and strictly recomputes the exact
competition metrics (L, X, O, B, J, Delta A_gap) at 1 mm geometric tolerance (0.001 m)
directly from raw polyline geometry, completely ignoring self-reported metrics.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import networkx as nx
import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon

from resplan_router.contracts import (
    EvaluatedHubMetrics,
    EvaluationReport,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    RuleViolation,
    SynthesizedRingsBundle,
)


class _NormalizedSegment:
    """Internal normalized representation of a route segment."""

    def __init__(
        self,
        from_node: str,
        to_node: str,
        polyline: list[Point2D],
    ) -> None:
        self.from_node = from_node
        self.to_node = to_node
        self.polyline = polyline

    @property
    def length_m(self) -> float:
        total = 0.0
        for i in range(len(self.polyline) - 1):
            p1 = self.polyline[i]
            p2 = self.polyline[i + 1]
            total += math.hypot(p2.x - p1.x, p2.y - p1.y)
        return float(round(total, 4))


class _NormalizedHub:
    """Internal normalized representation of a hub ring submission."""

    def __init__(
        self,
        hub_id: str,
        room_id: str,
        location: Point2D,
        assigned_rooms: list[str],
        assigned_nodes: list[str],
        closed_sequence: list[str],
        route_segments: list[_NormalizedSegment],
    ) -> None:
        self.hub_id = hub_id
        self.room_id = room_id
        self.location = location
        self.assigned_rooms = assigned_rooms
        self.assigned_nodes = assigned_nodes
        self.closed_sequence = closed_sequence
        self.route_segments = route_segments


def _clean_and_compress_polyline(points: Sequence[Point2D], tol: float = 0.001) -> list[Point2D]:
    """Remove consecutive duplicate points and merge collinear segments."""
    if len(points) <= 1:
        return list(points)

    # 1. Remove consecutive identical points within 0.1 mm
    cleaned: list[Point2D] = [points[0]]
    for i in range(1, len(points)):
        p = points[i]
        last = cleaned[-1]
        if math.hypot(p.x - last.x, p.y - last.y) > 1e-4:
            cleaned.append(p)

    if len(cleaned) <= 2:
        return cleaned

    # 2. Merge collinear consecutive segments
    compressed: list[Point2D] = [cleaned[0]]
    for i in range(1, len(cleaned) - 1):
        prev_pt = compressed[-1]
        curr_pt = cleaned[i]
        next_pt = cleaned[i + 1]

        dx1 = curr_pt.x - prev_pt.x
        dy1 = curr_pt.y - prev_pt.y
        dx2 = next_pt.x - curr_pt.x
        dy2 = next_pt.y - curr_pt.y

        # Collinear if cross product is negligible and directions match
        cross = abs(dx1 * dy2 - dy1 * dx2)
        dot = dx1 * dx2 + dy1 * dy2

        if cross < 1e-6 and dot > 0:
            # Same line & direction: skip curr_pt
            continue
        compressed.append(curr_pt)

    compressed.append(cleaned[-1])
    return compressed


class FloorPlanChecker:
    """Independent Evaluation Checker strictly enforcing Slides 3, 6 & 7 at 1 mm tolerance."""

    @classmethod
    def evaluate(
        cls,
        plan: MetricFloorPlan,
        raw_submission: Union[dict, SynthesizedRingsBundle],
        topology: Optional[MacroTopologyGraph] = None,
        tolerance_m: float = 0.001,
        max_area_m2: float = 100.0,
        max_nodes_per_hub: int = 5,
    ) -> EvaluationReport:
        """Evaluate a floor plan submission against all 6 Hard Rules and competition metrics.

        Args:
            plan: Official MetricFloorPlan containing verified geometry.
            raw_submission: Slide 8 submission dict or SynthesizedRingsBundle.
            topology: Optional MacroTopologyGraph for doorway connectivity check.
            tolerance_m: Spatial tolerance in metres (default 0.001 m = 1 mm).
            max_area_m2: Hub area capacity limit (default 100.0 m^2).
            max_nodes_per_hub: Hub node count capacity limit (default 5).

        Returns:
            EvaluationReport containing validation status, violations, and independently
            recomputed metrics (L, X, O, B, J, Delta A_gap).
        """
        # 1. Normalize submission data
        plan_id, hubs = cls._normalize_submission(plan, raw_submission)

        # 2. Extract or build MacroTopologyGraph if needed
        if topology is None:
            from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph
            from resplan_router.phase2_topology.portal_extractor import extract_all_portals

            portals = extract_all_portals(plan)
            topology = MacroTopologyGraph.build_topology(plan, portals)

        violations: list[RuleViolation] = []

        # 3. Check Hard Rules 1 to 6
        cls._check_rule_1_coverage(plan, hubs, violations)
        cls._check_rule_2_capacity(plan, hubs, violations, max_area_m2, max_nodes_per_hub)
        cls._check_rule_3_contiguity(plan, hubs, topology, violations)
        cls._check_rule_4_closed_ring(plan, hubs, violations, tolerance_m)
        cls._check_rule_5_free_space_and_axis_alignment(plan, hubs, violations, tolerance_m)
        cls._check_rule_6_hub_clearance(plan, hubs, violations, tolerance_m)

        # 4. Compute Independent Metrics (ignoring any reported metrics)
        hub_metrics_list, total_L, total_X, total_O, total_B, total_J, area_gap = (
            cls._recompute_metrics(plan, hubs, tolerance_m)
        )

        rules_passed = {
            "Rule 1": not any(v.rule_number == 1 for v in violations),
            "Rule 2": not any(v.rule_number == 2 for v in violations),
            "Rule 3": not any(v.rule_number == 3 for v in violations),
            "Rule 4": not any(v.rule_number == 4 for v in violations),
            "Rule 5": not any(v.rule_number == 5 for v in violations),
            "Rule 6": not any(v.rule_number == 6 for v in violations),
        }

        is_valid = len(violations) == 0

        return EvaluationReport(
            plan_id=plan_id,
            is_valid=is_valid,
            violations=violations,
            hub_count=len(hubs),
            hub_metrics=hub_metrics_list,
            total_length_m=total_L,
            total_crossings=total_X,
            total_overlap_m=total_O,
            total_bends=total_B,
            total_cost_J=total_J,
            area_gap_m2=area_gap,
            rules_passed=rules_passed,
        )

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    @classmethod
    def _normalize_submission(
        cls,
        plan: MetricFloorPlan,
        raw: Union[dict, SynthesizedRingsBundle],
    ) -> Tuple[str, list[_NormalizedHub]]:
        """Convert arbitrary submission formats into uniform _NormalizedHub objects."""
        if isinstance(raw, SynthesizedRingsBundle):
            plan_id = raw.plan_id
            hubs: list[_NormalizedHub] = []
            for ring in raw.hub_rings:
                segs = [
                    _NormalizedSegment(
                        from_node=r.from_node,
                        to_node=r.to_node,
                        polyline=list(r.polyline),
                    )
                    for r in ring.route_segments
                ]
                hubs.append(
                    _NormalizedHub(
                        hub_id=ring.hub_id,
                        room_id=ring.room_id,
                        location=ring.location,
                        assigned_rooms=list(ring.assigned_rooms),
                        assigned_nodes=list(ring.assigned_nodes),
                        closed_sequence=list(ring.closed_sequence),
                        route_segments=segs,
                    )
                )
            return plan_id, hubs

        if isinstance(raw, dict):
            plan_id = str(raw.get("plan_id", plan.plan_id))
            raw_hubs = raw.get("hubs") or raw.get("hub_rings") or []
            hubs = []
            for h in raw_hubs:
                hub_id = str(h.get("hub_id", ""))
                room_id = str(h.get("room_id", ""))
                loc_raw = h.get("location")
                if isinstance(loc_raw, Point2D):
                    loc = loc_raw
                elif isinstance(loc_raw, dict):
                    loc = Point2D(x=float(loc_raw["x"]), y=float(loc_raw["y"]))
                elif isinstance(loc_raw, (list, tuple)) and len(loc_raw) >= 2:
                    loc = Point2D(x=float(loc_raw[0]), y=float(loc_raw[1]))
                else:
                    loc = Point2D(x=0.0, y=0.0)

                assigned_rooms = [str(r) for r in h.get("assigned_rooms", [])]
                assigned_nodes = [str(n) for n in h.get("assigned_nodes", [])]
                closed_sequence = [str(s) for s in h.get("closed_sequence", [])]

                segs: list[_NormalizedSegment] = []
                for s in h.get("route_segments", []):
                    f_node = str(s.get("from") or s.get("from_node", ""))
                    t_node = str(s.get("to") or s.get("to_node", ""))
                    poly_pts: list[Point2D] = []
                    for pt in s.get("polyline", []):
                        if isinstance(pt, Point2D):
                            poly_pts.append(pt)
                        elif isinstance(pt, dict):
                            poly_pts.append(Point2D(x=float(pt["x"]), y=float(pt["y"])))
                        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                            poly_pts.append(Point2D(x=float(pt[0]), y=float(pt[1])))
                    segs.append(_NormalizedSegment(from_node=f_node, to_node=t_node, polyline=poly_pts))

                hubs.append(
                    _NormalizedHub(
                        hub_id=hub_id,
                        room_id=room_id,
                        location=loc,
                        assigned_rooms=assigned_rooms,
                        assigned_nodes=assigned_nodes,
                        closed_sequence=closed_sequence,
                        route_segments=segs,
                    )
                )
            return plan_id, hubs

        raise TypeError(f"Unsupported raw_submission type: {type(raw)}")

    # -------------------------------------------------------------------------
    # Hard Rule 1: Full Coverage
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_1_coverage(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        violations: list[RuleViolation],
    ) -> None:
        """Every eligible room & fixed node assigned to exactly one hub; no excluded spaces."""
        eligible_rooms = {r_id for r_id, r in plan.rooms.items() if r.is_eligible}
        excluded_rooms = {r_id for r_id, r in plan.rooms.items() if not r.is_eligible}
        fixed_nodes = set(plan.nodes.keys())

        room_assignment_counts: Dict[str, list[str]] = {r_id: [] for r_id in eligible_rooms}
        node_assignment_counts: Dict[str, list[str]] = {n_id: [] for n_id in fixed_nodes}

        for hub in hubs:
            # 1. Excluded rooms must NOT be assigned to any hub
            for r_id in hub.assigned_rooms:
                if r_id in excluded_rooms:
                    violations.append(
                        RuleViolation(
                            rule_number=1,
                            rule_name="Full Coverage",
                            hub_id=hub.hub_id,
                            room_id=r_id,
                            message=(
                                f"Excluded space '{r_id}' ({plan.rooms[r_id].category}) "
                                f"assigned to hub '{hub.hub_id}'"
                            ),
                        )
                    )
                elif r_id in room_assignment_counts:
                    room_assignment_counts[r_id].append(hub.hub_id)
                else:
                    violations.append(
                        RuleViolation(
                            rule_number=1,
                            rule_name="Full Coverage",
                            hub_id=hub.hub_id,
                            room_id=r_id,
                            message=f"Unknown room '{r_id}' assigned to hub '{hub.hub_id}'",
                        )
                    )

            # Fixed nodes
            for n_id in hub.assigned_nodes:
                if n_id in node_assignment_counts:
                    node_assignment_counts[n_id].append(hub.hub_id)
                else:
                    violations.append(
                        RuleViolation(
                            rule_number=1,
                            rule_name="Full Coverage",
                            hub_id=hub.hub_id,
                            message=f"Unknown node '{n_id}' assigned to hub '{hub.hub_id}'",
                        )
                    )

        # Check coverage of eligible rooms
        for r_id, assigned_hubs in room_assignment_counts.items():
            if len(assigned_hubs) == 0:
                violations.append(
                    RuleViolation(
                        rule_number=1,
                        rule_name="Full Coverage",
                        room_id=r_id,
                        message=f"Eligible room '{r_id}' is not assigned to any hub",
                    )
                )
            elif len(assigned_hubs) > 1:
                violations.append(
                    RuleViolation(
                        rule_number=1,
                        rule_name="Full Coverage",
                        room_id=r_id,
                        message=(
                            f"Eligible room '{r_id}' is assigned to multiple hubs: {assigned_hubs}"
                        ),
                    )
                )

        # Check coverage of fixed nodes
        for n_id, assigned_hubs in node_assignment_counts.items():
            loc = plan.nodes[n_id].location if n_id in plan.nodes else None
            if len(assigned_hubs) == 0:
                violations.append(
                    RuleViolation(
                        rule_number=1,
                        rule_name="Full Coverage",
                        location=loc,
                        message=f"Fixed node '{n_id}' is not assigned to any hub",
                    )
                )
            elif len(assigned_hubs) > 1:
                violations.append(
                    RuleViolation(
                        rule_number=1,
                        rule_name="Full Coverage",
                        location=loc,
                        message=f"Fixed node '{n_id}' is assigned to multiple hubs: {assigned_hubs}",
                    )
                )

    # -------------------------------------------------------------------------
    # Hard Rule 2: Capacity
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_2_capacity(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        violations: list[RuleViolation],
        max_area_m2: float = 100.0,
        max_nodes_per_hub: int = 5,
    ) -> None:
        """Each hub serves <= 100 m^2 of eligible area and <= 5 fixed nodes."""
        for hub in hubs:
            # Sum of assigned eligible room area
            area_sum = sum(
                plan.rooms[r_id].area_m2
                for r_id in hub.assigned_rooms
                if r_id in plan.rooms and plan.rooms[r_id].is_eligible
            )
            if area_sum > max_area_m2 + 1e-4:
                violations.append(
                    RuleViolation(
                        rule_number=2,
                        rule_name="Capacity",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' assigned area ({area_sum:.2f} m^2) exceeds "
                            f"capacity limit of {max_area_m2:.1f} m^2"
                        ),
                    )
                )

            if len(hub.assigned_nodes) > max_nodes_per_hub:
                violations.append(
                    RuleViolation(
                        rule_number=2,
                        rule_name="Capacity",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' serves {len(hub.assigned_nodes)} nodes, "
                            f"exceeding capacity limit of {max_nodes_per_hub}"
                        ),
                    )
                )

    # -------------------------------------------------------------------------
    # Hard Rule 3: Doorway Contiguity
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_3_contiguity(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        topology: MacroTopologyGraph,
        violations: list[RuleViolation],
    ) -> None:
        """Hub's assigned rooms must form a connected subgraph through doors/open passages only."""
        room_graph = topology.room_graph
        for hub in hubs:
            rooms = hub.assigned_rooms
            if len(rooms) <= 1:
                continue

            subgraph = room_graph.subgraph(rooms)
            if not nx.is_connected(subgraph):
                violations.append(
                    RuleViolation(
                        rule_number=3,
                        rule_name="Doorway Contiguity",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' assigned rooms {rooms} are not contiguous "
                            f"through doors/open passages"
                        ),
                    )
                )

    # -------------------------------------------------------------------------
    # Hard Rule 4: Closed Non-Branching Ring
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_4_closed_ring(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        violations: list[RuleViolation],
        tol: float = 0.001,
    ) -> None:
        """One closed ring per hub visiting every assigned node once and returning to hub."""
        for hub in hubs:
            seq = hub.closed_sequence
            if not seq:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=f"Hub '{hub.hub_id}' closed_sequence is empty",
                    )
                )
                continue

            # Must start and end at hub_id
            if seq[0] != hub.hub_id:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' closed_sequence must start with '{hub.hub_id}', "
                            f"got '{seq[0]}'"
                        ),
                    )
                )
            if seq[-1] != hub.hub_id:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' closed_sequence must end with '{hub.hub_id}', "
                            f"got '{seq[-1]}'"
                        ),
                    )
                )

            # Intermediate nodes visit each assigned node exactly once
            intermediate = seq[1:-1]
            if set(intermediate) != set(hub.assigned_nodes) or len(intermediate) != len(
                hub.assigned_nodes
            ):
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=(
                            f"Hub '{hub.hub_id}' closed_sequence intermediate nodes {intermediate} "
                            f"do not visit assigned nodes {hub.assigned_nodes} exactly once"
                        ),
                    )
                )

            # Check continuous segment chain
            segs = hub.route_segments
            if not segs:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=f"Hub '{hub.hub_id}' has no route segments",
                    )
                )
                continue

            # 1. First segment start point must match hub.location
            first_pt = segs[0].polyline[0] if segs[0].polyline else None
            if first_pt is None:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=f"Hub '{hub.hub_id}' first route segment has empty polyline",
                    )
                )
            else:
                dist_first = math.hypot(first_pt.x - hub.location.x, first_pt.y - hub.location.y)
                if dist_first > tol:
                    violations.append(
                        RuleViolation(
                            rule_number=4,
                            rule_name="Closed Ring",
                            hub_id=hub.hub_id,
                            location=first_pt,
                            message=(
                                f"Hub '{hub.hub_id}' ring start does not match hub location "
                                f"({dist_first:.4f} m > {tol:.4f} m)"
                            ),
                        )
                    )

            # 2. Last segment end point must match hub.location
            last_pt = segs[-1].polyline[-1] if segs[-1].polyline else None
            if last_pt is None:
                violations.append(
                    RuleViolation(
                        rule_number=4,
                        rule_name="Closed Ring",
                        hub_id=hub.hub_id,
                        message=f"Hub '{hub.hub_id}' last route segment has empty polyline",
                    )
                )
            else:
                dist_last = math.hypot(last_pt.x - hub.location.x, last_pt.y - hub.location.y)
                if dist_last > tol:
                    violations.append(
                        RuleViolation(
                            rule_number=4,
                            rule_name="Closed Ring",
                            hub_id=hub.hub_id,
                            location=last_pt,
                            message=(
                                f"Hub '{hub.hub_id}' ring return endpoint does not match hub location "
                                f"({dist_last:.4f} m > {tol:.4f} m)"
                            ),
                        )
                    )

            # 3. Consecutive segment continuity
            for i in range(len(segs) - 1):
                p_end = segs[i].polyline[-1] if segs[i].polyline else None
                p_next_start = segs[i + 1].polyline[0] if segs[i + 1].polyline else None
                if p_end is None or p_next_start is None:
                    continue
                gap = math.hypot(p_end.x - p_next_start.x, p_end.y - p_next_start.y)
                if gap > tol:
                    violations.append(
                        RuleViolation(
                            rule_number=4,
                            rule_name="Closed Ring",
                            hub_id=hub.hub_id,
                            location=p_end,
                            message=(
                                f"Hub '{hub.hub_id}' gap between segment {i} and {i+1} "
                                f"({gap:.4f} m > {tol:.4f} m)"
                            ),
                        )
                    )

            # 4. Check terminal node matching
            for seg in segs:
                if not seg.polyline:
                    continue
                # Start node target
                start_target = (
                    hub.location
                    if seg.from_node == hub.hub_id
                    else (plan.nodes[seg.from_node].location if seg.from_node in plan.nodes else None)
                )
                if start_target is not None:
                    d_s = math.hypot(
                        seg.polyline[0].x - start_target.x, seg.polyline[0].y - start_target.y
                    )
                    if d_s > tol:
                        violations.append(
                            RuleViolation(
                                rule_number=4,
                                rule_name="Closed Ring",
                                hub_id=hub.hub_id,
                                location=seg.polyline[0],
                                message=(
                                    f"Segment start does not match terminal '{seg.from_node}' "
                                    f"({d_s:.4f} m > {tol:.4f} m)"
                                ),
                            )
                        )

                # End node target
                end_target = (
                    hub.location
                    if seg.to_node == hub.hub_id
                    else (plan.nodes[seg.to_node].location if seg.to_node in plan.nodes else None)
                )
                if end_target is not None:
                    d_e = math.hypot(
                        seg.polyline[-1].x - end_target.x, seg.polyline[-1].y - end_target.y
                    )
                    if d_e > tol:
                        violations.append(
                            RuleViolation(
                                rule_number=4,
                                rule_name="Closed Ring",
                                hub_id=hub.hub_id,
                                location=seg.polyline[-1],
                                message=(
                                    f"Segment end does not match terminal '{seg.to_node}' "
                                    f"({d_e:.4f} m > {tol:.4f} m)"
                                ),
                            )
                        )

    # -------------------------------------------------------------------------
    # Hard Rule 5: Axis-Aligned Free Space
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_5_free_space_and_axis_alignment(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        violations: list[RuleViolation],
        tol: float = 0.001,
    ) -> None:
        """Cables must run axis-aligned and lie entirely in free space outside walls/windows."""
        obs_geom = plan.obstacles_geom
        inner_geom = plan.inner_geom
        free_space_geom = plan.free_space_geom

        # Buffer inner boundary and free space by tolerance for safe floating-point checks
        buffered_inner = inner_geom.buffer(tol)
        buffered_free = free_space_geom.buffer(tol)

        for hub in hubs:
            for seg in hub.route_segments:
                pts = seg.polyline
                if len(pts) < 2:
                    continue

                for i in range(len(pts) - 1):
                    p1 = pts[i]
                    p2 = pts[i + 1]
                    dx = abs(p2.x - p1.x)
                    dy = abs(p2.y - p1.y)

                    # 1. Axis-alignment check
                    if dx > 1e-4 and dy > 1e-4:
                        violations.append(
                            RuleViolation(
                                rule_number=5,
                                rule_name="Free Space",
                                hub_id=hub.hub_id,
                                location=p1,
                                message=(
                                    f"Segment ({p1.x:.3f}, {p1.y:.3f}) -> ({p2.x:.3f}, {p2.y:.3f}) "
                                    f"is diagonal (dx={dx:.4f}, dy={dy:.4f})"
                                ),
                            )
                        )

                    # 2. Geometry check: line within free space and not intersecting obstacles
                    seg_len = math.hypot(p2.x - p1.x, p2.y - p1.y)
                    if seg_len < 1e-4:
                        continue

                    line = LineString([(p1.x, p1.y), (p2.x, p2.y)])

                    # Check obstacle penetration
                    inter = obs_geom.intersection(line)
                    if not inter.is_empty and inter.length > tol:
                        violations.append(
                            RuleViolation(
                                rule_number=5,
                                rule_name="Free Space",
                                hub_id=hub.hub_id,
                                location=p1,
                                message=(
                                    f"Segment penetrates wall/window obstacle "
                                    f"(intersection length {inter.length:.4f} m > {tol:.4f} m)"
                                ),
                            )
                        )

                    # Check outside inner boundary
                    diff_inner = line.difference(buffered_inner)
                    if not diff_inner.is_empty and diff_inner.length > tol:
                        violations.append(
                            RuleViolation(
                                rule_number=5,
                                rule_name="Free Space",
                                hub_id=hub.hub_id,
                                location=p1,
                                message=(
                                    f"Segment exits inner boundary "
                                    f"(outside length {diff_inner.length:.4f} m > {tol:.4f} m)"
                                ),
                            )
                        )

                    # Check within free space
                    diff_free = line.difference(buffered_free)
                    if not diff_free.is_empty and diff_free.length > tol:
                        violations.append(
                            RuleViolation(
                                rule_number=5,
                                rule_name="Free Space",
                                hub_id=hub.hub_id,
                                location=p1,
                                message=(
                                    f"Segment lies outside free space "
                                    f"(violation length {diff_free.length:.4f} m > {tol:.4f} m)"
                                ),
                            )
                        )

    # -------------------------------------------------------------------------
    # Hard Rule 6: Hub Clearance
    # -------------------------------------------------------------------------

    @classmethod
    def _check_rule_6_hub_clearance(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        violations: list[RuleViolation],
        tol: float = 0.001,
        min_clearance_m: float = 0.50,
    ) -> None:
        """Hub must lie inside an assigned eligible room >= 0.50 m from walls."""
        obs_geom = plan.obstacles_geom
        inner_boundary = plan.inner_geom.boundary

        for hub in hubs:
            # 1. Designated room must exist
            if hub.room_id not in plan.rooms:
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=f"Hub designated room '{hub.room_id}' not found in plan",
                    )
                )
                continue

            host_room = plan.rooms[hub.room_id]

            # 2. Must be eligible
            if not host_room.is_eligible:
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=(
                            f"Hub placed in ineligible/excluded room '{hub.room_id}' "
                            f"({host_room.category})"
                        ),
                    )
                )

            # 3. Host room must be in hub's assigned rooms
            if hub.room_id not in hub.assigned_rooms:
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=(
                            f"Hub host room '{hub.room_id}' is not in hub's assigned rooms "
                            f"{hub.assigned_rooms}"
                        ),
                    )
                )

            # 4. Hub location inside host room geometry
            hub_pt = Point(hub.location.x, hub.location.y)
            if not host_room.geometry.buffer(tol).contains(hub_pt):
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=(
                            f"Hub location ({hub.location.x:.3f}, {hub.location.y:.3f}) "
                            f"is not inside host room '{hub.room_id}'"
                        ),
                    )
                )

            # 5. Clearance to walls and windows
            required_clearance = min_clearance_m - tol  # e.g. 0.499 m
            dist_to_obs = obs_geom.distance(hub_pt)
            if dist_to_obs < required_clearance:
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=(
                            f"Hub clearance to walls/windows ({dist_to_obs:.4f} m) is less than "
                            f"required {min_clearance_m:.2f} m"
                        ),
                    )
                )

            # Clearance to exterior inner boundary
            dist_to_inner = inner_boundary.distance(hub_pt)
            if dist_to_inner < required_clearance:
                violations.append(
                    RuleViolation(
                        rule_number=6,
                        rule_name="Hub Clearance",
                        hub_id=hub.hub_id,
                        room_id=hub.room_id,
                        location=hub.location,
                        message=(
                            f"Hub clearance to exterior boundary ({dist_to_inner:.4f} m) is less "
                            f"than required {min_clearance_m:.2f} m"
                        ),
                    )
                )

    # -------------------------------------------------------------------------
    # Recomputed Competition Metrics (Slides 3, 6 & 7)
    # -------------------------------------------------------------------------

    @classmethod
    def _recompute_metrics(
        cls,
        plan: MetricFloorPlan,
        hubs: list[_NormalizedHub],
        tol: float = 0.001,
    ) -> Tuple[list[EvaluatedHubMetrics], float, int, float, int, float, float]:
        """Recompute exact L, X, O, B, J, and Delta A_gap independently from raw polylines."""
        # Pre-compress all route segments per hub
        compressed_hub_legs: list[list[list[Point2D]]] = []
        for hub in hubs:
            legs = [_clean_and_compress_polyline(seg.polyline, tol=tol) for seg in hub.route_segments]
            compressed_hub_legs.append(legs)

        # 1. Length L (exact sum of polyline segment distances)
        hub_lengths: list[float] = []
        for legs in compressed_hub_legs:
            ring_l = 0.0
            for leg in legs:
                for i in range(len(leg) - 1):
                    ring_l += math.hypot(leg[i + 1].x - leg[i].x, leg[i + 1].y - leg[i].y)
            hub_lengths.append(float(round(ring_l, 4)))
        total_L = float(round(sum(hub_lengths), 4))

        # 2. Bends B (per closed ring, direction transitions in cyclic order)
        hub_bends: list[int] = []
        for legs in compressed_hub_legs:
            hub_bends.append(cls._compute_ring_bends(legs, tol=tol))
        total_B = sum(hub_bends)

        # 3. Crossings X (pairwise intersection away from node/hub terminals)
        # Collect terminal points: all hubs + all fixed nodes + leg endpoints
        terminal_points: list[Point2D] = [h.location for h in hubs]
        for n in plan.nodes.values():
            terminal_points.append(n.location)
        for legs in compressed_hub_legs:
            for leg in legs:
                if leg:
                    terminal_points.append(leg[0])
                    terminal_points.append(leg[-1])

        # Per-hub crossings
        hub_crossings: list[int] = []
        for legs in compressed_hub_legs:
            hub_crossings.append(cls._compute_crossings_for_legs(legs, terminal_points, tol=tol))

        # Total crossings across all hubs combined
        all_legs = [leg for legs in compressed_hub_legs for leg in legs]
        total_X = cls._compute_crossings_for_legs(all_legs, terminal_points, tol=tol)

        # 4. Overlap O (collinear intervals covered by k >= 2 segments)
        hub_overlaps: list[float] = []
        for legs in compressed_hub_legs:
            hub_overlaps.append(cls._compute_overlap_for_legs(legs, tol=tol))
        total_O = cls._compute_overlap_for_legs(all_legs, tol=tol)

        # 5. Composite Cost J = L + 5X + O + 0.5B
        hub_costs: list[float] = []
        for l_m, x_cnt, o_m, b_cnt in zip(hub_lengths, hub_crossings, hub_overlaps, hub_bends):
            j_k = round(l_m + 5.0 * x_cnt + o_m + 0.5 * b_cnt, 4)
            hub_costs.append(float(j_k))
        total_J = float(round(total_L + 5.0 * total_X + total_O + 0.5 * total_B, 4))

        # 6. Assigned Area Gap Delta A_gap
        hub_areas: list[float] = []
        for hub in hubs:
            a_k = sum(
                plan.rooms[r_id].area_m2
                for r_id in hub.assigned_rooms
                if r_id in plan.rooms and plan.rooms[r_id].is_eligible
            )
            hub_areas.append(float(round(a_k, 4)))

        if len(hub_areas) >= 2:
            area_gap = float(round(max(hub_areas) - min(hub_areas), 4))
        else:
            area_gap = 0.0

        # Construct EvaluatedHubMetrics per hub
        hub_metrics_list: list[EvaluatedHubMetrics] = []
        for i, hub in enumerate(hubs):
            hub_metrics_list.append(
                EvaluatedHubMetrics(
                    hub_id=hub.hub_id,
                    room_id=hub.room_id,
                    location=hub.location,
                    assigned_rooms=hub.assigned_rooms,
                    assigned_nodes=hub.assigned_nodes,
                    closed_sequence=hub.closed_sequence,
                    length_m=hub_lengths[i],
                    crossings=hub_crossings[i],
                    overlap_m=hub_overlaps[i],
                    bends=hub_bends[i],
                    cost_J=hub_costs[i],
                    zone_area_m2=hub_areas[i],
                )
            )

        return hub_metrics_list, total_L, total_X, total_O, total_B, total_J, area_gap

    # -------------------------------------------------------------------------
    # Metric Helper: Bends
    # -------------------------------------------------------------------------

    @classmethod
    def _compute_ring_bends(cls, legs: list[list[Point2D]], tol: float = 0.001) -> int:
        """Count bends along a closed ring by comparing consecutive unit directions in cyclic order.

        Slide 7 semantics:
        - Every 90 deg turn counts as B = 1.
        - A 180 deg reversal counts as B = 1.
        - Straight-through passage counts as B = 0.
        - Wrap-around transition at the hub is evaluated between last leg and first leg.
        """
        # Collect all non-zero directed segments in cycle order
        directed_segments: list[Tuple[Point2D, Point2D, str]] = []
        for leg in legs:
            for i in range(len(leg) - 1):
                p1 = leg[i]
                p2 = leg[i + 1]
                dx = p2.x - p1.x
                dy = p2.y - p1.y
                dist = math.hypot(dx, dy)
                if dist <= tol:
                    continue

                if abs(dx) >= abs(dy):
                    direction = "EAST" if dx > 0 else "WEST"
                else:
                    direction = "NORTH" if dy > 0 else "SOUTH"

                directed_segments.append((p1, p2, direction))

        if len(directed_segments) < 2:
            return 0

        bends = 0
        m = len(directed_segments)
        for i in range(m):
            dir_curr = directed_segments[i][2]
            dir_next = directed_segments[(i + 1) % m][2]
            if dir_curr != dir_next:
                bends += 1

        return bends

    # -------------------------------------------------------------------------
    # Metric Helper: Crossings
    # -------------------------------------------------------------------------

    @classmethod
    def _compute_crossings_for_legs(
        cls,
        legs: list[list[Point2D]],
        terminal_points: Sequence[Point2D],
        tol: float = 0.001,
    ) -> int:
        """Find non-collinear cable intersection points away from node/hub terminals.

        Slide 7 semantics:
        - Internal turns of a leg are bends, not crossings.
        - Legs meeting at a node or hub do NOT count as crossings (X = 0).
        - Multiple cables crossing at the same location are deduplicated to 1 crossing.
        """
        # Collect elementary line segments with metadata (leg_idx, seg_idx)
        segments_with_meta: list[Tuple[int, int, LineString]] = []
        for leg_idx, leg in enumerate(legs):
            for seg_idx in range(len(leg) - 1):
                p1, p2 = leg[seg_idx], leg[seg_idx + 1]
                if math.hypot(p2.x - p1.x, p2.y - p1.y) > tol:
                    segments_with_meta.append(
                        (leg_idx, seg_idx, LineString([(p1.x, p1.y), (p2.x, p2.y)]))
                    )

        terminal_geoms = [Point(t.x, t.y) for t in terminal_points]
        crossing_points: list[Tuple[float, float]] = []

        n = len(segments_with_meta)
        for i in range(n):
            l1, s1, ls1 = segments_with_meta[i]
            for j in range(i + 1, n):
                l2, s2, ls2 = segments_with_meta[j]

                # Consecutive segments of same leg meet at an internal corner
                if l1 == l2 and abs(s1 - s2) == 1:
                    continue

                inter = ls1.intersection(ls2)
                if inter.is_empty:
                    continue

                pts: list[Point] = []
                if inter.geom_type == "Point":
                    pts = [inter]
                elif inter.geom_type == "MultiPoint":
                    pts = list(inter.geoms)

                for pt in pts:
                    # 1. Must be away from any node or hub terminal (> 1 mm)
                    if any(pt.distance(t) <= tol * 2.0 for t in terminal_geoms):
                        continue

                    # 2. Must not be the endpoint of either leg
                    is_leg_endpoint = False
                    for leg in legs:
                        if not leg:
                            continue
                        p_start = Point(leg[0].x, leg[0].y)
                        p_end = Point(leg[-1].x, leg[-1].y)
                        if pt.distance(p_start) <= tol * 2.0 or pt.distance(p_end) <= tol * 2.0:
                            is_leg_endpoint = True
                            break
                    if is_leg_endpoint:
                        continue

                    cx, cy = round(pt.x, 4), round(pt.y, 4)
                    # Deduplicate points within tolerance
                    if not any(
                        abs(cx - ex) <= tol and abs(cy - ey) <= tol for ex, ey in crossing_points
                    ):
                        crossing_points.append((cx, cy))

        return len(crossing_points)

    # -------------------------------------------------------------------------
    # Metric Helper: Overlap
    # -------------------------------------------------------------------------

    @classmethod
    def _compute_overlap_for_legs(
        cls,
        legs: list[list[Point2D]],
        tol: float = 0.001,
    ) -> float:
        """Calculate overlap O in metres where cables share the same line (<= 1 mm lateral distance).

        Slide 7 semantics:
        - Stretches covered by k >= 2 legs count (k - 1) times their length.
        - Parallel runs on different lines (> 1 mm apart) yield O = 0.0 m.
        """
        # Separate segments into horizontal and vertical
        horiz_segs: list[Tuple[float, float, float]] = []  # (y, x_min, x_max)
        vert_segs: list[Tuple[float, float, float]] = []  # (x, y_min, y_max)

        for leg in legs:
            for i in range(len(leg) - 1):
                p1, p2 = leg[i], leg[i + 1]
                dx = abs(p2.x - p1.x)
                dy = abs(p2.y - p1.y)
                if dx > tol and dy <= tol:
                    horiz_segs.append((p1.y, min(p1.x, p2.x), max(p1.x, p2.x)))
                elif dy > tol and dx <= tol:
                    vert_segs.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y)))

        total_overlap = 0.0

        # Process horizontal segments
        total_overlap += cls._cluster_and_compute_interval_overlap(horiz_segs, tol=tol)

        # Process vertical segments
        total_overlap += cls._cluster_and_compute_interval_overlap(vert_segs, tol=tol)

        return float(round(total_overlap, 4))

    @classmethod
    def _cluster_and_compute_interval_overlap(
        cls,
        segs: list[Tuple[float, float, float]],
        tol: float = 0.001,
    ) -> float:
        """Cluster 1D collinear segments within lateral tolerance and compute (k - 1) overlap."""
        if len(segs) < 2:
            return 0.0

        # Sort by lateral coordinate (coord, min_val, max_val)
        sorted_segs = sorted(segs, key=lambda s: s[0])

        clusters: list[list[Tuple[float, float, float]]] = []
        for s in sorted_segs:
            if not clusters:
                clusters.append([s])
            else:
                # Compare with current cluster average or anchor
                cluster_coord = clusters[-1][0][0]
                if abs(s[0] - cluster_coord) <= tol:
                    clusters[-1].append(s)
                else:
                    clusters.append([s])

        overlap_m = 0.0
        for cluster in clusters:
            if len(cluster) < 2:
                continue

            # Collect all unique cut points along the longitudinal axis
            cut_points: list[float] = []
            for _, u_min, u_max in cluster:
                cut_points.append(u_min)
                cut_points.append(u_max)
            cut_points = sorted(list(set(round(u, 6) for u in cut_points)))

            # Evaluate each elementary interval
            for idx in range(len(cut_points) - 1):
                u_start = cut_points[idx]
                u_end = cut_points[idx + 1]
                delta = u_end - u_start
                if delta <= tol:
                    continue

                u_mid = (u_start + u_end) / 2.0
                k = sum(1 for _, u_min, u_max in cluster if u_min <= u_mid <= u_max)
                if k >= 2:
                    overlap_m += (k - 1) * delta

        return overlap_m
