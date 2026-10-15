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

