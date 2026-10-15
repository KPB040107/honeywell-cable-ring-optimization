import math
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, field_validator


class Point2D(BaseModel):
    x: float = Field(..., description="X coordinate in meters")
    y: float = Field(..., description="Y coordinate in meters")

    model_config = {"frozen": True}

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    def snapped(self, grid_size: float = 0.05) -> "Point2D":
        snapped_x = round(round(self.x / grid_size) * grid_size, 4)
        snapped_y = round(round(self.y / grid_size) * grid_size, 4)
        return Point2D(x=snapped_x, y=snapped_y)

    def manhattan_distance(self, other: "Point2D") -> float:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def euclidean_distance(self, other: "Point2D") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


class RoomEntity(BaseModel):
    room_id: str = Field(..., description="Unique room identifier, e.g. living_0")
    category: str = Field(..., description="Semantic room category, e.g. living, bedroom, bathroom")
    polygon_wkt: str = Field(..., description="Well-Known Text geometry of the room polygon")
    area_m2: float = Field(..., ge=0.0, description="Metric floor area in square meters")
    is_eligible: bool = Field(..., description="True if room belongs to eligible categories")
    is_transit: bool = Field(default=False, description="True if non-eligible room permits transit (e.g. corridor)")

    model_config = {"frozen": True}


class DoorEntity(BaseModel):
    door_id: str = Field(..., description="Unique door identifier, e.g. door_0")
    polygon_wkt: str = Field(..., description="Well-Known Text geometry of the doorway boundary")
    is_front_door: bool = Field(default=False, description="True if exterior/entrance door")

    model_config = {"frozen": True}


class DeviceNode(BaseModel):
    node_id: str = Field(..., description="Unique device identifier, e.g. N1")
    host_room_id: str = Field(..., description="Identifier of the room hosting this device")
    coord: Point2D = Field(..., description="Exact (x, y) coordinates of the device")

    model_config = {"frozen": True}


class FloorPlanModel(BaseModel):
    plan_id: str = Field(..., description="Floor plan identifier")
    scale_factor: float = Field(default=1.0, gt=0.0, description="Metric scale multiplier applied to raw geometry")
    inner_polygon_wkt: str = Field(..., description="WKT of building interior boundary")
    obstacle_union_wkt: str = Field(..., description="WKT of unified impassable obstacles (walls, windows, etc.)")
    rooms: List[RoomEntity] = Field(default_factory=list, description="Indexed rooms in the floor plan")
    doors: List[DoorEntity] = Field(default_factory=list, description="Extracted door polygons")
    nodes: List[DeviceNode] = Field(default_factory=list, description="Deterministic device nodes placed in eligible rooms")

    model_config = {"frozen": True}


class PortalEdge(BaseModel):
    portal_id: str = Field(..., description="Unique portal edge identifier, e.g. portal_0")
    portal_type: str = Field(..., description="'door' or 'open_passage'")
    room_a_id: str = Field(..., description="First connected room ID")
    room_b_id: str = Field(..., description="Second connected room ID")
    width_m: float = Field(..., ge=0.0, description="Passage opening width in meters")
    center_point: Point2D = Field(..., description="Threshold midpoint waypoint")
    lane_a_point: Point2D = Field(..., description="Lane A offset waypoint")
    lane_b_point: Point2D = Field(..., description="Lane B offset waypoint")
    room_a_clearance_a: Point2D = Field(..., description="Clearance waypoint inside room A for Lane A")
    room_a_clearance_b: Point2D = Field(..., description="Clearance waypoint inside room A for Lane B")
    room_b_clearance_a: Point2D = Field(..., description="Clearance waypoint inside room B for Lane A")
    room_b_clearance_b: Point2D = Field(..., description="Clearance waypoint inside room B for Lane B")

    model_config = {"frozen": True}


class MacroTopologyModel(BaseModel):
    room_ids: List[str] = Field(..., description="All room IDs registered in the macro graph")
    portals: List[PortalEdge] = Field(default_factory=list, description="Valid passageway connections between rooms")
    adjacency_list: Dict[str, List[str]] = Field(default_factory=dict, description="Adjacency list mapping room_id to adjacent room_ids")
    connected_components: List[List[str]] = Field(default_factory=list, description="Door-connected components of rooms")

    model_config = {"frozen": True}


class MicroRoutingIndex(BaseModel):
    waypoints: Dict[str, Point2D] = Field(..., description="Registered waypoint coordinates by ID")
    distance_matrix: Dict[str, float] = Field(..., description="Pairwise shortest rectilinear path distance keyed by 'u|v'")
    bend_matrix: Dict[str, int] = Field(..., description="Pairwise 90-degree bend count keyed by 'u|v'")
    path_cache: Dict[str, List[Point2D]] = Field(..., description="Pairwise rectilinear polylines keyed by 'u|v'")

    model_config = {"frozen": True}


class ZoneAssignment(BaseModel):
    hub_id: str = Field(..., description="Hub identifier, e.g. H1")
    hub_coord: Point2D = Field(..., description="Selected physical coordinates of hub H_k")
    host_room_id: str = Field(..., description="Room hosting hub H_k")
    room_ids: List[str] = Field(..., description="List of room IDs assigned to this zone")
    node_ids: List[str] = Field(..., description="List of device node IDs assigned to this zone")
    total_area_m2: float = Field(..., ge=0.0, description="Total eligible area served by this hub in m2")

    model_config = {"frozen": True}


class ZonePartitionResult(BaseModel):
    hub_count: int = Field(..., ge=1, description="Number of hubs deployed (K)")
    lower_bound_hubs: int = Field(..., ge=1, description="Theoretical lower bound K_min")
    zones: List[ZoneAssignment] = Field(default_factory=list, description="Partitioned contiguous zones")

    model_config = {"frozen": True}


class RouteSegment(BaseModel):
    step_index: int = Field(..., ge=1, description="Sequential step number along loop")
    from_id: str = Field(..., description="Source node/hub ID")
    to_id: str = Field(..., description="Destination node/hub ID")
    waypoints: List[Point2D] = Field(..., description="Axis-aligned rectilinear waypoint polyline")
    length_m: float = Field(..., ge=0.0, description="Segment physical length in meters")
    bends: int = Field(..., ge=0, description="Number of 90-degree orthogonal bends on segment")

    model_config = {"frozen": True}

    @field_validator("waypoints")
    @classmethod
    def validate_axis_alignment(cls, waypoints: List[Point2D]) -> List[Point2D]:
        for i in range(len(waypoints) - 1):
            p1 = waypoints[i]
            p2 = waypoints[i + 1]
            dx = abs(p1.x - p2.x)
            dy = abs(p1.y - p2.y)
            if dx > 1e-6 and dy > 1e-6:
                raise ValueError(f"Diagonal segment detected between {p1} and {p2}: dx={dx}, dy={dy}")
        return waypoints


class ClosedRingRoute(BaseModel):
    hub_id: str = Field(..., description="Originating hub ID")
    hub_coord: Point2D = Field(..., description="Originating hub coordinate")
    ordered_node_ids: List[str] = Field(..., description="Sequence of device nodes visited")
    ring_sequence: List[str] = Field(..., description="Complete loop IDs, e.g. [H1, N1, N2, H1]")
    segments: List[RouteSegment] = Field(..., description="Sequential physical route segments")
    total_length_m: float = Field(..., ge=0.0, description="Total rectilinear cable run in meters")
    total_bends: int = Field(..., ge=0, description="Total 90-degree turns along loop")
    crossings_count: int = Field(default=0, ge=0, description="Point self-intersections")
    overlap_length_m: float = Field(default=0.0, ge=0.0, description="Collinear self-overlap length in meters")

    model_config = {"frozen": True}


class RingRoutingResult(BaseModel):
    rings: List[ClosedRingRoute] = Field(..., description="Synthesized closed loops for all hubs")
    total_length_m: float = Field(..., ge=0.0, description="System-wide cable length")
    total_bends: int = Field(..., ge=0, description="System-wide 90-degree bend count")
    total_crossings: int = Field(default=0, ge=0, description="Total point intersections")
    total_overlap_m: float = Field(default=0.0, ge=0.0, description="Total collinear segment overlap")
    total_cost_j: float = Field(..., ge=0.0, description="Installation cost objective J")

    model_config = {"frozen": True}


class ValidatedSolutionBundle(BaseModel):
    is_valid: bool = Field(..., description="True if all 8 hard constraints pass")
    rule_checks: Dict[str, bool] = Field(..., description="Pass/fail status per rule")
    violations: List[str] = Field(default_factory=list, description="Detailed list of rule violations if any")
    scoring_json: Dict[str, Any] = Field(..., description="Deterministic machine-gradable scoring payload")
    overlay_image_path: Optional[str] = Field(default=None, description="Path to rendered PNG overlay")
    overlay_svg_path: Optional[str] = Field(default=None, description="Path to rendered SVG overlay")

    model_config = {"frozen": True}
