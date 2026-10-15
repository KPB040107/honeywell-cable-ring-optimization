"""Immutable Pydantic v2 data contracts enforcing metric units and 0.1mm precision."""

from enum import Enum
from typing import Any, Dict, FrozenSet, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import shapely


class Point2D(BaseModel):
    """Immutable 2D point in metric coordinates (metres).

    Coordinates are rounded to 4 decimal places (0.1 mm precision)
    to ensure deterministic geometry without floating point drift.
    """

    model_config = ConfigDict(frozen=True)

    x: float = Field(description="X coordinate in metres (lower-left origin)")
    y: float = Field(description="Y coordinate in metres (lower-left origin)")

    @field_validator("x", "y", mode="before")
    @classmethod
    def round_precision_and_validate(cls, v: Any) -> float:
        val = round(float(v), 4)
        if val < -1e-4:
            raise ValueError(f"Coordinate must be non-negative in metric frame, got {val}")
        # Clean up -0.0
        if abs(val) < 1e-12:
            return 0.0
        return val

    @property
    def coords(self) -> tuple[float, float]:
        return (self.x, self.y)

    def as_shapely(self) -> shapely.Point:
        return shapely.Point(self.x, self.y)


class RoomCategory(str, Enum):
    """Room classification according to official competition rules (Slide 2.1)."""

    # Eligible rooms (receive fixed nodes and hubs)
    LIVING = "living"
    BEDROOM = "bedroom"
    KITCHEN = "kitchen"
    STORAGE = "storage"

    # Excluded rooms/spaces (no nodes and no hubs)
    BATHROOM = "bathroom"
    BALCONY = "balcony"
    STAIR = "stair"
    GARDEN = "garden"
    POOL = "pool"
    PARKING = "parking"
    LAND = "land"
    CORRIDOR = "corridor"

    @classmethod
    def eligible_categories(cls) -> FrozenSet[str]:
        return frozenset({cls.LIVING.value, cls.BEDROOM.value, cls.KITCHEN.value, cls.STORAGE.value})

    @classmethod
    def excluded_categories(cls) -> FrozenSet[str]:
        return frozenset({
            cls.BATHROOM.value,
            cls.BALCONY.value,
            cls.STAIR.value,
            cls.GARDEN.value,
            cls.POOL.value,
            cls.PARKING.value,
            cls.LAND.value,
            cls.CORRIDOR.value,
        })

    @classmethod
    def is_eligible_category(cls, category: str) -> bool:
        return category.strip().lower() in cls.eligible_categories()


class EligibleOrExcludedRoom(BaseModel):
    """Metadata and healed geometry of a single room in metres."""

    model_config = ConfigDict(frozen=True)

    room_id: str = Field(description="Unique canonical room ID (e.g. R01)")
    category: str = Field(description="Room category string")
    is_eligible: bool = Field(description="True if room is eligible for device nodes & hubs")
    area_m2: float = Field(description="Floor area of the room in square metres", ge=0.0)
    polygon_wkt: str = Field(description="Well-Known Text (WKT) representation of the room polygon")

    @field_validator("category")
    @classmethod
    def normalize_category(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("polygon_wkt")
    @classmethod
    def validate_wkt(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("WKT string must not be empty")
        try:
            geom = shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        if geom.is_empty:
            raise ValueError("Room polygon geometry cannot be empty")
        return v

    @property
    def geometry(self) -> shapely.Polygon:
        return shapely.from_wkt(self.polygon_wkt)


class FixedNode(BaseModel):
    """Immutable fixed interior node located in an eligible room."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(description="Unique fixed node ID (e.g. N1)")
    room_id: str = Field(description="Associated eligible host room ID")
    location: Point2D = Field(description="Fixed metric coordinates (>= 0.5m clearance)")


class MetricFloorPlan(BaseModel):
    """Top-level normalized metric floor plan contract produced by Phase 1 Ingestion."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Unique floor plan identifier")
    scale_units_per_m: float = Field(
        description="Calculated metric scale factor in drawing units per metre",
        gt=0.0,
    )
    total_area_m2: float = Field(description="Reported floor plan area in m^2", gt=0.0)
    inner_wkt: str = Field(description="WKT geometry of outer enclosing inner boundary")
    free_space_wkt: str = Field(
        description="WKT geometry of routable free space F = inner - (wall + window)"
    )
    obstacles_wkt: str = Field(
        description="WKT geometry of obstacle union O = wall + window + front_door"
    )
    rooms: Dict[str, EligibleOrExcludedRoom] = Field(
        default_factory=dict,
        description="Dictionary mapping room_id to room models",
    )
    nodes: Dict[str, FixedNode] = Field(
        default_factory=dict,
        description="Dictionary mapping node_id to fixed nodes",
    )

    @field_validator("inner_wkt", "free_space_wkt", "obstacles_wkt")
    @classmethod
    def validate_wkt_strings(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("WKT string must not be empty")
        try:
            shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        return v

    @property
    def inner_geom(self) -> shapely.Polygon:
        return shapely.from_wkt(self.inner_wkt)

    @property
    def free_space_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.free_space_wkt)

    @property
    def obstacles_geom(self) -> shapely.MultiPolygon:
        return shapely.from_wkt(self.obstacles_wkt)


class PortalType(str, Enum):
    """Classification of topological connections between rooms."""

    DOOR = "door"
    OPEN_PASSAGE = "open_passage"


class DoorOrPassagePortal(BaseModel):
    """Immutable portal connection (door or open passage) between two adjacent rooms.

    Provides center and zero-overlap parallel doorway lane waypoints (>= 20mm separation)
    for single-door leaf room transits and orthogonal room step points.
    """

    model_config = ConfigDict(frozen=True)

    portal_id: str = Field(description="Unique portal identifier (e.g. P01)")
    portal_type: PortalType = Field(description="Portal type: DOOR or OPEN_PASSAGE")
    room1_id: str = Field(description="First connected room ID")
    room2_id: str = Field(description="Second connected room ID")
    center_point: Point2D = Field(description="Midpoint coordinates of the threshold")
    threshold_wkt: str = Field(
        description="WKT LineString representing the physical doorway or open threshold segment"
    )
    width_m: float = Field(
        description="Width of the doorway gap in metres (> 0.0)",
        gt=0.0,
    )
    lane_a_point: Point2D = Field(
        description="Parallel lane A waypoint offset along threshold for incoming transit"
    )
    lane_b_point: Point2D = Field(
        description="Parallel lane B waypoint offset along threshold for outgoing transit"
    )
    step_room1: Point2D = Field(
        description="Orthogonal step waypoint inset into room1 for clean 90-degree turns"
    )
    step_room2: Point2D = Field(
        description="Orthogonal step waypoint inset into room2 for clean 90-degree turns"
    )
    lateral_offset_m: float = Field(
        description="Lateral offset distance between centerline and lane waypoint (>= 0.02m)",
        ge=0.02,
    )

    @field_validator("threshold_wkt")
    @classmethod
    def validate_threshold_wkt(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("Threshold WKT string must not be empty")
        try:
            geom = shapely.from_wkt(v)
        except Exception as exc:
            raise ValueError(f"Invalid Shapely WKT geometry: {exc}") from exc
        if geom.is_empty:
            raise ValueError("Threshold LineString cannot be empty")
        if not isinstance(geom, (shapely.LineString, shapely.MultiLineString)):
            raise ValueError(f"Threshold geometry must be LineString, got {geom.geom_type}")
        return v

    @property
    def threshold_geom(self) -> shapely.LineString:
        return shapely.from_wkt(self.threshold_wkt)


class PortalTopologyGraph(BaseModel):
    """Room adjacency dual graph G_macro capturing interior door and open passage connectivity."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    eligible_room_ids: list[str] = Field(
        default_factory=list,
        description="List of room IDs eligible for nodes and hubs",
    )
    portals: list[DoorOrPassagePortal] = Field(
        default_factory=list,
        description="List of portals linking interior rooms",
    )
    adjacency: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Adjacency list mapping room_id to list of neighboring room_ids",
    )
    is_fully_connected: bool = Field(
        description="True if the eligible room subgraph forms a single connected component"
    )
    leaf_room_ids: list[str] = Field(
        default_factory=list,
        description="List of room IDs having degree 1 in topology (single-door leaf rooms)",
    )


class CompassDirection(str, Enum):
    """Discrete cardinal compass direction for axis-aligned micro-routing and bend accounting."""

    NORTH = "N"
    SOUTH = "S"
    EAST = "E"
    WEST = "W"

    @classmethod
    def from_delta(cls, dx: float, dy: float, tolerance: float = 1e-4) -> "CompassDirection":
        """Determine cardinal direction from displacement delta (dx, dy).

        Raises ValueError if displacement is zero or not strictly axis-aligned.
        """
        abs_dx = abs(dx)
        abs_dy = abs(dy)
        if abs_dx < tolerance and abs_dy < tolerance:
            raise ValueError(f"Zero displacement (dx={dx}, dy={dy}) has no direction")
        if abs_dx >= tolerance and abs_dy >= tolerance:
            raise ValueError(f"Non-rectilinear displacement: dx={dx}, dy={dy}")
        if abs_dx >= tolerance:
            return cls.EAST if dx > 0 else cls.WEST
        else:
            return cls.NORTH if dy > 0 else cls.SOUTH

    def unit_vector(self) -> tuple[float, float]:
        """Return unit vector (dx, dy) for this compass direction."""
        vectors = {
            CompassDirection.NORTH: (0.0, 1.0),
            CompassDirection.SOUTH: (0.0, -1.0),
            CompassDirection.EAST: (1.0, 0.0),
            CompassDirection.WEST: (-1.0, 0.0),
        }
        return vectors[self]

    def opposite(self) -> "CompassDirection":
        """Return the opposite 180-degree compass direction."""
        opposites = {
            CompassDirection.NORTH: CompassDirection.SOUTH,
            CompassDirection.SOUTH: CompassDirection.NORTH,
            CompassDirection.EAST: CompassDirection.WEST,
            CompassDirection.WEST: CompassDirection.EAST,
        }
        return opposites[self]


class PairRouteMetric(BaseModel):
    """Optimal obstacle-aware rectilinear route metric between a pair of endpoints.

    Includes exact entry/exit directions, cable length L, bend count B,
    composite cost J = L + 0.5 * B, and the axis-aligned polyline.
    """

    model_config = ConfigDict(frozen=True)

    start_id: str = Field(description="Starting terminal identifier (node or hub candidate ID)")
    end_id: str = Field(description="Ending terminal identifier (node or hub candidate ID)")
    start_dir: Optional[CompassDirection] = Field(
        default=None,
        description="Departure direction from start terminal",
    )
    end_dir: Optional[CompassDirection] = Field(
        default=None,
        description="Arrival direction at end terminal",
    )
    length_m: float = Field(
        description="Axis-aligned cable length in metres",
        ge=0.0,
    )
    bends: int = Field(
        description="Number of 90-degree turns and 180-degree reversals",
        ge=0,
    )
    cost_J: float = Field(
        description="Official checker cost J = L + 0.5 * B",
        ge=0.0,
    )
    polyline: list[Point2D] = Field(
        default_factory=list,
        description="Ordered sequence of axis-aligned vertices from start to end",
    )

    @field_validator("polyline")
    @classmethod
    def validate_axis_aligned_polyline(cls, points: list[Point2D]) -> list[Point2D]:
        """Verify that every segment in polyline is strictly horizontal or vertical."""
        if len(points) < 2:
            return points
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            if dx > 1e-4 and dy > 1e-4:
                raise ValueError(
                    f"Segment {i} -> {i+1} ({p1.coords} -> {p2.coords}) is not axis-aligned: "
                    f"dx={dx:.5f}, dy={dy:.5f}"
                )
        return points


class RectilinearRoutingIndex(BaseModel):
    """Directional pairwise routing tensor cache and grid metadata."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    grid_node_count: int = Field(description="Number of vertices in the Hanan routing grid", ge=0)
    grid_edge_count: int = Field(description="Number of valid free-space edges in the Hanan grid", ge=0)
    pair_metrics: dict[str, PairRouteMetric] = Field(
        default_factory=dict,
        description="Lookup table keyed by '{start}_{end}_{dir_in}_{dir_out}' or '{start}_{end}'",
    )

    def get_metric(
        self,
        start_id: str,
        end_id: str,
        start_dir: Optional[CompassDirection] = None,
        end_dir: Optional[CompassDirection] = None,
    ) -> Optional[PairRouteMetric]:
        """Retrieve precomputed PairRouteMetric for given endpoint and direction pair."""
        key = f"{start_id}_{end_id}_{start_dir.value if start_dir else '*'}_{end_dir.value if end_dir else '*'}"
        if key in self.pair_metrics:
            return self.pair_metrics[key]
        fallback_key = f"{start_id}_{end_id}"
        return self.pair_metrics.get(fallback_key)


class HubAssignment(BaseModel):
    """Immutable hub assignment and contiguous zone model (Slide 3 Rule 2 & Rule 6)."""

    model_config = ConfigDict(frozen=True)

    hub_id: str = Field(description="Unique hub identifier (e.g. H1, H2)")
    room_id: str = Field(description="Host eligible room containing the hub")
    location: Point2D = Field(description="Metric coordinates satisfying >= 0.5m wall clearance")
    assigned_rooms: list[str] = Field(
        default_factory=list,
        description="Eligible rooms assigned to this hub",
    )
    assigned_nodes: list[str] = Field(
        default_factory=list,
        description="Fixed nodes served by this hub (<= 5)",
    )
    zone_area_m2: float = Field(
        description="Total eligible area of assigned rooms (<= 100.0 m^2)",
        ge=0.0,
    )
    estimated_ring_cost_J: float = Field(
        default=0.0,
        description="Pre-routing estimated closed-ring cost",
        ge=0.0,
    )

    @field_validator("zone_area_m2")
    @classmethod
    def validate_zone_area(cls, v: float) -> float:
        if v > 100.001:
            raise ValueError(f"Zone area {v:.4f} m^2 exceeds maximum capacity limit of 100.0 m^2")
        return v

    @field_validator("assigned_nodes")
    @classmethod
    def validate_assigned_nodes(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            raise ValueError(f"Zone assigned nodes count ({len(v)}) exceeds maximum capacity of 5 nodes")
        return v

    @model_validator(mode="after")
    def validate_host_room_membership(self) -> "HubAssignment":
        if self.assigned_rooms and self.room_id not in self.assigned_rooms:
            raise ValueError(
                f"Host room '{self.room_id}' must be in assigned_rooms {self.assigned_rooms}"
            )
        return self


class HubZonePartition(BaseModel):
    """Immutable full floor plan hub assignment and contiguous zone partition contract."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    hub_count: int = Field(description="Total selected hubs H", ge=1)
    lower_bound_hubs: int = Field(
        description="Theoretical lower bound H_min = max(ceil(area/100), ceil(nodes/5))",
        ge=1,
    )
    area_gap_m2: float = Field(
        default=0.0,
        description="Area gap between maximum and minimum zone area: max_k A_k - min_k A_k",
        ge=0.0,
    )
    hubs: list[HubAssignment] = Field(
        default_factory=list,
        description="List of hub assignments for each zone",
    )

    @model_validator(mode="after")
    def validate_partition(self) -> "HubZonePartition":
        if self.hubs and len(self.hubs) != self.hub_count:
            raise ValueError(
                f"hub_count ({self.hub_count}) does not match number of hubs ({len(self.hubs)})"
            )
        if self.hub_count < self.lower_bound_hubs:
            raise ValueError(
                f"hub_count ({self.hub_count}) cannot be less than lower bound ({self.lower_bound_hubs})"
            )
        return self


class RouteSegment(BaseModel):
    """Single directed leg of a closed ring connecting two consecutive terminals."""

    model_config = ConfigDict(frozen=True)

    from_node: str = Field(description="Starting terminal ID (e.g. H1 or N1)")
    to_node: str = Field(description="Ending terminal ID")
    length_m: float = Field(description="Cable length in metres", ge=0.0)
    polyline: list[Point2D] = Field(
        default_factory=list,
        description="Ordered sequence of axis-aligned vertices",
    )

    @field_validator("polyline")
    @classmethod
    def validate_axis_aligned_polyline(cls, points: list[Point2D]) -> list[Point2D]:
        """Verify that every segment in polyline is strictly horizontal or vertical."""
        if len(points) < 2:
            return points
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            dx = abs(p2.x - p1.x)
            dy = abs(p2.y - p1.y)
            if dx > 1e-4 and dy > 1e-4:
                raise ValueError(
                    f"Segment {i} -> {i+1} ({p1.coords} -> {p2.coords}) is not axis-aligned: "
                    f"dx={dx:.5f}, dy={dy:.5f}"
                )
        return points


class CompletedHubRing(BaseModel):
    """Completed, direction-aware closed ring route for a single hub."""

    model_config = ConfigDict(frozen=True)

    hub_id: str = Field(description="Unique hub identifier (e.g. H1)")
    room_id: str = Field(description="Host eligible room containing the hub")
    location: Point2D = Field(description="Fixed metric coordinates of the hub")
    assigned_rooms: list[str] = Field(
        default_factory=list,
        description="Eligible rooms assigned to this hub",
    )
    assigned_nodes: list[str] = Field(
        default_factory=list,
        description="Fixed nodes served by this hub",
    )
    closed_sequence: list[str] = Field(
        description="Ordered sequence of terminals starting and ending with hub_id",
    )
    route_segments: list[RouteSegment] = Field(
        default_factory=list,
        description="Synthesized route segments connecting sequence terminals",
    )
    ring_length_m: float = Field(
        description="Total cable length in metres",
        ge=0.0,
    )
    ring_bends: int = Field(
        description="Total bend count including node and hub turns",
        ge=0,
    )
    ring_crossings: int = Field(
        default=0,
        description="Number of crossings X",
        ge=0,
    )
    ring_overlap_m: float = Field(
        default=0.0,
        description="Total overlap length O in metres",
        ge=0.0,
    )
    cost_J: float = Field(
        description="Composite cost J = L + 5X + O + 0.5B",
        ge=0.0,
    )

    @model_validator(mode="after")
    def validate_closed_sequence(self) -> "CompletedHubRing":
        if not self.closed_sequence:
            raise ValueError("closed_sequence cannot be empty")
        if self.closed_sequence[0] != self.hub_id:
            raise ValueError(
                f"closed_sequence must start with hub_id '{self.hub_id}', got '{self.closed_sequence[0]}'"
            )
        if self.closed_sequence[-1] != self.hub_id:
            raise ValueError(
                f"closed_sequence must end with hub_id '{self.hub_id}', got '{self.closed_sequence[-1]}'"
            )
        intermediate = self.closed_sequence[1:-1]
        if self.assigned_nodes:
            if set(intermediate) != set(self.assigned_nodes) or len(intermediate) != len(self.assigned_nodes):
                raise ValueError(
                    f"closed_sequence intermediate nodes {intermediate} must visit each assigned node {self.assigned_nodes} exactly once"
                )
        return self


class SynthesizedRingsBundle(BaseModel):
    """Complete floor-plan bundle of synthesized closed rings across all hubs."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    hub_rings: list[CompletedHubRing] = Field(
        default_factory=list,
        description="List of completed closed rings for each hub",
    )
    total_length_m: float = Field(
        description="Total cable length across all rings in metres",
        ge=0.0,
    )
    total_bends: int = Field(
        description="Total bends across all rings",
        ge=0,
    )
    total_crossings: int = Field(
        default=0,
        description="Total crossings across all rings",
        ge=0,
    )
    total_overlap_m: float = Field(
        default=0.0,
        description="Total overlap length across all rings in metres",
        ge=0.0,
    )
    total_cost_J: float = Field(
        description="Total composite cost J = L + 5X + O + 0.5B",
        ge=0.0,
    )


class RuleViolation(BaseModel):
    """Diagnostic detail of a single hard rule violation."""

    model_config = ConfigDict(frozen=True)

    rule_number: int = Field(description="Hard rule number violated (1 to 6)", ge=1, le=6)
    rule_name: str = Field(description="Canonical hard rule name")
    hub_id: Optional[str] = Field(default=None, description="Hub ID associated with violation, if applicable")
    room_id: Optional[str] = Field(default=None, description="Room ID associated with violation, if applicable")
    location: Optional[Point2D] = Field(default=None, description="Spatial coordinates of violation, if applicable")
    message: str = Field(description="Human-readable violation description")


class EvaluatedHubMetrics(BaseModel):
    """Recomputed evaluation metrics for an individual hub ring."""

    model_config = ConfigDict(frozen=True)

    hub_id: str = Field(description="Unique hub identifier")
    room_id: str = Field(description="Assigned eligible room hosting the hub")
    location: Point2D = Field(description="Metric coordinates of the hub")
    assigned_rooms: list[str] = Field(default_factory=list, description="Rooms served by this hub")
    assigned_nodes: list[str] = Field(default_factory=list, description="Fixed nodes served by this hub")
    closed_sequence: list[str] = Field(default_factory=list, description="Ordered ring sequence")
    length_m: float = Field(description="Recomputed ring cable length in metres", ge=0.0)
    crossings: int = Field(default=0, description="Recomputed crossings within this ring", ge=0)
    overlap_m: float = Field(default=0.0, description="Recomputed overlap length in metres", ge=0.0)
    bends: int = Field(default=0, description="Recomputed total bend count", ge=0)
    cost_J: float = Field(description="Recomputed composite cost J = L + 5X + O + 0.5B", ge=0.0)
    zone_area_m2: float = Field(default=0.0, description="Total floor area of assigned rooms", ge=0.0)


class EvaluationReport(BaseModel):
    """Official Slide 3, 6 & 7 Independent Evaluation Checker Report."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    is_valid: bool = Field(description="True iff 0 rule violations")
    violations: list[RuleViolation] = Field(default_factory=list, description="List of rule violations")
    hub_count: int = Field(description="Total number of hubs", ge=0)
    hub_metrics: list[EvaluatedHubMetrics] = Field(default_factory=list, description="Per-hub evaluated metrics")
    total_length_m: float = Field(description="Total cable length L in metres across all rings", ge=0.0)
    total_crossings: int = Field(description="Total crossings X across all rings", ge=0)
    total_overlap_m: float = Field(description="Total overlap length O in metres across all rings", ge=0.0)
    total_bends: int = Field(description="Total bends B across all rings", ge=0)
    total_cost_J: float = Field(description="Total composite cost J = L + 5X + O + 0.5B", ge=0.0)
    area_gap_m2: float = Field(description="Assigned room area gap delta_A_gap = max(A) - min(A)", ge=0.0)
    rules_passed: dict[str, bool] = Field(
        default_factory=dict,
        description="Dictionary mapping Rule 1..Rule 6 to pass/fail boolean status",
    )


class ProposalType(str, Enum):
    """Classification of architectural perturbation proposals."""

    ADD_GHOST_DOOR = "add_ghost_door"
    SLIDE_DOOR = "slide_door"


class StructuralProposal(BaseModel):
    """Candidate architectural wall perturbation proposal evaluated by the Co-Pilot engine."""

    model_config = ConfigDict(frozen=True)

    proposal_id: str = Field(description="Unique proposal identifier")
    proposal_type: ProposalType = Field(description="Proposal classification type")
    room_a_id: str = Field(description="First adjacent room connected across the wall")
    room_b_id: str = Field(description="Second adjacent room connected across the wall")
    wall_segment: list[Point2D] = Field(
        description="Endpoints [P1, P2] of the shared interior wall segment in metres"
    )
    suggested_door_center: Point2D = Field(
        description="Suggested metric center coordinates of the doorway opening"
    )
    door_width_m: float = Field(
        default=0.90,
        description="Suggested doorway or open-passage opening width in metres",
        gt=0.0,
    )
    delta_hubs: int = Field(
        default=0,
        description="Hub count reduction Delta H = H_base - H_new",
    )
    delta_cost_J: float = Field(
        default=0.0,
        description="Composite routing cost reduction Delta J = J_base - J_new",
    )
    delta_length_m: float = Field(
        default=0.0,
        description="Cable length reduction in metres Delta L = L_base - L_new",
    )
    delta_bends: int = Field(
        default=0,
        description="Bend count reduction Delta B = B_base - B_new",
    )
    original_cost_J: float = Field(
        default=0.0,
        description="Baseline total composite cost J_base",
        ge=0.0,
    )
    new_cost_J: float = Field(
        default=0.0,
        description="Predicted total composite cost J_new under this proposal",
        ge=0.0,
    )
    rationale: str = Field(
        default="",
        description="Architectural rationale detailing cable shortcut, bend elimination, or hub merger",
    )


class CoPilotReport(BaseModel):
    """Ranked report of architectural structural recommendations generated by the Co-Pilot engine."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    baseline_hubs: int = Field(description="Baseline hub count H_base", ge=0)
    baseline_cost_J: float = Field(description="Baseline composite routing cost J_base", ge=0.0)
    proposals_evaluated: int = Field(
        default=0,
        description="Total candidate structural perturbations explored",
        ge=0,
    )
    recommended_proposals: list[StructuralProposal] = Field(
        default_factory=list,
        description="Lexicographically ranked list of top recommended structural proposals",
    )


class PowerSource(str, Enum):
    """Active electrical branch energizing a device node in Class A topology."""

    PRIMARY_OUTBOUND = "primary_outbound"
    RETURN_TERMINAL = "return_terminal"
    OFFLINE = "offline"


class WireCutLocation(BaseModel):
    """Spatial and topological location of an open-circuit wire cut on a cable leg."""

    model_config = ConfigDict(frozen=True)

    hub_id: str = Field(description="Hub identifier for the ring")
    leg_index: int = Field(description="0-indexed leg in closed_sequence where wire is cut", ge=0)
    from_node: str = Field(description="Starting terminal ID of the severed leg")
    to_node: str = Field(description="Ending terminal ID of the severed leg")
    cut_point: Point2D = Field(description="2D physical coordinate of the cut")
    distance_along_leg_m: float = Field(
        default=0.0,
        description="Metric distance of cut from from_node along leg",
        ge=0.0,
    )


class NodeSurvivabilityStatus(BaseModel):
    """Operational status and branch connectivity for an individual device node."""

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(description="Unique device node ID")
    is_online: bool = Field(description="True if node is electrically reachable from at least one hub terminal")
    power_source: PowerSource = Field(description="Power branch energizing this node")
    path_length_m: float = Field(
        default=0.0,
        description="Metric cable distance from hub to node along energized branch",
        ge=0.0,
    )
    hop_count: int = Field(
        default=0,
        description="Number of cable hops from hub terminal to node",
        ge=0,
    )


class ClassASimulationReport(BaseModel):
    """Comprehensive NFPA 72 Class A wire-cut survivability simulation report."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(default="", description="Floor plan identifier")
    hub_id: str = Field(description="Hub identifier")
    is_severed: bool = Field(default=False, description="True if one or more cable legs are severed")
    cut_location: Optional[WireCutLocation] = Field(
        default=None,
        description="Primary or single wire cut location if severed",
    )
    cut_locations: list[WireCutLocation] = Field(
        default_factory=list,
        description="All cut locations in simulation",
    )
    total_nodes: int = Field(description="Total number of device nodes in ring", ge=0)
    online_nodes: int = Field(description="Number of operational device nodes remaining online", ge=0)
    survivability_pct: float = Field(
        description="Percentage of nodes surviving (0.0 to 100.0)",
        ge=0.0,
        le=100.0,
    )
    primary_branch_nodes: list[str] = Field(
        default_factory=list,
        description="Ordered node IDs powered via Primary Outbound",
    )
    return_branch_nodes: list[str] = Field(
        default_factory=list,
        description="Ordered node IDs powered via Return Terminal",
    )
    offline_nodes: list[str] = Field(
        default_factory=list,
        description="Node IDs disconnected from both terminals",
    )
    node_statuses: dict[str, NodeSurvivabilityStatus] = Field(
        default_factory=dict,
        description="Detailed per-node survivability status",
    )
    nfpa72_class_a_compliant: bool = Field(
        description="True iff 100% of nodes remain online under any single cut",
    )
    max_latency_path_m: float = Field(
        default=0.0,
        description="Longest cable path length in metres from hub to any operational node",
        ge=0.0,
    )


class BIMElementType(str, Enum):
    """Classification of 3D Building Information Modeling architectural and electrical elements."""

    SLAB = "slab"
    WALL = "wall"
    WINDOW = "window"
    DOOR_LINTEL = "door_lintel"
    NODE = "node"
    HUB = "hub"
    CONDUIT = "conduit"


class BIMElement3D(BaseModel):
    """Immutable 3D geometric mesh representation of an architectural or electrical element."""

    model_config = ConfigDict(frozen=True)

    element_id: str = Field(description="Unique BIM element ID")
    element_type: BIMElementType = Field(description="Type classification of BIM element")
    room_id: Optional[str] = Field(default=None, description="Associated room ID if applicable")
    hub_id: Optional[str] = Field(default=None, description="Associated hub ID if applicable")
    vertices_3d: list[list[float]] = Field(
        description="List of 3D vertices [x, y, z] in metric coordinates (metres)"
    )
    faces: list[list[int]] = Field(
        description="List of face vertex indices (0-indexed polygons/triangles)"
    )
    color_hex: str = Field(description="Primary display hex color (e.g. #2B2B2B)")
    opacity: float = Field(default=1.0, ge=0.0, le=1.0, description="Surface material opacity")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary element metadata (area, dimensions, labels)"
    )


class BIM3DScene(BaseModel):
    """Top-level 3D Building Information Modeling (BIM) architectural scene contract."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(description="Floor plan identifier")
    units: str = Field(default="m", description="Metric coordinate units")
    ceiling_height_m: float = Field(default=2.70, description="Standard ceiling height in metres", gt=0.0)
    conduit_elevation_m: float = Field(
        default=2.55, description="Ceiling conduit center elevation in metres", gt=0.0
    )
    elements: list[BIMElement3D] = Field(
        default_factory=list, description="Collection of 3D architectural and electrical elements"
    )
    metrics_hud: dict[str, Any] = Field(
        default_factory=dict, description="Live reviewer metrics and formula breakdown"
    )

