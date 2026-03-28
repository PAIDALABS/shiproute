"""Vessel search, detail retrieval, and availability scoring."""

import asyncio
import time
from typing import Optional

import httpx


# In-memory cache for vessel_info (24h TTL)
_vessel_cache: dict[int, dict] = {}
_cache_ttl = 86400  # 24 hours


def _get_cached(mmsi: int) -> Optional[dict]:
    entry = _vessel_cache.get(mmsi)
    if entry and (time.time() - entry["_cached_at"]) < _cache_ttl:
        return entry
    return None


def _set_cached(mmsi: int, data: dict):
    data["_cached_at"] = time.time()
    _vessel_cache[mmsi] = data
    # Trim cache if too large
    if len(_vessel_cache) > 2000:
        oldest = sorted(_vessel_cache.items(), key=lambda x: x[1].get("_cached_at", 0))[:500]
        for k, _ in oldest:
            del _vessel_cache[k]


AFRICA_KEYWORDS = [
    "MOMBASA", "DAR", "MAPUTO", "DJIBOUTI", "MOGADISHU", "LAGOS", "APAPA",
    "TEMA", "ABIDJAN", "DAKAR", "LUANDA", "DOUALA", "DURBAN", "CAPE TOWN",
    "BEIRA", "NACALA", "TOAMASINA", "TAMATAVE", "ZANZIBAR", "LAMU",
    "BERBERA", "AFRICA", "MADAGASCAR", "KENYA", "TANZANIA", "MOZAMBIQUE",
    "NIGERIA", "GHANA", "SENEGAL", "ANGOLA", "CAMEROON", "SOMALIA",
]

BAGGED_CARGO_TYPES = ["General Cargo", "Multi Purpose", "Cargo", "Bulk Carrier"]


def score_availability(vessel: dict) -> dict:
    """Score vessel availability 0.0-1.0 with reason."""
    score = 0.0
    reasons = []

    speed = vessel.get("speed") or 0
    dest = (vessel.get("destination") or "").strip()

    if speed < 0.5:
        score += 0.3
        reasons.append("Stationary")
    elif speed < 2:
        score += 0.1
        reasons.append("Barely moving")

    # No destination or destination = current area
    if not dest or dest.upper() in ("", "CLASS B"):
        score += 0.10
        reasons.append("No destination set")

    # Check if vessel type suggests commercial availability
    vtype = (vessel.get("type_specific") or vessel.get("type") or "").lower()
    if any(t in vtype for t in ["cargo", "bulk", "tanker", "general"]):
        score += 0.15
        reasons.append("Commercial vessel type")

    # At anchor (not berthed = not actively loading)
    if speed < 0.5 and vessel.get("distance", 0) > 0.5:
        score += 0.2
        reasons.append("At anchorage")

    score = min(score, 1.0)

    if score >= 0.7:
        label = "Likely Available"
    elif score >= 0.4:
        label = "Possibly Available"
    elif score >= 0.2:
        label = "Uncertain"
    else:
        label = "En Route"

    return {
        "score": round(score, 2),
        "label": label,
        "reasons": reasons,
    }


def is_africa_trade(vessel: dict) -> Optional[str]:
    """Check if vessel is likely on an Africa trade route. Returns reason or None."""
    dest = (vessel.get("destination") or "").upper()
    flag = (vessel.get("country_iso") or "").upper()
    vtype = vessel.get("type_specific") or vessel.get("type") or ""

    is_cargo = any(bt.lower() in vtype.lower() for bt in BAGGED_CARGO_TYPES)

    if is_cargo:
        for kw in AFRICA_KEYWORDS:
            if kw in dest:
                return f"Destination: {dest}"

    africa_flags = {"MZ", "KE", "TZ", "NG", "GH", "SN", "AO", "CM", "DJ", "SO", "MG", "ZA"}
    if is_cargo and flag in africa_flags:
        return f"Africa-flagged ({flag})"

    return None


async def search_vessels(
    api_key: str,
    lat: float,
    lon: float,
    radius_nm: float = 15,
    vessel_type: Optional[str] = None,
    idle_only: bool = False,
    africa_only: bool = False,
) -> dict:
    """Search for vessels near a point using Datalastic."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.datalastic.com/api/v0/vessel_inradius",
            params={"api-key": api_key, "lat": lat, "lon": lon, "radius": radius_nm},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

    vessels = data.get("vessels", [])
    total_raw = data.get("total", len(vessels))

    # Filter
    results = []
    for v in vessels:
        # Type filter
        if vessel_type:
            vt = (v.get("type") or "").lower()
            vts = (v.get("type_specific") or "").lower()
            if vessel_type.lower() not in vt and vessel_type.lower() not in vts:
                continue

        # Idle filter
        if idle_only and (v.get("speed") or 0) > 0.5:
            continue

        # Availability
        avail = score_availability(v)
        v["availability"] = avail

        # Africa check
        africa_reason = is_africa_trade(v)
        v["africa_trade"] = africa_reason

        if africa_only and not africa_reason:
            continue

        results.append(v)

    # Sort: available first, then by distance
    results.sort(key=lambda v: (-v["availability"]["score"], v.get("distance", 999)))

    return {
        "total_in_area": total_raw,
        "filtered_count": len(results),
        "vessels": results,
    }


async def get_vessel_detail(api_key: str, mmsi: int) -> Optional[dict]:
    """Get detailed vessel info with caching."""
    cached = _get_cached(mmsi)
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.datalastic.com/api/v0/vessel_info",
            params={"api-key": api_key, "mmsi": mmsi},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        info = resp.json().get("data")
        if not info:
            return None

    _set_cached(mmsi, info)
    return info


async def get_vessel_track(api_key: str, mmsi: int, days: int = 30) -> Optional[dict]:
    """Get vessel position history."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.datalastic.com/api/v0/vessel_history",
            params={"api-key": api_key, "mmsi": mmsi, "days": days},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data")
        if not data or not data.get("positions"):
            return None

    # Convert positions to GeoJSON LineString for map rendering
    positions = data["positions"]
    coords = [[p["lon"], p["lat"]] for p in positions if p.get("lat") and p.get("lon")]

    return {
        "mmsi": mmsi,
        "name": data.get("name"),
        "type": data.get("type_specific") or data.get("type"),
        "positions": positions,
        "track_geojson": {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"mmsi": mmsi, "name": data.get("name")},
        } if len(coords) >= 2 else None,
    }
