"""Obstacle-aware rectilinear Hanan grid generator for free space micro-routing.

Constructs an axis-aligned grid inside routable free space F = inner \\ (wall U window),
induced by fixed nodes, portal threshold waypoints, parallel doorway lanes, room step points,
offset bypass lines (+/-0.04m), and room inset contours (0.15m).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, List, Optional, Sequence, Set, Tuple
import networkx as nx
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    CompassDirection,
    DoorOrPassagePortal,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
)


@dataclass(frozen=True, slots=True)
class GridEdge:
    """Directed or undirected axis-aligned grid edge between adjacent Hanan grid vertices."""

    u: Point2D
    v: Point2D
    direction: CompassDirection
    length_m: float
    room_id: Optional[str] = None
    portal_id: Optional[str] = None

    @property
    def tag(self) -> str:
        """Return the canonical tag: portal_id if present, else room_id, else 'unassigned'."""
        if self.portal_id:
            return self.portal_id
        return self.room_id or "unassigned"

    @property
    def linestring(self) -> LineString:
        """Return Shapely LineString representation of this edge."""
        return LineString([(self.u.x, self.u.y), (self.v.x, self.v.y)])

    def reverse(self) -> GridEdge:
        """Return reversed edge from v to u with opposite compass direction."""
        return GridEdge(
            u=self.v,
            v=self.u,
            direction=self.direction.opposite(),
            length_m=self.length_m,
            room_id=self.room_id,
            portal_id=self.portal_id,
        )


class HananRoutingGrid:
    """Obstacle-aware axis-aligned Hanan routing grid inside floor plan free space."""

    def __init__(
        self,
        plan_id: str,
        vertices: Sequence[Point2D],
        edges: Sequence[GridEdge],
        node_vertices: Optional[Dict[str, Point2D]] = None,
        portal_vertices: Optional[Dict[str, Dict[str, Point2D]]] = None,
    ) -> None:
        self.plan_id = plan_id
        self._vertices: List[Point2D] = list(vertices)
        self._vertex_set: Set[Point2D] = set(self._vertices)
        self._edges: List[GridEdge] = list(edges)
        self.node_vertices: Dict[str, Point2D] = dict(node_vertices or {})
        self.portal_vertices: Dict[str, Dict[str, Point2D]] = dict(portal_vertices or {})

        # Fast coordinate index for nearest/exact snapping: (x, y) -> Point2D
        self._coord_map: Dict[Tuple[float, float], Point2D] = {
            (v.x, v.y): v for v in self._vertices
        }

        # Build adjacency graph
        self._adj: Dict[Point2D, List[GridEdge]] = {v: [] for v in self._vertices}
        self.graph: nx.Graph = nx.Graph()

        for v in self._vertices:
            self.graph.add_node(v, coords=(v.x, v.y))

        for edge in self._edges:
            # Directed outgoing edge from u to v
            if edge.u in self._adj:
                self._adj[edge.u].append(edge)
            # Directed return edge from v to u
            rev = edge.reverse()
            if edge.v in self._adj:
                self._adj[edge.v].append(rev)

            # Add to NetworkX undirected graph
            self.graph.add_edge(
                edge.u,
                edge.v,
                weight=edge.length_m,
                length=edge.length_m,
                direction=edge.direction,
                room_id=edge.room_id,
                portal_id=edge.portal_id,
                edge=edge,
            )

    @property
    def vertices(self) -> List[Point2D]:
        """Return list of all grid vertices."""
        return self._vertices

    @property
    def edges(self) -> List[GridEdge]:
        """Return list of unique undirected grid edges."""
        return self._edges

    @property
    def vertex_count(self) -> int:
        """Return total number of vertices."""
        return len(self._vertices)

    @property
    def edge_count(self) -> int:
        """Return total number of edges."""
        return len(self._edges)

    def has_vertex(self, p: Point2D) -> bool:
        """Check if Point2D is an exact vertex in this grid."""
        return p in self._vertex_set or (p.x, p.y) in self._coord_map

    def get_vertex(self, p: Point2D) -> Optional[Point2D]:
        """Look up vertex matching coordinates of p."""
        if p in self._vertex_set:
            return p
        return self._coord_map.get((p.x, p.y))

    def snap_to_vertex(self, p: Point2D, tolerance: float = 0.001) -> Point2D:
        """Snap coordinate to the closest grid vertex within tolerance (metres).

        Raises KeyError if no vertex is within tolerance.
        """
        exact = self.get_vertex(p)
        if exact is not None:
            return exact

        # Search for closest within tolerance
        best_dist = float("inf")
        best_v: Optional[Point2D] = None
        for v in self._vertices:
            dist = math.hypot(v.x - p.x, v.y - p.y)
            if dist < best_dist:
                best_dist = dist
                best_v = v

        if best_v is not None and best_dist <= tolerance:
            return best_v

        raise KeyError(
            f"Point {p.coords} cannot be snapped to Hanan grid within tolerance {tolerance}m "
            f"(closest is {best_v.coords if best_v else 'none'} at {best_dist:.5f}m)"
        )

    def get_neighbors(self, u: Point2D) -> List[GridEdge]:
        """Return outgoing grid edges from vertex u."""
        vertex = self.get_vertex(u)
        if vertex is not None:
            return self._adj.get(vertex, [])
        return []


def deduplicate_coords(
    candidates: Sequence[float],
    exact_values: Set[float],
    tol: float = 0.001,
) -> List[float]:
    """Sort and deduplicate 1D coordinates with 1mm tolerance while preserving exact terminal values.

    Args:
        candidates: Candidate 1D coordinates.
        exact_values: Set of exact coordinates that must never be merged away or displaced.
        tol: Deduplication tolerance in metres (default 0.001m / 1mm).

    Returns:
        List[float]: Sorted, deduplicated coordinates.
    """
    exact_set = {round(float(x), 4) for x in exact_values}
    all_sorted = sorted({round(float(x), 4) for x in candidates if x >= 0.0})

    result: List[float] = []
    for x in all_sorted:
        if not result:
            result.append(x)
            continue

        prev = result[-1]
        if abs(x - prev) < tol:
            if x in exact_set and prev not in exact_set:
                result[-1] = x
            elif prev in exact_set and x not in exact_set:
                # Keep prev
                pass
            elif x in exact_set and prev in exact_set:
                result.append(x)
            else:
                # Neither is exact, keep first
                pass
        else:
            result.append(x)

    # Invariant guarantee: all exact coordinates MUST be present
    for ev in exact_set:
        if ev not in result:
            # Insert maintaining sorted order
            inserted = False
            for idx, r in enumerate(result):
                if ev < r:
                    result.insert(idx, ev)
                    inserted = True
                    break
            if not inserted:
                result.append(ev)

    return result


def build_hanan_grid(
    plan: MetricFloorPlan,
    topology: PortalTopologyGraph,
    extra_points: Optional[Sequence[Point2D]] = None,
) -> HananRoutingGrid:
    """Build an obstacle-aware axis-aligned Hanan routing grid inside plan free space.

    Extracts X and Y coordinates from:
    1. Fixed node coordinates in plan.nodes.
    2. Portal threshold centers, Lane A, Lane B, and step points from topology.portals.
    3. Offset bypass lines (+/-0.04m) around nodes and door centers.
    4. Room bounding box coordinates inset by 0.15m where within room interior.
    5. Any optional extra points (e.g. candidate hub coordinates).

    Filters candidate rectilinear segments against free space F = inner \\ (wall U window)
    and asserts zero obstacle collisions. Tags all edges with valid room or portal IDs.

    Args:
        plan: Normalized MetricFloorPlan with free_space_geom, obstacles_geom, inner_geom.
        topology: PortalTopologyGraph with extracted doorway and open-passage portals.
        extra_points: Optional extra terminal points to include in grid generation.

    Returns:
        HananRoutingGrid: Fully indexed axis-aligned routing grid.
    """
    exact_x: Set[float] = set()
    exact_y: Set[float] = set()
    cand_x: List[float] = []
    cand_y: List[float] = []

    node_vertices: Dict[str, Point2D] = {}
    portal_vertices: Dict[str, Dict[str, Point2D]] = {}

    # 1. Fixed node coordinates
    for node_id, node in plan.nodes.items():
        nx_val = round(node.location.x, 4)
        ny_val = round(node.location.y, 4)
        node_pt = Point2D(x=nx_val, y=ny_val)
        node_vertices[node_id] = node_pt
        exact_x.add(nx_val)
        exact_y.add(ny_val)
        cand_x.extend([nx_val, round(nx_val - 0.04, 4), round(nx_val + 0.04, 4)])
        cand_y.extend([ny_val, round(ny_val - 0.04, 4), round(ny_val + 0.04, 4)])

    # 2. Portal waypoints: center, lane_a, lane_b, step_room1, step_room2
    for portal in topology.portals:
        p_dict: Dict[str, Point2D] = {}
        for name, pt in [
            ("center", portal.center_point),
            ("lane_a", portal.lane_a_point),
            ("lane_b", portal.lane_b_point),
            ("step1", portal.step_room1),
            ("step2", portal.step_room2),
        ]:
            px = round(pt.x, 4)
            py = round(pt.y, 4)
            p_dict[name] = Point2D(x=px, y=py)
            exact_x.add(px)
            exact_y.add(py)
            cand_x.append(px)
            cand_y.append(py)

        portal_vertices[portal.portal_id] = p_dict

        # Bypass lines around door center (+/-0.04m)
        cx = round(portal.center_point.x, 4)
        cy = round(portal.center_point.y, 4)
        cand_x.extend([round(cx - 0.04, 4), round(cx + 0.04, 4)])
        cand_y.extend([round(cy - 0.04, 4), round(cy + 0.04, 4)])

    # 3. Extra points (if supplied, e.g. candidate hubs)
    if extra_points:
        for idx, pt in enumerate(extra_points):
            ex = round(pt.x, 4)
            ey = round(pt.y, 4)
            exact_x.add(ex)
            exact_y.add(ey)
            cand_x.extend([ex, round(ex - 0.04, 4), round(ex + 0.04, 4)])
            cand_y.extend([ey, round(ey - 0.04, 4), round(ey + 0.04, 4)])

    # 4. Room inset contour lines (0.15m inset from room bounds)
    for room in plan.rooms.values():
        minx, miny, maxx, maxy = room.geometry.bounds
        ix1 = round(minx + 0.15, 4)
        ix2 = round(maxx - 0.15, 4)
        iy1 = round(miny + 0.15, 4)
        iy2 = round(maxy - 0.15, 4)

        if ix1 < ix2:
            cand_x.extend([ix1, ix2])
        if iy1 < iy2:
            cand_y.extend([iy1, iy2])

    # Clamp candidates to inner bounding box
    inner_bounds = plan.inner_geom.bounds
    cand_x = [x for x in cand_x if 0.0 <= x <= inner_bounds[2]]
    cand_y = [y for y in cand_y if 0.0 <= y <= inner_bounds[3]]

    # 5. Deduplicate coordinates with 1mm tolerance
    X_coords = deduplicate_coords(cand_x, exact_x, tol=0.001)
    Y_coords = deduplicate_coords(cand_y, exact_y, tol=0.001)

    # 6. Generate candidate edges and filter against free space and obstacles
    free_space = plan.free_space_geom
    obstacles = plan.obstacles_geom
    inner = plan.inner_geom

    candidate_edges: List[Tuple[Point2D, Point2D, CompassDirection, float, LineString]] = []

    # Horizontal candidate edges
    for y in Y_coords:
        for i in range(len(X_coords) - 1):
            x1, x2 = X_coords[i], X_coords[i + 1]
            dx = round(x2 - x1, 4)
            if dx < 1e-4:
                continue
            p1 = Point2D(x=x1, y=y)
            p2 = Point2D(x=x2, y=y)
            seg = LineString([(x1, y), (x2, y)])
            candidate_edges.append((p1, p2, CompassDirection.EAST, dx, seg))

    # Vertical candidate edges
    for x in X_coords:
        for j in range(len(Y_coords) - 1):
            y1, y2 = Y_coords[j], Y_coords[j + 1]
            dy = round(y2 - y1, 4)
            if dy < 1e-4:
                continue
            p1 = Point2D(x=x, y=y1)
            p2 = Point2D(x=x, y=y2)
            seg = LineString([(x, y1), (x, y2)])
            candidate_edges.append((p1, p2, CompassDirection.NORTH, dy, seg))

    # Pre-build portal buffers and room geometries for fast tagging
    portal_buffers = [
        (p.portal_id, p.threshold_geom.buffer(0.08)) for p in topology.portals
    ]
    room_geoms = [
        (r_id, r.geometry.buffer(1e-4)) for r_id, r in plan.rooms.items()
    ]

    retained_edges: List[GridEdge] = []
    retained_vertices: Set[Point2D] = set()

    for p1, p2, direction, length, seg in candidate_edges:
        # Rule 5 check: must be inside inner and free space, zero obstacle intersection
        if not (inner.covers(seg) or seg.within(inner)):
            continue

        if not (free_space.covers(seg) or seg.within(free_space)):
            # If distance to obstacles is >= 0.001m and within inner, allow
            if obstacles.distance(seg) < 0.001:
                continue

        if obstacles.intersects(seg):
            inter = obstacles.intersection(seg)
            if not inter.is_empty:
                # If non-empty intersection length > 0 or distance to obstacle boundary is 0
                if inter.length > 1e-4 or obstacles.distance(seg) < 0.001:
                    continue

        # Tag edge with portal_id or room_id
        mid = Point((p1.x + p2.x) / 2.0, (p1.y + p2.y) / 2.0)
        edge_portal_id: Optional[str] = None
        edge_room_id: Optional[str] = None

        # 1. Check if edge crosses or belongs to a portal
        for p_id, p_buf in portal_buffers:
            if p_buf.intersects(seg):
                edge_portal_id = p_id
                break

        # 2. Check which room contains the midpoint
        if edge_portal_id is None:
            for r_id, r_geom in room_geoms:
                if r_geom.covers(mid):
                    edge_room_id = r_id
                    break

            if edge_room_id is None and plan.rooms:
                # Fallback to closest room within 0.02m
                closest_r_id = min(
                    plan.rooms.keys(),
                    key=lambda rid: plan.rooms[rid].geometry.distance(mid),
                )
                if plan.rooms[closest_r_id].geometry.distance(mid) < 0.02:
                    edge_room_id = closest_r_id

        grid_edge = GridEdge(
            u=p1,
            v=p2,
            direction=direction,
            length_m=length,
            room_id=edge_room_id,
            portal_id=edge_portal_id,
        )
        retained_edges.append(grid_edge)
        retained_vertices.add(p1)
        retained_vertices.add(p2)

    # Guarantee all exact terminal points are registered as grid vertices
    for n_pt in node_vertices.values():
        retained_vertices.add(n_pt)

    for p_dict in portal_vertices.values():
        for wpt in p_dict.values():
            retained_vertices.add(wpt)

    # Sort vertices deterministically: by y, then x
    sorted_vertices = sorted(retained_vertices, key=lambda p: (p.y, p.x))

    return HananRoutingGrid(
        plan_id=plan.plan_id,
        vertices=sorted_vertices,
        edges=retained_edges,
        node_vertices=node_vertices,
        portal_vertices=portal_vertices,
    )
