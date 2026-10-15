"""Phase 2: Door and open-passage topology extraction and portal waypoints."""

from resplan_router.phase2_topology.portal_extractor import (
    extract_all_portals,
    extract_door_portals,
    extract_open_passages,
)

__all__ = [
    "extract_all_portals",
    "extract_door_portals",
    "extract_open_passages",
]
