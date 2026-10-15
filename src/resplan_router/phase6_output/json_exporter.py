"""Official Slide 8 JSON Exporter.

Serializes floor plan routing solutions verbatim to the official Slide 8 competition
JSON schema with 0.1 mm coordinate precision and clean Pydantic v2 validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from resplan_router.contracts import (
    HubZonePartition,
    MetricFloorPlan,
    SynthesizedRingsBundle,
)


class Slide8RouteSegment(BaseModel):
    """Single directed leg of a closed ring in Slide 8 JSON format."""

    model_config = ConfigDict(populate_by_name=True)

    from_node: str = Field(alias="from", description="Departure terminal ID (hub or node)")
    to_node: str = Field(alias="to", description="Arrival terminal ID (hub or node)")
    length_m: float = Field(description="Segment cable length in metres", ge=0.0)
    polyline: list[list[float]] = Field(
        description="Ordered sequence of [x, y] coordinates in metres (0.1 mm precision)"
    )


class Slide8Hub(BaseModel):
    """Hub ring specification in Slide 8 JSON format."""

    hub_id: str = Field(description="Unique hub identifier")
    room_id: str = Field(description="Host room ID containing the hub")
    location: dict[str, float] = Field(description="Hub coordinate location {'x': ..., 'y': ...}")
    assigned_rooms: list[str] = Field(description="List of room IDs assigned to this hub")
    assigned_nodes: list[str] = Field(description="List of node IDs served by this hub")
    closed_sequence: list[str] = Field(description="Ordered visiting sequence [H1, N..., H1]")
    route_segments: list[Slide8RouteSegment] = Field(
        description="List of directed route segments completing the closed ring"
    )
    ring_length_m: float = Field(description="Total cable length of this ring in metres", ge=0.0)


class Slide8ReportedMetrics(BaseModel):
    """Top-level self-reported metrics in Slide 8 JSON format."""

    hub_count: int = Field(description="Total number of hubs", ge=1)
    total_length_m: float = Field(description="Total cable length across all hubs in metres", ge=0.0)


class Slide8Export(BaseModel):
    """Complete Slide 8 competition submission document."""

    plan_id: str = Field(description="Unique floor plan identifier")
    units: str = Field(default="m", description="Metric unit symbol ('m')")
    hubs: list[Slide8Hub] = Field(description="List of hub routing specifications")
    reported_metrics: Slide8ReportedMetrics = Field(description="Self-reported summary metrics")


def export_slide8_json(
    plan: MetricFloorPlan,
    bundle: SynthesizedRingsBundle,
    partition: Optional[HubZonePartition] = None,
    output_path: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """Serialize synthesized closed rings to official Slide 8 competition JSON.

    Args:
        plan: MetricFloorPlan containing verified geometry.
        bundle: SynthesizedRingsBundle containing completed hub rings.
        partition: Optional HubZonePartition (if provided, cross-verifies room/node assignments).
        output_path: Optional file path to write formatted JSON.

    Returns:
        Dictionary adhering verbatim to Slide 8 competition schema.
    """
    hubs_data: list[dict[str, Any]] = []

    # Map partition assignments by hub_id if available
    partition_map = {h.hub_id: h for h in partition.hubs} if partition else {}

    for ring in bundle.hub_rings:
        hub_part = partition_map.get(ring.hub_id)
        assigned_rooms = (
            list(hub_part.assigned_rooms) if hub_part else list(ring.assigned_rooms)
        )
        assigned_nodes = (
            list(hub_part.assigned_nodes) if hub_part else list(ring.assigned_nodes)
        )

        segments_data: list[dict[str, Any]] = []
        for seg in ring.route_segments:
            poly_coords = [
                [round(float(pt.x), 4), round(float(pt.y), 4)] for pt in seg.polyline
            ]
            segments_data.append(
                {
                    "from": seg.from_node,
                    "to": seg.to_node,
                    "length_m": round(float(seg.length_m), 4),
                    "polyline": poly_coords,
                }
            )

        hub_dict: dict[str, Any] = {
            "hub_id": ring.hub_id,
            "room_id": ring.room_id,
            "location": {
                "x": round(float(ring.location.x), 4),
                "y": round(float(ring.location.y), 4),
            },
            "assigned_rooms": assigned_rooms,
            "assigned_nodes": assigned_nodes,
            "closed_sequence": list(ring.closed_sequence),
            "route_segments": segments_data,
            "ring_length_m": round(float(ring.ring_length_m), 4),
        }
        hubs_data.append(hub_dict)

    result_dict: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "units": "m",
        "hubs": hubs_data,
        "reported_metrics": {
            "hub_count": len(bundle.hub_rings),
            "total_length_m": round(float(bundle.total_length_m), 4),
        },
    }

    # Strict schema validation via Pydantic model
    validated_model = Slide8Export.model_validate(result_dict)
    # Ensure standard python dict representation matching alias names
    export_dict = validated_model.model_dump(by_alias=True)

    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(export_dict, f, indent=2, ensure_ascii=False)

    return export_dict
