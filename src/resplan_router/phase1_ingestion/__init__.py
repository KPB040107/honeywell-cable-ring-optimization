"""Phase 1: Ingestion, scale normalization, polygon healing, and fixed node loading."""

from resplan_router.phase1_ingestion.dataset_repository import (
    DatasetRepository,
    DatasetUnavailableError,
    InvalidPlanIdError,
    MalformedRecordError,
    PlanNotFoundError,
)
from resplan_router.phase1_ingestion.device_candidates import (
    generate_all_room_devices,
    generate_room_device_candidates,
)
from resplan_router.phase1_ingestion.node_loader import load_fixed_nodes
from resplan_router.phase1_ingestion.parser import (
    build_obstacles_and_freespace,
    classify_room,
    parse_and_heal_polygon,
    parse_floor_plan,
)
from resplan_router.phase1_ingestion.resplan_adapter import (
    adapt_resplan_record,
    floor_plan_to_preview_geojson,
)
from resplan_router.phase1_ingestion.scale_converter import (
    compute_scale_factor,
    normalize_to_metric,
    parse_geometry,
)

__all__ = [
    "DatasetRepository",
    "DatasetUnavailableError",
    "InvalidPlanIdError",
    "MalformedRecordError",
    "PlanNotFoundError",
    "adapt_resplan_record",
    "build_obstacles_and_freespace",
    "classify_room",
    "compute_scale_factor",
    "floor_plan_to_preview_geojson",
    "generate_all_room_devices",
    "generate_room_device_candidates",
    "load_fixed_nodes",
    "normalize_to_metric",
    "parse_and_heal_polygon",
    "parse_floor_plan",
    "parse_geometry",
]
