import math
import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class AmbiguousCoordinateUnitError(Exception):
    """Raised when the coordinate unit cannot be verified deterministically."""
    pass


def compute_metric_scale_factor(data: Dict[str, Any], raw_room_area: float) -> Tuple[float, str]:
    """
    Computes metric scale multiplier based on explicit metadata.
    Raises AmbiguousCoordinateUnitError if units or calibration data are ambiguous.
    """
    coord_unit = data.get("coordinate_unit")
    if not coord_unit or coord_unit not in {"meter", "pixel"}:
        raise AmbiguousCoordinateUnitError(
            f"Missing or invalid coordinate_unit: '{coord_unit}'. Must be explicitly 'meter' or 'pixel'."
        )

    if coord_unit == "meter":
        return 1.0, "Input coordinates explicitly declared in meters (scale factor 1.0)."

    # If pixel, we require explicit metadata area (GFA in m2)
    metadata_area = data.get("area")
    if not metadata_area or metadata_area <= 0:
        raise AmbiguousCoordinateUnitError(
            "Coordinate unit is 'pixel' but valid 'area' (GFA m2) metadata is missing."
        )

    if raw_room_area <= 0:
        raise ValueError("Computed raw room area must be strictly positive to calibrate scale.")

    scale = math.sqrt(metadata_area / raw_room_area)
    msg = f"Calibrated pixel coordinates to meters using GFA {metadata_area} m2 / raw area {raw_room_area:.1f}: scale={scale:.6f}"
    logger.info(msg)
    return scale, msg
