"""Live 0.5 m Wall-Clearance Halo Tracker.

Computes exact euclidean distance from any candidate device node or hub coordinate
to obstacle geometry (walls, windows, front door, exterior inner boundary) and checks
eligible room containment in < 1 ms.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import shapely
from shapely.geometry import Point

from resplan_router.contracts import MetricFloorPlan, Point2D


class ClearanceHaloTracker:
    """Live wall-clearance halo tracker enforcing Slide 3 0.5 m clearance rules."""

    @classmethod
    def check_clearance(
        cls,
        point: Point2D,
        plan: MetricFloorPlan,
        required_clearance_m: float = 0.50,
    ) -> Dict[str, Any]:
        """Check whether a point maintains the mandatory 0.50 m clearance from walls/windows.

        Args:
            point: 2D coordinate to test.
            plan: MetricFloorPlan containing obstacle and room geometries.
            required_clearance_m: Mandatory wall clearance in metres (default 0.50 m).

        Returns:
            Dictionary containing:
            - is_clear: True iff distance >= required_clearance_m - 0.001 and point inside eligible room
            - distance_m: Distance to nearest wall/window/boundary in metres
            - required_clearance_m: Required threshold (0.50 m)
            - halo_radius_m: Radius to draw halo (0.50 m)
            - color: Hex color code (#2ECC71 for valid, #E6194B for violation)
            - status_text: Human-readable status string
            - room_id: ID of the host room or None
            - inside_eligible: True iff inside an eligible room
        """
        p = Point(point.x, point.y)
        tol = 0.001

        # 1. Distance to obstacles (walls, windows, front door)
        obs_geom = plan.obstacles_geom
        dist_obs = float("inf")
        if obs_geom is not None and not obs_geom.is_empty:
            dist_obs = float(obs_geom.distance(p))

        # 2. Distance to exterior enclosing inner boundary
        dist_inner = float("inf")
        if plan.inner_wkt:
            inner_bound = plan.inner_geom.boundary
            if inner_bound is not None and not inner_bound.is_empty:
                dist_inner = float(inner_bound.distance(p))

        distance_m = min(dist_obs, dist_inner)
        if distance_m < 0.0 or distance_m == float("inf"):
            distance_m = 0.0

        # 3. Room containment check
        inside_room_id: Optional[str] = None
        inside_eligible = False

        for r_id, room in plan.rooms.items():
            geom = room.geometry
            if geom.buffer(tol).contains(p):
                inside_room_id = r_id
                if room.is_eligible:
                    inside_eligible = True
                break

        # If inside an obstacle (e.g. inside a wall body), clearance is 0.0
        if obs_geom is not None and not obs_geom.is_empty and obs_geom.contains(p):
            distance_m = 0.0

        is_clear = bool(inside_eligible and (distance_m >= (required_clearance_m - tol)))
        color = "#2ECC71" if is_clear else "#E6194B"

        if is_clear:
            status_text = "CLEAR (>= 0.5m)"
        elif not inside_eligible:
            status_text = f"OUTSIDE ELIGIBLE ROOM ({distance_m:.2f}m)"
        else:
            status_text = f"TOO CLOSE ({distance_m:.2f}m < 0.50m)"

        return {
            "is_clear": is_clear,
            "distance_m": round(float(distance_m), 4),
            "required_clearance_m": required_clearance_m,
            "halo_radius_m": required_clearance_m,
            "color": color,
            "status_text": status_text,
            "room_id": inside_room_id,
            "inside_eligible": inside_eligible,
        }
