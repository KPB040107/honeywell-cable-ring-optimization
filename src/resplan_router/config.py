"""System configuration and tolerances for ResPlan Cable Ring Router."""

from pathlib import Path
from typing import Any, Union
import yaml
from pydantic import BaseModel, ConfigDict, Field


class SystemConfig(BaseModel):
    """Central configuration for ResPlan cable routing pipeline.

    Enforces official problem rules (Slide 3), checker tolerance (Slide 6 & 7),
    and scoring function weights (J = L + 5X + O + 0.5B).
    """

    model_config = ConfigDict(frozen=True)

    # Capacity constraints
    max_area_m2: float = Field(
        default=100.0,
        description="Maximum eligible room area served per hub (Slide 3 Rule 2)",
        ge=0.0,
    )
    max_nodes_per_hub: int = Field(
        default=5,
        description="Maximum fixed nodes served per hub (Slide 3 Rule 2)",
        ge=1,
    )

    # Geometry and clearance thresholds
    wall_clearance_m: float = Field(
        default=0.5,
        description="Required minimum wall/window clearance for nodes and hubs (Slide 3 Rule 6)",
        ge=0.0,
    )
    checker_tolerance_m: float = Field(
        default=0.001,
        description="Official geometric evaluation tolerance: 1 mm = 0.001 m (Slide 6 & 7)",
        ge=0.0,
    )
    min_room_area_m2: float = Field(
        default=2.0,
        description="Minimum room area threshold to filter degenerate polygon slivers",
        ge=0.0,
    )
    doorway_lane_offset_m: float = Field(
        default=0.02,
        description="Parallel doorway lane track offset (20 mm >= 1 mm checker tolerance for O=0)",
        ge=0.005,
    )

    # Cost function weights: J = L + 5X + O + 0.5B
    weight_length: float = Field(
        default=1.0,
        description="Weight for cable length L in cost function J (Slide 3 & 7)",
        ge=0.0,
    )
    weight_crossing: float = Field(
        default=5.0,
        description="Weight for cable crossings X in cost function J (Slide 3 & 7)",
        ge=0.0,
    )
    weight_overlap: float = Field(
        default=1.0,
        description="Weight for cable overlap O in cost function J (Slide 3 & 7)",
        ge=0.0,
    )
    weight_bend: float = Field(
        default=0.5,
        description="Weight for 90-degree and 180-degree bends B in cost function J (Slide 3 & 7)",
        ge=0.0,
    )

    @classmethod
    def from_yaml(cls, yaml_path: Union[str, Path]) -> "SystemConfig":
        """Load configuration from a YAML file."""
        path = Path(yaml_path)
        if not path.is_file():
            raise FileNotFoundError(f"Config YAML not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def to_yaml(self, yaml_path: Union[str, Path]) -> None:
        """Serialize configuration to a YAML file."""
        path = Path(yaml_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.model_dump(), f, sort_keys=False)

    def with_overrides(self, **kwargs: Any) -> "SystemConfig":
        """Return a new SystemConfig instance with specified field overrides."""
        current_data = self.model_dump()
        current_data.update({k: v for k, v in kwargs.items() if v is not None})
        return SystemConfig(**current_data)
