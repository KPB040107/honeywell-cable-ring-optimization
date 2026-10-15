"""Architectural Co-Pilot Candidate Perturbation Generator (Phase 8).

Identifies candidate structural modifications—adding ghost open passages on unlinked
shared interior walls and sliding existing doors along walls—to guide cable routing
and hub partition optimization.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import shapely
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    DoorOrPassagePortal,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    ProposalType,
)
from resplan_router.phase2_topology.macro_graph import MacroTopologyGraph


def _extract_linestrings(geom: Optional[BaseGeometry]) -> List[LineString]:
    """Recursively extract all LineString geometries from a Shapely geometry."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        lines: List[LineString] = []
        for g in geom.geoms:
            lines.extend(_extract_linestrings(g))
        return lines
    return []


def _get_shared_boundary(poly1: BaseGeometry, poly2: BaseGeometry) -> Optional[BaseGeometry]:
    """Compute the shared 1D boundary line segments between two room polygons."""
    inter = poly1.intersection(poly2)
    lines = _extract_linestrings(inter)
    if lines:
        return shapely.unary_union(lines)

    # Check boundary-to-boundary intersection
    b_inter = poly1.boundary.intersection(poly2.boundary)
    lines = _extract_linestrings(b_inter)
    if lines:
        return shapely.unary_union(lines)

    # Check poly1 boundary inside poly2
    p1_in_p2 = poly1.boundary.intersection(poly2)
    lines = _extract_linestrings(p1_in_p2)
    if lines:
        return shapely.unary_union(lines)

    return None


def _decompose_into_straight_segments(
    geom: BaseGeometry,
) -> List[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    """Decompose line geometry into straight segments with endpoints and length.

    Returns:
        List of tuples ((x1, y1), (x2, y2), length).
    """
    raw_lines = _extract_linestrings(geom)
    if not raw_lines:
        return []

    # Attempt to line_merge contiguous segments
    merged = shapely.line_merge(shapely.unary_union(raw_lines))
    lines = _extract_linestrings(merged)

    segments: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    for line in lines:
        # Simplify collinear points
        simp = shapely.simplify(line, tolerance=0.001)
        coords = list(simp.coords)
        for k in range(len(coords) - 1):
            p1 = coords[k]
            p2 = coords[k + 1]
            seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if seg_len >= 0.05:
                segments.append((p1, p2, seg_len))
    return segments


class PerturbationCandidateGenerator:
    """Detects shared interior walls and movable doorways for architectural co-pilot search."""

    def __init__(
        self,
        min_wall_length_m: float = 1.0,
        corner_clearance_m: float = 0.50,
        default_door_width_m: float = 0.90,
    ) -> None:
        self.min_wall_length_m = min_wall_length_m
        self.corner_clearance_m = corner_clearance_m
        self.default_door_width_m = default_door_width_m

    @staticmethod
    def _resolve_topology(
        topology: Union[MacroTopologyGraph, PortalTopologyGraph],
    ) -> Tuple[PortalTopologyGraph, Optional[MacroTopologyGraph]]:
        """Normalize topology input to PortalTopologyGraph and optional MacroTopologyGraph."""
        if isinstance(topology, MacroTopologyGraph):
            return topology.topology or PortalTopologyGraph(
                plan_id=topology.plan.plan_id if topology.plan else "",
                eligible_room_ids=[],
                portals=topology.portals,
                adjacency={},
                is_fully_connected=False,
                leaf_room_ids=[],
            ), topology
        return topology, None

    def find_shared_interior_walls(
        self,
        plan: MetricFloorPlan,
        topology: Union[MacroTopologyGraph, PortalTopologyGraph],
        windows: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Identify candidate shared interior walls between adjacent eligible rooms.

        Finds shared 1D boundary line segments with length >= 1.0 m between eligible rooms,
        strictly excluding exterior boundaries and window segments. If no direct portal
        exists between the rooms (or a connected room is a leaf room), samples ghost door
        centers along the wall with >= 0.50 m corner clearance.

        Args:
            plan: Normalized metric floor plan contract.
            topology: Macro topology graph or portal topology contract.
            windows: Optional list of raw window polygons to exclude.

        Returns:
            List of candidate dictionaries detailing ghost door locations.
        """
        portal_topo, macro_topo = self._resolve_topology(topology)
        eligible_room_ids = [
            r_id for r_id, r in plan.rooms.items() if r.is_eligible
        ]
        leaf_room_ids = set(portal_topo.leaf_room_ids)

        # Build existing portal lookup: set of frozenset({r1, r2})
        existing_door_pairs: Set[frozenset[str]] = set()
        for p in portal_topo.portals:
            existing_door_pairs.add(frozenset([p.room1_id, p.room2_id]))

        exterior_boundary = plan.inner_geom.boundary

        # Window geometry union if any
        window_union: Optional[BaseGeometry] = None
        if windows:
            from resplan_router.phase2_topology.portal_extractor import _ensure_geometry, _flatten_geometries
            flat_win = [_ensure_geometry(w) for w in _flatten_geometries(windows)]
            valid_win = [w for w in flat_win if w is not None and not w.is_empty]
            if valid_win:
                window_union = shapely.unary_union(valid_win)

        candidates: List[Dict[str, Any]] = []

        # Iterate all pairs of eligible rooms
        for i in range(len(eligible_room_ids)):
            r_a_id = eligible_room_ids[i]
            r_a_poly = plan.rooms[r_a_id].geometry

            for j in range(i + 1, len(eligible_room_ids)):
                r_b_id = eligible_room_ids[j]
                r_b_poly = plan.rooms[r_b_id].geometry

                # Compute shared 1D boundary
                shared = _get_shared_boundary(r_a_poly, r_b_poly)
                if shared is None or shared.is_empty:
                    continue

                segments = _decompose_into_straight_segments(shared)
                has_direct_portal = frozenset([r_a_id, r_b_id]) in existing_door_pairs
                is_leaf_pair = (r_a_id in leaf_room_ids) or (r_b_id in leaf_room_ids)

                # Propose ghost door if no direct portal exists OR one room is a leaf room
                if has_direct_portal and not is_leaf_pair:
                    continue

                for p1, p2, seg_len in segments:
                    # Filter: wall segment must be >= min_wall_length_m (default 1.0 m)
                    if seg_len < self.min_wall_length_m:
                        continue

                    seg_line = LineString([p1, p2])

                    # Exclude exterior wall overlap: if segment overlaps exterior boundary
                    exterior_inter = seg_line.intersection(exterior_boundary)
                    if not exterior_inter.is_empty and exterior_inter.length > 0.05:
                        continue
                    if exterior_boundary.distance(seg_line) < 0.001:
                        # Check if entire segment is collinear with exterior
                        if seg_line.intersection(exterior_boundary.buffer(0.01)).length > 0.9 * seg_len:
                            continue

                    # Exclude window overlap
                    if window_union is not None and not window_union.is_empty:
                        win_inter = seg_line.intersection(window_union)
                        if not win_inter.is_empty and win_inter.length > 0.05:
                            continue

                    # Sample candidate centers along the wall
                    # Midpoint is always tested; 25% and 75% sampled for walls >= 2.0 m
                    sample_ratios = [0.50]
                    if seg_len >= 2.0:
                        sample_ratios.extend([0.25, 0.75])

                    for ratio in sample_ratios:
                        cx = p1[0] + ratio * (p2[0] - p1[0])
                        cy = p1[1] + ratio * (p2[1] - p1[1])
                        c_pt = Point(cx, cy)

                        # Enforce corner clearance
                        d1 = math.hypot(cx - p1[0], cy - p1[1])
                        d2 = math.hypot(cx - p2[0], cy - p2[1])
                        if d1 < self.corner_clearance_m - 1e-4 or d2 < self.corner_clearance_m - 1e-4:
                            continue

                        # If windows present, check center clearance
                        if window_union is not None and not window_union.is_empty:
                            if window_union.distance(c_pt) < (self.default_door_width_m / 2.0):
                                continue

                        # If direct portal already exists between this pair, ensure new center is not duplicate
                        if has_direct_portal:
                            is_dup = False
                            for p in portal_topo.portals:
                                if frozenset([p.room1_id, p.room2_id]) == frozenset([r_a_id, r_b_id]):
                                    if math.hypot(cx - p.center_point.x, cy - p.center_point.y) < 1.0:
                                        is_dup = True
                                        break
                            if is_dup:
                                continue

                        cand_id = f"ghost_{r_a_id}_{r_b_id}_{int(round(cx * 100))}_{int(round(cy * 100))}"
                        candidates.append({
                            "candidate_id": cand_id,
                            "proposal_type": ProposalType.ADD_GHOST_DOOR,
                            "room_a_id": r_a_id,
                            "room_b_id": r_b_id,
                            "wall_segment": [
                                Point2D(x=round(p1[0], 4), y=round(p1[1], 4)),
                                Point2D(x=round(p2[0], 4), y=round(p2[1], 4)),
                            ],
                            "suggested_door_center": Point2D(x=round(cx, 4), y=round(cy, 4)),
                            "door_width_m": self.default_door_width_m,
                        })

        return candidates

    def find_sliding_door_candidates(
        self,
        plan: MetricFloorPlan,
        topology: Union[MacroTopologyGraph, PortalTopologyGraph],
    ) -> List[Dict[str, Any]]:
        """Identify candidate sliding positions for existing interior doorways along host walls.

        For each existing interior portal, identifies the host wall segment and evaluates
        sliding offsets of +/- 0.50 m and +/- 1.00 m while preserving corner clearance.

        Args:
            plan: Normalized metric floor plan contract.
            topology: Macro topology graph or portal topology contract.

        Returns:
            List of candidate dictionaries detailing sliding door options.
        """
        portal_topo, _ = self._resolve_topology(topology)
        candidates: List[Dict[str, Any]] = []

        slide_offsets = [-1.0, -0.5, 0.5, 1.0]

        for portal in portal_topo.portals:
            r1 = plan.rooms.get(portal.room1_id)
            r2 = plan.rooms.get(portal.room2_id)
            if not r1 or not r2:
                continue

            # Compute shared boundary to find host wall
            shared = _get_shared_boundary(r1.geometry, r2.geometry)
            if shared is None or shared.is_empty:
                continue

            segments = _decompose_into_straight_segments(shared)
            if not segments:
                continue

            # Identify which segment hosts the portal
            p_center = Point(portal.center_point.x, portal.center_point.y)
            host_segment: Optional[Tuple[Tuple[float, float], Tuple[float, float], float]] = None
            min_dist = float("inf")

            for seg in segments:
                line = LineString([seg[0], seg[1]])
                d = line.distance(p_center)
                if d < min_dist and d < 0.10:
                    min_dist = d
                    host_segment = seg

            if host_segment is None:
                continue

            p1, p2, seg_len = host_segment
            # Unit direction along host wall
            dx = (p2[0] - p1[0]) / seg_len
            dy = (p2[1] - p1[1]) / seg_len

            curr_cx = portal.center_point.x
            curr_cy = portal.center_point.y

            for offset in slide_offsets:
                new_cx = curr_cx + offset * dx
                new_cy = curr_cy + offset * dy

                # Check clearance to both wall corners
                d1 = math.hypot(new_cx - p1[0], new_cy - p1[1])
                d2 = math.hypot(new_cx - p2[0], new_cy - p2[1])

                # Center must remain on the wall segment and maintain corner clearance
                proj = (new_cx - p1[0]) * dx + (new_cy - p1[1]) * dy
                if proj < self.corner_clearance_m - 1e-4 or proj > (seg_len - self.corner_clearance_m + 1e-4):
                    continue

                if d1 < self.corner_clearance_m - 1e-4 or d2 < self.corner_clearance_m - 1e-4:
                    continue

                cand_id = f"slide_{portal.portal_id}_{offset:+0.1f}"
                candidates.append({
                    "candidate_id": cand_id,
                    "proposal_type": ProposalType.SLIDE_DOOR,
                    "portal_id": portal.portal_id,
                    "room_a_id": portal.room1_id,
                    "room_b_id": portal.room2_id,
                    "wall_segment": [
                        Point2D(x=round(p1[0], 4), y=round(p1[1], 4)),
                        Point2D(x=round(p2[0], 4), y=round(p2[1], 4)),
                    ],
                    "suggested_door_center": Point2D(x=round(new_cx, 4), y=round(new_cy, 4)),
                    "door_width_m": portal.width_m,
                    "offset_m": offset,
                })

        return candidates

    def generate_all_candidates(
        self,
        plan: MetricFloorPlan,
        topology: Union[MacroTopologyGraph, PortalTopologyGraph],
        windows: Optional[Sequence[Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate all candidate structural perturbations (ghost doors and sliding doors)."""
        ghosts = self.find_shared_interior_walls(plan, topology, windows=windows)
        slides = self.find_sliding_door_candidates(plan, topology)
        return ghosts + slides
