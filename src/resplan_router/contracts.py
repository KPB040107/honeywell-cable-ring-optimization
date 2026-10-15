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
