from pathlib import Path
from typing import List, Union
import yaml
from pydantic import BaseModel, Field


class CostWeights(BaseModel):
    w_len: float = Field(default=1.0, description="Cost weight per meter of cable length")
    w_bend: float = Field(default=0.5, description="Cost weight per 90-degree bend")
    w_ovlp: float = Field(default=50.0, description="Cost weight per meter of collinear overlap")
    w_cross: float = Field(default=25.0, description="Cost weight per orthogonal cable crossing")

    model_config = {"frozen": True}


class SystemConfig(BaseModel):
    profile_name: str = Field(default="strict_v1", description="Profile identifier")
    description: str = Field(default="", description="Profile description")
    max_area_per_hub: float = Field(default=100.0, ge=10.0, description="Maximum eligible room area in m2 per hub")
    max_nodes_per_hub: int = Field(default=5, ge=1, description="Maximum devices/rooms per hub")
    min_wall_clearance_m: float = Field(default=0.20, ge=0.05, description="Minimum distance from nodes/hubs to room boundary")
    pole_buffer_clearance_m: float = Field(default=0.25, ge=0.05, description="Buffer erosion distance for polylabel")
    grid_snap_tolerance_m: float = Field(default=0.05, ge=0.01, description="Coordinate snapping resolution in meters")
    portal_lane_offset_delta_m: float = Field(default=0.15, ge=0.05, description="Half-separation delta for dual-lane doorways")
    portal_step_clearance_m: float = Field(default=0.20, ge=0.05, description="Entry/exit step distance through portal normal")
    eligible_room_types: List[str] = Field(
        default_factory=lambda: ["living", "bedroom", "kitchen", "storage"],
        description="Room categories requiring exactly 1 device node",
    )
    excluded_room_types: List[str] = Field(
        default_factory=lambda: ["bathroom", "balcony", "parking", "pool", "stair", "garden", "land"],
        description="Room categories receiving 0 devices",
    )
    transit_corridor_types: List[str] = Field(
        default_factory=lambda: ["corridor", "hallway", "passage"],
        description="Excluded room categories allowed to serve as transit nodes",
    )
    cost_weights: CostWeights = Field(default_factory=CostWeights)
    time_limit_cp_sat_sec: float = Field(default=3.0, ge=0.5, description="CP-SAT MILP solver timeout in seconds")
    time_limit_routing_sec: float = Field(default=1.0, ge=0.1, description="TSP routing solver timeout in seconds")

    model_config = {"frozen": True}


def load_config(config_path: Union[str, Path]) -> SystemConfig:
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return SystemConfig.model_validate(data)
