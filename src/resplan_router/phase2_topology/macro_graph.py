"""Macro-topology room-portal dual graph construction and parallel lane validation.

Constructs the room-portal dual graph G_macro in NetworkX, verifies connected component
topology among eligible rooms, identifies degree-1 single-door leaf rooms, and validates
that parallel doorway lanes maintain lateral separation >= 20mm inside free space.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import networkx as nx
import shapely
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from resplan_router.contracts import (
    DoorOrPassagePortal,
    MetricFloorPlan,
    Point2D,
    PortalTopologyGraph,
    PortalType,
)


class TopologyError(Exception):
    """Raised when floor plan topology violates connectivity or validity constraints."""
    pass


class _BuildTopologyDispatcher:
    """Descriptor enabling build_topology to work as both an instance method and a classmethod."""

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            def class_build_topology(
                plan: MetricFloorPlan,
                portals: Sequence[DoorOrPassagePortal],
                require_connected: bool = False,
            ) -> PortalTopologyGraph:
                inst = owner()
                return inst._build(plan, portals, require_connected=require_connected)

            return class_build_topology
        else:
            def instance_build_topology(
                plan: MetricFloorPlan,
                portals: Sequence[DoorOrPassagePortal],
                require_connected: bool = False,
            ) -> PortalTopologyGraph:
                return instance._build(plan, portals, require_connected=require_connected)

            return instance_build_topology


class MacroTopologyGraph:
    """NetworkX dual graph G_macro capturing room-portal connectivity and doorway lanes.

    Nodes represent interior rooms (with eligibility, category, and area attributes).
    Edges represent physical doorway or open-passage portals connecting adjacent rooms.
    """

    def __init__(
        self,
        plan: Optional[MetricFloorPlan] = None,
        portals: Optional[Sequence[DoorOrPassagePortal]] = None,
        require_connected: bool = False,
    ) -> None:
        """Initialize MacroTopologyGraph.

        If plan and portals are provided, immediately constructs the topology graph.

        Args:
            plan: Optional MetricFloorPlan contract.
            portals: Optional sequence of DoorOrPassagePortal objects.
            require_connected: If True, raises TopologyError if eligible rooms are disconnected.
        """
        self.graph: nx.Graph = nx.Graph()
        self.plan: Optional[MetricFloorPlan] = plan
        self.portals: List[DoorOrPassagePortal] = list(portals) if portals else []
        self.topology: Optional[PortalTopologyGraph] = None

        if plan is not None and portals is not None:
            self._build(plan, portals, require_connected=require_connected)

    def _build(
        self,
        plan: MetricFloorPlan,
        portals: Sequence[DoorOrPassagePortal],
        require_connected: bool = False,
    ) -> PortalTopologyGraph:
        """Build and populate the room-portal dual graph and return PortalTopologyGraph.

        Args:
            plan: Normalized metric floor plan contract.
            portals: Sequence of extracted DoorOrPassagePortal objects.
            require_connected: If True, raises TopologyError if eligible rooms do not form
                a single connected component.

        Returns:
            PortalTopologyGraph: Validated topology contract containing adjacency,
                connected status, eligible rooms, and leaf room IDs.

        Raises:
            TopologyError: If `require_connected` is True and the eligible room subgraph
                is disconnected into multiple components.
        """
        self.plan = plan
        self.portals = list(portals)
        self.graph = nx.Graph()

        # 1. Add all rooms as graph nodes
        for r_id, room in plan.rooms.items():
            self.graph.add_node(
                r_id,
                category=room.category,
                is_eligible=room.is_eligible,
                area_m2=room.area_m2,
                polygon_wkt=room.polygon_wkt,
            )

        # 2. Add portals as edges linking room pairs
        for portal in self.portals:
            # Ensure endpoints exist as nodes
            if portal.room1_id not in self.graph:
                self.graph.add_node(portal.room1_id, is_eligible=False)
            if portal.room2_id not in self.graph:
                self.graph.add_node(portal.room2_id, is_eligible=False)

            self.graph.add_edge(
                portal.room1_id,
                portal.room2_id,
                portal=portal,
                portal_id=portal.portal_id,
                portal_type=portal.portal_type,
                width_m=portal.width_m,
            )

        # 3. Determine eligible room subgraph and connectivity
        eligible_room_ids = sorted([r_id for r_id, r in plan.rooms.items() if r.is_eligible])
        eligible_subgraph = self.graph.subgraph(eligible_room_ids)

        if not eligible_room_ids:
            is_fully_connected = True
            components = []
        else:
            components = list(nx.connected_components(eligible_subgraph))
            is_fully_connected = len(components) <= 1

        if require_connected and not is_fully_connected:
            raise TopologyError(
                f"Eligible room subgraph for plan '{plan.plan_id}' is disconnected into "
                f"{len(components)} components: {components}"
            )

        # 4. Identify leaf rooms (degree == 1 in eligible subgraph)
        leaf_room_ids = sorted(
            [node for node in eligible_room_ids if eligible_subgraph.degree(node) == 1]
        )

        # 5. Build full adjacency dictionary
        adjacency: Dict[str, List[str]] = {}
        for r_id in sorted(plan.rooms.keys()):
            if r_id in self.graph:
                adjacency[r_id] = sorted(list(self.graph.neighbors(r_id)))
            else:
                adjacency[r_id] = []

        topology = PortalTopologyGraph(
            plan_id=plan.plan_id,
            eligible_room_ids=eligible_room_ids,
            portals=self.portals,
            adjacency=adjacency,
            is_fully_connected=is_fully_connected,
            leaf_room_ids=leaf_room_ids,
        )
        self.topology = topology
        return topology

    build_topology = _BuildTopologyDispatcher()

    @staticmethod
    def get_portal_lanes(
        portal: DoorOrPassagePortal,
    ) -> Tuple[Point2D, Point2D, Point2D]:
        """Extract centerline and parallel doorway lane waypoints from a portal.

        Args:
            portal: DoorOrPassagePortal object.

        Returns:
            Tuple[Point2D, Point2D, Point2D]: (center_point, lane_a_point, lane_b_point).
        """
        return (portal.center_point, portal.lane_a_point, portal.lane_b_point)

    @staticmethod
    def validate_parallel_lane_clearance(
        portal: DoorOrPassagePortal,
        free_space: Union[BaseGeometry, MetricFloorPlan, str],
        min_separation_m: float = 0.020,
    ) -> bool:
        """Validate that parallel doorway lanes maintain lateral separation and lie inside free space.

        Verifies that:
        1. Euclidean distance between lane_a and lane_b is >= min_separation_m (default 0.020m = 20mm).
        2. Both lane_a and lane_b points lie strictly inside routable free space.

        Args:
            portal: DoorOrPassagePortal to validate.
            free_space: Shapely geometry, MetricFloorPlan, or WKT string of free space.
            min_separation_m: Minimum allowed lateral distance in metres (default 0.020m).

        Returns:
            bool: True if all invariants pass.

        Raises:
            ValueError: If separation is below tolerance or lane points fall outside free space.
        """
        if hasattr(free_space, "free_space_geom"):
            fs_geom: BaseGeometry = free_space.free_space_geom  # type: ignore
        elif isinstance(free_space, str):
            fs_geom = shapely.from_wkt(free_space)
        elif isinstance(free_space, BaseGeometry):
            fs_geom = free_space
        else:
            raise TypeError(f"Unsupported free_space type: {type(free_space)}")

        p_a = Point(portal.lane_a_point.x, portal.lane_a_point.y)
        p_b = Point(portal.lane_b_point.x, portal.lane_b_point.y)

        # 1. Lateral separation check
        dx = portal.lane_b_point.x - portal.lane_a_point.x
        dy = portal.lane_b_point.y - portal.lane_a_point.y
        sep = math.hypot(dx, dy)

        if sep < min_separation_m - 1e-6:
            raise ValueError(
                f"Portal '{portal.portal_id}' lane separation {sep:.4f}m is below "
                f"minimum required {min_separation_m:.4f}m (20mm)"
            )

        # 2. Free space containment check (within 1e-4m numerical boundary tolerance)
        dist_a = fs_geom.distance(p_a)
        if dist_a > 1e-4:
            raise ValueError(
                f"Portal '{portal.portal_id}' lane_a point ({portal.lane_a_point.x}, {portal.lane_a_point.y}) "
                f"lies outside free space (distance {dist_a:.6f}m > 1e-4m)"
            )

        dist_b = fs_geom.distance(p_b)
        if dist_b > 1e-4:
            raise ValueError(
                f"Portal '{portal.portal_id}' lane_b point ({portal.lane_b_point.x}, {portal.lane_b_point.y}) "
                f"lies outside free space (distance {dist_b:.6f}m > 1e-4m)"
            )

        return True

    def get_edge_portal(self, room_a: str, room_b: str) -> Optional[DoorOrPassagePortal]:
        """Return the DoorOrPassagePortal connecting two rooms, if one exists."""
        if self.graph.has_edge(room_a, room_b):
            return self.graph[room_a][room_b].get("portal")
        return None

    def get_neighbors(self, room_id: str) -> List[str]:
        """Return list of neighboring room IDs connected by portals."""
        if room_id in self.graph:
            return sorted(list(self.graph.neighbors(room_id)))
        return []

    def is_leaf_room(self, room_id: str) -> bool:
        """Check if a room is a single-door leaf room in the eligible topology."""
        if self.topology is not None:
            return room_id in self.topology.leaf_room_ids
        return False
