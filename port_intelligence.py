"""
Port Intelligence — computes comprehensive analytics from vessel data.

Metrics computed from vessel_inradius (instant, no API calls):
  - Flag state distribution
  - Vessel type breakdown / port specialization
  - Speed profile
  - ETA accuracy analysis
  - Anchorage hotspot clustering
  - Destination patterns

Metrics enriched via vessel_info API (slower):
  - Fleet age profile
  - Vessel size classes (Panamax, Supramax, etc.)
  - Cargo load estimation

Metrics from vessel_history API (slowest):
  - Trade route origins (where vessels came from)
"""

import asyncio
import logging
import math
import time
from collections import Counter
from datetime import datetime

import httpx


def compute_instant_intelligence(vessels: list[dict], port_def: dict) -> dict:
    """Compute all metrics that need NO extra API calls."""

    # ── Flag state distribution ──────────────────────────────
    flags = Counter()
    for v in vessels:
        f = v.get("country_iso") or v.get("flag") or "Unknown"
        if f:
            flags[f] += 1
        else:
            flags["Unknown"] += 1
    flag_dist = [{"flag": f, "count": c, "pct": round(100 * c / max(len(vessels), 1), 1)}
                 for f, c in flags.most_common(15)]

    # ── Vessel type breakdown ────────────────────────────────
    types = Counter()
    type_specific = Counter()
    for v in vessels:
        types[v.get("ship_type") or "Unknown"] += 1
        ts = v.get("type_specific") or v.get("ship_type") or "Unknown"
        type_specific[ts] += 1
    type_dist = [{"type": t, "count": c} for t, c in type_specific.most_common(15)]

    # Port specialization — dominant cargo types
    cargo_types = {t: c for t, c in type_specific.items()
                   if any(k in t.lower() for k in ("bulk", "cargo", "tanker", "container", "oil", "gas", "chemical"))}
    total_cargo = sum(cargo_types.values()) or 1
    specialization = [{"type": t, "pct": round(100 * c / total_cargo, 1)}
                      for t, c in sorted(cargo_types.items(), key=lambda x: -x[1])[:5]]

    # ── Speed profile ────────────────────────────────────────
    speeds = [(v.get("speed") or 0) for v in vessels]
    speed_profile = {
        "stationary": sum(1 for s in speeds if s < 0.5),
        "slow": sum(1 for s in speeds if 0.5 <= s < 3),
        "moderate": sum(1 for s in speeds if 3 <= s <= 8),
        "fast": sum(1 for s in speeds if s > 8),
    }

    # ── ETA accuracy ─────────────────────────────────────────
    now = time.time()
    with_eta = 0
    past_eta = 0
    future_eta = 0
    overdue_hours = []
    for v in vessels:
        eta = v.get("eta_epoch")
        if eta and isinstance(eta, (int, float)) and eta > 1000000:
            with_eta += 1
            diff_h = (eta - now) / 3600
            if diff_h < 0:
                past_eta += 1
                overdue_hours.append(abs(diff_h))
            else:
                future_eta += 1

    eta_analysis = {
        "vessels_with_eta": with_eta,
        "overdue": past_eta,
        "still_expected": future_eta,
        "avg_overdue_hours": round(sum(overdue_hours) / len(overdue_hours), 1) if overdue_hours else 0,
        "max_overdue_hours": round(max(overdue_hours), 1) if overdue_hours else 0,
    }

    # ── Destination patterns ─────────────────────────────────
    dests = Counter()
    for v in vessels:
        d = (v.get("destination") or "").strip().upper()
        if d and d != "CLASS B":
            dests[d] += 1
    dest_patterns = [{"destination": d, "count": c} for d, c in dests.most_common(10)]

    # ── Anchorage hotspots ───────────────────────────────────
    anchored = [v for v in vessels if v.get("state") == "ANCHORED" or (v.get("speed") or 0) < 0.5]
    anchor_points = [{"lat": v["lat"], "lon": v["lon"], "name": v.get("name", ""),
                      "mmsi": v.get("mmsi")}
                     for v in anchored if v.get("lat") and v.get("lon")]

    return {
        "flag_distribution": flag_dist,
        "vessel_types": type_dist,
        "port_specialization": specialization,
        "speed_profile": speed_profile,
        "eta_analysis": eta_analysis,
        "destination_patterns": dest_patterns,
        "anchorage_points": anchor_points[:50],
    }


async def enrich_with_specs(vessels: list[dict], api_key: str, max_vessels: int = 20) -> dict:
    """Fetch vessel_info for specs: age, size, DWT, draught."""
    # Pick cargo-relevant vessels
    cargo_vessels = [v for v in vessels
                     if v.get("state") in ("ANCHORED", "BERTHED")
                     and v.get("ship_type") not in ("Tug", "Pilot Vessel", "Reference Point")][:max_vessels]

    specs = []
    async with httpx.AsyncClient() as client:
        for v in cargo_vessels:
            mmsi = v.get("mmsi")
            if not mmsi:
                continue
            try:
                resp = await client.get(
                    "https://api.datalastic.com/api/v0/vessel_info",
                    params={"api-key": api_key, "mmsi": mmsi},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                info = resp.json().get("data", {})
                if not info:
                    continue
                specs.append({**info, "_state": v.get("state")})
                await asyncio.sleep(0.15)
            except Exception:
                logging.debug("Failed to fetch vessel info for MMSI %s", mmsi, exc_info=True)
                continue

    if not specs:
        return {"fleet_age": {}, "size_classes": [], "cargo_estimate": {}}

    # ── Fleet age profile ────────────────────────────────────
    ages = []
    for s in specs:
        yb = s.get("year_built")
        if yb:
            try:
                age = datetime.now().year - int(yb)
                if 0 < age < 80:
                    ages.append(age)
            except (ValueError, TypeError):
                pass

    fleet_age = {}
    if ages:
        fleet_age = {
            "avg_age": round(sum(ages) / len(ages), 1),
            "newest": min(ages),
            "oldest": max(ages),
            "under_10": sum(1 for a in ages if a < 10),
            "10_to_20": sum(1 for a in ages if 10 <= a < 20),
            "over_20": sum(1 for a in ages if a >= 20),
            "vessels_with_data": len(ages),
        }

    # ── Vessel size classes ──────────────────────────────────
    def _classify_size(dwt, length):
        if dwt and dwt > 200000:
            return "VLCC / VLOC"
        if dwt and dwt > 100000:
            return "Capesize"
        if dwt and dwt > 65000:
            return "Panamax"
        if dwt and dwt > 50000:
            return "Supramax"
        if dwt and dwt > 40000:
            return "Handymax"
        if dwt and dwt > 15000:
            return "Handysize"
        if length and length > 100:
            return "Coaster (large)"
        return "Small vessel"

    size_counter = Counter()
    for s in specs:
        def _n(v):
            try:
                return float(v) if v else 0
            except (TypeError, ValueError):
                return 0
        dwt = _n(s.get("deadweight"))
        length = _n(s.get("length"))
        cls = _classify_size(dwt, length)
        size_counter[cls] += 1
    size_classes = [{"class": c, "count": n} for c, n in size_counter.most_common()]

    # ── Cargo estimation ─────────────────────────────────────
    total_dwt = 0
    total_cargo = 0
    total_teu = 0
    load_pcts = []
    cargo_by_type = Counter()
    dwt_by_type = Counter()

    for s in specs:
        def _n(v):
            try:
                return float(v) if v else 0
            except (TypeError, ValueError):
                return 0

        dwt = _n(s.get("deadweight"))
        davg = _n(s.get("draught_avg"))
        dmax = _n(s.get("draught_max"))
        teu = int(_n(s.get("teu")))
        vtype = s.get("type_specific") or s.get("type") or "Unknown"

        total_dwt += dwt
        total_teu += teu
        dwt_by_type[vtype] += dwt

        if dmax > 0 and davg > 0:
            load = min(davg / dmax, 1.0)
            load_pcts.append(load)
            est = dwt * load
            total_cargo += est
            cargo_by_type[vtype] += est

    cargo_estimate = {
        "total_dwt": round(total_dwt),
        "total_est_cargo_tonnes": round(total_cargo),
        "total_teu": total_teu,
        "avg_load_pct": round(sum(load_pcts) / len(load_pcts) * 100, 1) if load_pcts else None,
        "vessels_analyzed": len(specs),
        "by_type": [
            {"type": t, "est_cargo": round(c), "dwt": round(dwt_by_type[t]),
             "count": sum(1 for s in specs if (s.get("type_specific") or s.get("type")) == t)}
            for t, c in sorted(cargo_by_type.items(), key=lambda x: -x[1])
        ],
    }

    return {
        "fleet_age": fleet_age,
        "size_classes": size_classes,
        "cargo_estimate": cargo_estimate,
    }


async def trace_origins(vessels: list[dict], api_key: str, port_def: dict, max_vessels: int = 8) -> list[dict]:
    """Fetch vessel_history to trace where ships came from."""
    cargo_vessels = [v for v in vessels
                     if v.get("ship_type") in ("Cargo", "Tanker")
                     and v.get("state") in ("ANCHORED", "BERTHED")][:max_vessels]

    origins = []
    async with httpx.AsyncClient() as client:
        for v in cargo_vessels:
            mmsi = v.get("mmsi")
            if not mmsi:
                continue
            try:
                resp = await client.get(
                    "https://api.datalastic.com/api/v0/vessel_history",
                    params={"api-key": api_key, "mmsi": mmsi, "days": 30},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", {})
                positions = data.get("positions", [])
                if len(positions) < 10:
                    continue

                # Find the farthest point from port (likely origin)
                port_lat = port_def["lat"]
                port_lon = port_def["lon"]
                max_dist = 0
                origin_pos = None
                for p in positions:
                    dlat = p["lat"] - port_lat
                    dlon = p["lon"] - port_lon
                    dist = math.sqrt(dlat ** 2 + dlon ** 2)
                    if dist > max_dist:
                        max_dist = dist
                        origin_pos = p

                if origin_pos and max_dist > 1:  # at least ~60nm away
                    origins.append({
                        "name": data.get("name") or v.get("name"),
                        "type": data.get("type_specific") or data.get("type") or v.get("type"),
                        "mmsi": mmsi,
                        "origin_lat": round(origin_pos["lat"], 2),
                        "origin_lon": round(origin_pos["lon"], 2),
                        "origin_date": origin_pos.get("last_position_UTC"),
                        "origin_dest": origin_pos.get("destination", ""),
                        "current_lat": v.get("lat"),
                        "current_lon": v.get("lon"),
                    })
                await asyncio.sleep(0.2)
            except Exception:
                logging.debug("Failed to fetch vessel history for MMSI %s", mmsi, exc_info=True)
                continue

    return origins
