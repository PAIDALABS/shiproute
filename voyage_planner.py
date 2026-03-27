"""Multi-leg voyage planning with fuel/cost estimation."""

import searoute as sr
from typing import Optional


def calculate_multi_leg(waypoints: list[dict], speed_knots: float = 14.0) -> dict:
    """Calculate route for multiple waypoints.

    Each waypoint: {"lat": float, "lon": float, "name": str (optional)}
    Returns: {"legs": [...], "totals": {...}}
    """
    if len(waypoints) < 2:
        return {"error": "Need at least 2 waypoints"}

    legs = []
    total_nmi = 0
    total_hours = 0

    for i in range(len(waypoints) - 1):
        origin = waypoints[i]
        dest = waypoints[i + 1]

        try:
            route = sr.searoute(
                [origin["lon"], origin["lat"]],
                [dest["lon"], dest["lat"]],
                units="naut",
            )
        except Exception as e:
            return {"error": f"Route calculation failed for leg {i+1}: {e}"}

        dist_nmi = route["properties"].get("length", 0)
        dist_km = dist_nmi * 1.852
        duration_hours = dist_nmi / speed_knots if speed_knots > 0 else 0

        legs.append({
            "leg": i + 1,
            "origin": {"name": origin.get("name", f"WP{i+1}"), "lat": origin["lat"], "lon": origin["lon"]},
            "destination": {"name": dest.get("name", f"WP{i+2}"), "lat": dest["lat"], "lon": dest["lon"]},
            "route": route,
            "distance_nmi": round(dist_nmi, 1),
            "distance_km": round(dist_km, 1),
            "duration_hours": round(duration_hours, 1),
        })
        total_nmi += dist_nmi
        total_hours += duration_hours

    return {
        "legs": legs,
        "totals": {
            "legs": len(legs),
            "distance_nmi": round(total_nmi, 1),
            "distance_km": round(total_nmi * 1.852, 1),
            "duration_hours": round(total_hours, 1),
            "duration_days": round(total_hours / 24, 1),
        },
        "speed_knots": speed_knots,
    }


def estimate_fuel_cost(
    distance_nmi: float,
    speed_knots: float,
    consumption_mt_per_day: float,
    fuel_price_per_mt: float = 600.0,
) -> dict:
    """Estimate fuel consumption and cost for a voyage."""
    voyage_days = distance_nmi / (speed_knots * 24) if speed_knots > 0 else 0
    fuel_mt = voyage_days * consumption_mt_per_day
    fuel_cost = fuel_mt * fuel_price_per_mt

    return {
        "voyage_days": round(voyage_days, 1),
        "fuel_mt": round(fuel_mt, 1),
        "fuel_cost_usd": round(fuel_cost),
        "consumption_mt_per_day": consumption_mt_per_day,
        "fuel_price_per_mt": fuel_price_per_mt,
    }
