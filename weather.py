"""Weather integration using Open-Meteo Marine API (free, no key required)."""

import asyncio
import math
from datetime import datetime, timezone, timedelta

import httpx

MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"


def _risk_level(wave_height_m: float, wind_speed_kts: float) -> str:
    if wave_height_m > 6 or wind_speed_kts > 45:
        return "SEVERE"
    if wave_height_m > 4 or wind_speed_kts > 35:
        return "HIGH"
    if wave_height_m > 2.5 or wind_speed_kts > 25:
        return "MODERATE"
    return "LOW"


def _risk_color(level: str) -> str:
    return {"SEVERE": "#ef5350", "HIGH": "#ff9800", "MODERATE": "#ffeb3b", "LOW": "#4caf50"}[level]


def _sample_points_along_route(route_geojson: dict, num_points: int = 20) -> list[dict]:
    """Sample evenly-spaced points along a GeoJSON route."""
    coords = route_geojson.get("geometry", {}).get("coordinates", [])
    if not coords:
        features = route_geojson.get("features", [])
        feat = features[0] if features else route_geojson
        coords = feat.get("geometry", {}).get("coordinates", [])

    if len(coords) < 2:
        return []

    # Calculate cumulative distances
    R = 3440.065  # Earth radius in nm
    cum_dist = [0]
    for i in range(1, len(coords)):
        lon1, lat1 = math.radians(coords[i-1][0]), math.radians(coords[i-1][1])
        lon2, lat2 = math.radians(coords[i][0]), math.radians(coords[i][1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        d = R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        cum_dist.append(cum_dist[-1] + d)

    total_dist = cum_dist[-1]
    if total_dist == 0:
        return []

    # Sample evenly
    step = total_dist / max(num_points - 1, 1)
    points = []
    coord_idx = 0

    for i in range(num_points):
        target = i * step
        while coord_idx < len(cum_dist) - 1 and cum_dist[coord_idx + 1] < target:
            coord_idx += 1

        if coord_idx >= len(coords) - 1:
            lon, lat = coords[-1]
        else:
            seg_start = cum_dist[coord_idx]
            seg_end = cum_dist[coord_idx + 1]
            seg_len = seg_end - seg_start
            frac = (target - seg_start) / seg_len if seg_len > 0 else 0
            lon = coords[coord_idx][0] + frac * (coords[coord_idx + 1][0] - coords[coord_idx][0])
            lat = coords[coord_idx][1] + frac * (coords[coord_idx + 1][1] - coords[coord_idx][1])

        points.append({
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "distance_nmi": round(target, 1),
        })

    return points


async def get_route_weather(
    route_geojson: dict,
    speed_knots: float = 14.0,
    departure_time: str | None = None,
    num_points: int = 20,
) -> dict:
    """Fetch marine weather along a route."""
    points = _sample_points_along_route(route_geojson, min(num_points, 30))
    if not points:
        return {"error": "Could not sample route points"}

    # Calculate ETA at each point
    dep = datetime.fromisoformat(departure_time) if departure_time else datetime.now(timezone.utc)
    for p in points:
        hours_to_point = p["distance_nmi"] / speed_knots if speed_knots > 0 else 0
        p["eta"] = (dep + timedelta(hours=hours_to_point)).isoformat()

    # Fetch weather for all points concurrently
    async with httpx.AsyncClient() as client:
        tasks = []
        for p in points:
            tasks.append(_fetch_marine_point(client, p["lat"], p["lon"]))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge weather data with points
    weather_points = []
    for p, wx in zip(points, results):
        if isinstance(wx, Exception) or wx is None:
            p["weather"] = None
            p["risk_level"] = "UNKNOWN"
            p["risk_color"] = "#8b949e"
        else:
            p["weather"] = wx
            p["risk_level"] = _risk_level(wx.get("wave_height_m", 0), wx.get("wind_speed_kts", 0))
            p["risk_color"] = _risk_color(p["risk_level"])
        weather_points.append(p)

    # Overall risk = worst segment
    risk_levels = [p["risk_level"] for p in weather_points if p["risk_level"] != "UNKNOWN"]
    risk_order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3}
    overall = max(risk_levels, key=lambda r: risk_order.get(r, -1)) if risk_levels else "UNKNOWN"

    # Find worst segment
    worst = max(weather_points, key=lambda p: risk_order.get(p["risk_level"], -1))

    return {
        "weather_points": weather_points,
        "overall_risk": overall,
        "overall_risk_color": _risk_color(overall) if overall != "UNKNOWN" else "#8b949e",
        "worst_point": {
            "lat": worst["lat"],
            "lon": worst["lon"],
            "distance_nmi": worst["distance_nmi"],
            "risk_level": worst["risk_level"],
            "weather": worst.get("weather"),
        },
        "points_sampled": len(weather_points),
    }


async def _fetch_marine_point(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    """Fetch current marine weather for a single point."""
    try:
        resp = await client.get(MARINE_API, params={
            "latitude": lat,
            "longitude": lon,
            "current": "wave_height,wave_direction,wave_period,wind_wave_height,swell_wave_height",
            "wind_speed_unit": "kn",
        }, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json().get("current", {})
        return {
            "wave_height_m": data.get("wave_height") or 0,
            "wave_direction": data.get("wave_direction") or 0,
            "wave_period_s": data.get("wave_period") or 0,
            "wind_wave_height_m": data.get("wind_wave_height") or 0,
            "swell_height_m": data.get("swell_wave_height") or 0,
        }
    except Exception:
        return None


async def get_port_weather(lat: float, lon: float) -> dict:
    """Fetch current weather + 7-day forecast for a port location."""
    async with httpx.AsyncClient() as client:
        # Marine data (waves)
        marine_resp = await client.get(MARINE_API, params={
            "latitude": lat,
            "longitude": lon,
            "current": "wave_height,wave_direction,wave_period",
            "daily": "wave_height_max,wave_period_max",
            "forecast_days": 7,
        }, timeout=10)

        # Weather data (wind, temp, visibility)
        weather_resp = await client.get(WEATHER_API, params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility",
            "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max,wind_gusts_10m_max,precipitation_sum",
            "wind_speed_unit": "kn",
            "forecast_days": 7,
        }, timeout=10)

    result = {"current": {}, "forecast": []}

    if marine_resp.status_code == 200:
        mc = marine_resp.json().get("current", {})
        result["current"]["wave_height_m"] = mc.get("wave_height") or 0
        result["current"]["wave_direction"] = mc.get("wave_direction") or 0
        result["current"]["wave_period_s"] = mc.get("wave_period") or 0

        md = marine_resp.json().get("daily", {})
        wave_max = md.get("wave_height_max", [])
        wave_period_max = md.get("wave_period_max", [])
        dates = md.get("time", [])
        for i, date in enumerate(dates):
            while len(result["forecast"]) <= i:
                result["forecast"].append({"date": date})
            result["forecast"][i]["wave_max_m"] = wave_max[i] if i < len(wave_max) else 0

    if weather_resp.status_code == 200:
        wc = weather_resp.json().get("current", {})
        result["current"]["temperature_c"] = wc.get("temperature_2m") or 0
        result["current"]["wind_speed_kts"] = wc.get("wind_speed_10m") or 0
        result["current"]["wind_direction"] = wc.get("wind_direction_10m") or 0
        result["current"]["wind_gusts_kts"] = wc.get("wind_gusts_10m") or 0
        result["current"]["visibility_m"] = wc.get("visibility") or 0

        wd = weather_resp.json().get("daily", {})
        dates = wd.get("time", [])
        temp_max = wd.get("temperature_2m_max", [])
        temp_min = wd.get("temperature_2m_min", [])
        wind_max = wd.get("wind_speed_10m_max", [])
        gust_max = wd.get("wind_gusts_10m_max", [])
        precip = wd.get("precipitation_sum", [])

        for i, date in enumerate(dates):
            while len(result["forecast"]) <= i:
                result["forecast"].append({"date": date})
            f = result["forecast"][i]
            f["date"] = date
            f["temp_max_c"] = temp_max[i] if i < len(temp_max) else 0
            f["temp_min_c"] = temp_min[i] if i < len(temp_min) else 0
            f["wind_max_kts"] = wind_max[i] if i < len(wind_max) else 0
            f["gust_max_kts"] = gust_max[i] if i < len(gust_max) else 0
            f["precipitation_mm"] = precip[i] if i < len(precip) else 0

    # Risk assessment
    wh = result["current"].get("wave_height_m", 0)
    ws = result["current"].get("wind_speed_kts", 0)
    result["current"]["risk_level"] = _risk_level(wh, ws)
    result["current"]["risk_color"] = _risk_color(result["current"]["risk_level"])

    return result
