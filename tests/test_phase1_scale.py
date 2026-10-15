"""Unit tests for Phase 1 scale conversion and normalization."""

import json
from pathlib import Path
import pytest
from shapely.geometry import Polygon

from resplan_router.phase1_ingestion.scale_converter import (
    compute_scale_factor,
    normalize_to_metric,
    parse_geometry,
)


@pytest.fixture
def slide4_fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "slide4_scale_plan.json"


@pytest.fixture
def slide4_data(slide4_fixture_path: Path) -> dict:
    with open(slide4_fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_slide4_exact_scale_conversion(slide4_data: dict) -> None:
    """Verify exact Slide 4 numerical specifications:

    - 200 x 180 drawing units
    - inner.area = 36000
    - plan area = 110.0 m^2
    - scale = 18.09068 ± 1e-4
    - metric width ~ 11.055 m, height ~ 9.950 m
    - 0.5 m clearance ~ 9.045 drawing units
    """
    scale = compute_scale_factor(slide4_data)

    # 1. Scale factor verification
    expected_scale = 18.09068
    assert scale == pytest.approx(expected_scale, rel=1e-4)

    # 2. Converted metric dimensions
    inner_geom = parse_geometry(slide4_data["inner"])
    metric_geom = normalize_to_metric(inner_geom, scale)

    min_x, min_y, max_x, max_y = metric_geom.bounds
    width_m = max_x - min_x
    height_m = max_y - min_y

    assert width_m == pytest.approx(11.055, abs=0.005)
    assert height_m == pytest.approx(9.950, abs=0.005)
    assert min_x == pytest.approx(0.0, abs=1e-6)
    assert min_y == pytest.approx(0.0, abs=1e-6)

    # Metric area should equal exactly 110.0 m^2
    assert metric_geom.area == pytest.approx(110.0, rel=1e-5)

    # 3. 0.5m clearance equivalent in drawing units
    drawing_units_0_5m = 0.5 * scale
    assert drawing_units_0_5m == pytest.approx(9.045, abs=0.01)


def test_net_area_ignored(slide4_data: dict) -> None:
    """Verify that net_area is strictly ignored and causes zero error even when 0 or mismatching."""
    slide4_data["net_area"] = 0.0
    scale_zero = compute_scale_factor(slide4_data)

    slide4_data["net_area"] = 9999.0
    scale_arbitrary = compute_scale_factor(slide4_data)

    del slide4_data["net_area"]
    scale_missing = compute_scale_factor(slide4_data)

    assert scale_zero == scale_arbitrary == scale_missing
    assert scale_zero == pytest.approx(18.09068, rel=1e-4)


def test_normalize_to_metric_origin_alignment() -> None:
    """Verify lower-left origin alignment with non-zero initial bounding box."""
    # Rectangle of 200 x 180 starting at (50, 75)
    poly = Polygon([(50.0, 75.0), (250.0, 75.0), (250.0, 255.0), (50.0, 255.0)])
    scale = 20.0
    origin_offset = (50.0, 75.0)

    norm_poly = normalize_to_metric(poly, scale, origin_offset=origin_offset)

    min_x, min_y, max_x, max_y = norm_poly.bounds
    assert min_x == pytest.approx(0.0, abs=1e-6)
    assert min_y == pytest.approx(0.0, abs=1e-6)
    assert max_x == pytest.approx(10.0, abs=1e-6)
    assert max_y == pytest.approx(9.0, abs=1e-6)
    assert norm_poly.area == pytest.approx(36000.0 / (20.0 ** 2), rel=1e-6)


def test_invalid_scale_inputs() -> None:
    """Verify robust error handling for invalid or non-positive areas."""
    with pytest.raises(ValueError, match="strictly positive"):
        compute_scale_factor({"area": 0.0, "inner": [[0, 0], [10, 0], [10, 10], [0, 10]]})

    with pytest.raises(ValueError, match="strictly positive"):
        compute_scale_factor({"area": -10.0, "inner": [[0, 0], [10, 0], [10, 10], [0, 10]]})

    with pytest.raises(ValueError, match="contain 'area' key"):
        compute_scale_factor({"inner": [[0, 0], [10, 0], [10, 10], [0, 10]]})

    with pytest.raises(ValueError, match="strictly positive"):
        normalize_to_metric(Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]), scale=0.0)
