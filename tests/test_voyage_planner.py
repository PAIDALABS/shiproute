"""Tests for voyage_planner module."""

import pytest
from voyage_planner import estimate_fuel_cost, calculate_multi_leg


class TestEstimateFuelCost:
    def test_estimate_fuel_cost_basic(self):
        """distance=1000, speed=14, consumption=25, price=600 -> positive reasonable values."""
        result = estimate_fuel_cost(
            distance_nmi=1000,
            speed_knots=14,
            consumption_mt_per_day=25,
            fuel_price_per_mt=600,
        )
        assert result["fuel_mt"] > 0
        assert result["fuel_cost_usd"] > 0
        # 1000 nmi at 14 kts -> ~2.976 days -> ~74.4 mt fuel -> ~$44,643
        assert result["voyage_days"] == pytest.approx(3.0, abs=0.1)
        assert result["fuel_mt"] == pytest.approx(74.4, abs=1.0)
        assert result["fuel_cost_usd"] == pytest.approx(44643, abs=200)
        assert result["consumption_mt_per_day"] == 25
        assert result["fuel_price_per_mt"] == 600

    def test_estimate_fuel_cost_zero_speed(self):
        """speed=0 -> should return 0 values without crashing."""
        result = estimate_fuel_cost(
            distance_nmi=1000,
            speed_knots=0,
            consumption_mt_per_day=25,
            fuel_price_per_mt=600,
        )
        assert result["voyage_days"] == 0
        assert result["fuel_mt"] == 0
        assert result["fuel_cost_usd"] == 0


class TestCalculateMultiLeg:
    def test_calculate_multi_leg_insufficient_waypoints(self):
        """Single waypoint -> returns dict with 'error' key."""
        result = calculate_multi_leg([{"lat": 51.92, "lon": 4.48, "name": "Rotterdam"}])
        assert "error" in result

    def test_calculate_multi_leg_two_waypoints(self):
        """Two valid coastal waypoints (Rotterdam -> Antwerp) -> 1 leg, positive distance."""
        waypoints = [
            {"lat": 51.92, "lon": 4.48, "name": "Rotterdam"},
            {"lat": 51.23, "lon": 4.42, "name": "Antwerp"},
        ]
        result = calculate_multi_leg(waypoints, speed_knots=14.0)
        assert "error" not in result
        assert "legs" in result
        assert len(result["legs"]) == 1
        assert result["totals"]["distance_nmi"] > 0
        assert result["totals"]["legs"] == 1
        assert result["speed_knots"] == 14.0
        # Verify leg structure
        leg = result["legs"][0]
        assert leg["leg"] == 1
        assert leg["origin"]["name"] == "Rotterdam"
        assert leg["destination"]["name"] == "Antwerp"
        assert leg["distance_nmi"] > 0
        assert leg["distance_km"] > 0
        assert leg["duration_hours"] > 0
