"""Tests for weather._risk_level, _risk_color, and _sample_points_along_route."""

import pytest

from weather import _risk_level, _risk_color, _sample_points_along_route


# ---------------------------------------------------------------------------
# _risk_level
# ---------------------------------------------------------------------------


def test_risk_level_low():
    assert _risk_level(wave_height_m=1.0, wind_speed_kts=15) == "LOW"


def test_risk_level_moderate():
    assert _risk_level(wave_height_m=3.8, wind_speed_kts=20) == "MODERATE"


def test_risk_level_high():
    assert _risk_level(wave_height_m=4.5, wind_speed_kts=36) == "HIGH"


def test_risk_level_severe():
    assert _risk_level(wave_height_m=7.0, wind_speed_kts=50) == "SEVERE"


def test_risk_level_wind_only():
    """Wind alone (31 kts) triggers MODERATE even with zero wave height."""
    assert _risk_level(wave_height_m=0, wind_speed_kts=31) == "MODERATE"


# ---------------------------------------------------------------------------
# _risk_color
# ---------------------------------------------------------------------------


def test_risk_color_all_levels():
    """All 4 known levels return hex color strings; unknown returns default."""
    for level in ("SEVERE", "HIGH", "MODERATE", "LOW"):
        color = _risk_color(level)
        assert color.startswith("#"), f"{level} did not return a hex color"

    # Unknown / unrecognised level falls back to a default grey
    default_color = _risk_color("BANANA")
    assert default_color.startswith("#")
    assert default_color == "#8b949e"


# ---------------------------------------------------------------------------
# _sample_points_along_route
# ---------------------------------------------------------------------------


def test_sample_points_two_coords():
    """Simple 2-point route returns num_points points with increasing distance."""
    route = {
        "geometry": {
            "coordinates": [[36.8, -1.3], [39.6, -4.0]],
        }
    }
    num_points = 5
    points = _sample_points_along_route(route, num_points=num_points)

    assert len(points) == num_points

    # First point should be at distance 0
    assert points[0]["distance_nmi"] == 0.0

    # Distances must be monotonically non-decreasing
    for i in range(1, len(points)):
        assert points[i]["distance_nmi"] >= points[i - 1]["distance_nmi"]

    # Each point has lat, lon, and distance_nmi keys
    for p in points:
        assert "lat" in p
        assert "lon" in p
        assert "distance_nmi" in p


def test_sample_points_empty():
    """Empty coordinates list returns []."""
    route = {"geometry": {"coordinates": []}}
    assert _sample_points_along_route(route) == []


def test_sample_points_single_coord():
    """Only 1 coordinate (need at least 2) returns []."""
    route = {"geometry": {"coordinates": [[36.8, -1.3]]}}
    assert _sample_points_along_route(route) == []
