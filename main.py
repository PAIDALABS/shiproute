from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import searoute as sr
import psycopg2
import psycopg2.extras
import os
import asyncio
import logging
import time as _time

from ports_loader import PortsLoader
from congestion_engine import CongestionEngine
from ais_stream import run_ais_stream

# ── Database connection ───────────────────────────────────────────────────────

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "/tmp"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "dbname": os.getenv("DB_NAME", "vessel_records"),
    "user": os.getenv("DB_USER", os.environ.get("USER", "aditya")),
}

def get_db():
    return psycopg2.connect(**DB_CONFIG)

app = FastAPI(title="ShipRoute")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ports = PortsLoader()

# ── Live congestion engine ────────────────────────────────────────────────────
engine = CongestionEngine()
logging.basicConfig(level=logging.INFO)


# ── Port search ──────────────────────────────────────────────────────────────

@app.get("/api/ports/search")
def search_ports(q: str = Query(default="", min_length=0), limit: int = Query(default=10, le=50)):
    results = ports.search(q, limit=limit)
    return results


# ── Route calculation ─────────────────────────────────────────────────────────

class PointInput(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    port_id: Optional[str] = None


class RouteRequest(BaseModel):
    origin: PointInput
    destination: PointInput


def resolve_point(pt: PointInput) -> dict:
    """Return dict with name, lat, lon."""
    if pt.port_id:
        p = ports.get(pt.port_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"Port not found: {pt.port_id}")
        return {"name": p["name"], "lat": p["lat"], "lon": p["lon"]}
    if pt.lat is not None and pt.lon is not None:
        return {"name": f"{pt.lat:.4f}, {pt.lon:.4f}", "lat": pt.lat, "lon": pt.lon}
    raise HTTPException(status_code=422, detail="Provide either port_id or lat/lon coordinates")


@app.post("/api/route")
def calculate_route(req: RouteRequest):
    origin = resolve_point(req.origin)
    destination = resolve_point(req.destination)

    # searoute expects [lon, lat]
    try:
        route = sr.searoute(
            [origin["lon"], origin["lat"]],
            [destination["lon"], destination["lat"]],
            units="naut",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Route calculation failed: {e}")

    distance_nmi: float = route["properties"].get("length", 0)
    distance_km: float = distance_nmi * 1.852
    duration_hours: float = distance_nmi / 14.0  # 14-knot average

    return {
        "route": route,
        "distance_km": round(distance_km, 1),
        "distance_nmi": round(distance_nmi, 1),
        "duration_hours": round(duration_hours, 1),
        "origin": origin,
        "destination": destination,
    }


# ── Port Congestion (v1) ───────────────────────────────────────────────────────

@app.get("/api/congestion")
def get_all_congestion(min_vessels: int = Query(default=1)):
    """Return congestion metrics for all ports, sorted by severity."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                m.port_locode,
                p.name,
                p.country,
                p.lat,
                p.lon,
                m.total_vessels,
                m.vessels_anchored,
                m.vessels_berthed,
                m.vessels_approaching,
                ROUND(m.avg_anchor_wait_hrs, 1) as avg_anchor_wait_hrs,
                ROUND(m.avg_port_stay_hrs, 1) as avg_port_stay_hrs,
                m.congestion_score,
                m.congestion_level,
                to_timestamp(m.time_window_start) as data_from,
                to_timestamp(m.time_window_end) as data_to
            FROM port_congestion_metrics m
            JOIN ports p ON p.locode = m.port_locode
            WHERE m.total_vessels >= %s
            ORDER BY m.congestion_score DESC, m.vessels_anchored DESC
        """, (min_vessels,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"ports": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Live Port Congestion ──────────────────────────────────────────────────────

@app.get("/api/congestion/live")
def get_live_congestion():
    """Return live congestion summary for all monitored ports."""
    return {
        "ports": engine.get_all_ports_summary(),
        "stream_status": "connected" if engine.stream_connected else "connecting",
        "last_message_at": (
            _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(engine.last_message_at))
            if engine.last_message_at else None
        ),
    }


@app.get("/api/congestion/live/status")
def get_live_status():
    """Return AIS Stream connection health."""
    return engine.get_status()


@app.get("/api/congestion/live/{locode}")
def get_live_port_detail(locode: str):
    """Return live congestion detail for a specific port with vessel list."""
    detail = engine.get_port_detail(locode.upper())
    if not detail:
        raise HTTPException(status_code=404, detail=f"Port {locode} not monitored for live congestion")
    return detail


@app.get("/api/congestion/live/{locode}/cargo")
async def get_live_cargo(locode: str):
    """Return cargo estimates for vessels in port using Datalastic vessel specs."""
    from ais_stream import DATALASTIC_API_KEY
    import httpx

    locode_upper = locode.upper()
    port_detail = engine.get_port_detail(locode_upper)
    if not port_detail:
        raise HTTPException(status_code=404, detail=f"Port {locode} not monitored")

    vessels = port_detail.get("vessels", [])
    if not vessels or not DATALASTIC_API_KEY:
        return {"locode": locode_upper, "vessels": [], "summary": {}, "by_type": []}

    # Fetch specs for berthed + anchored vessels (up to 30)
    target_vessels = [
        v for v in vessels if v.get("state") in ("BERTHED", "ANCHORED")
    ][:30]

    cargo_vessels = []
    async with httpx.AsyncClient() as client:
        for v in target_vessels:
            mmsi = v.get("mmsi")
            if not mmsi:
                continue
            try:
                resp = await client.get(
                    "https://api.datalastic.com/api/v0/vessel_info",
                    params={"api-key": DATALASTIC_API_KEY, "mmsi": mmsi},
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                info = resp.json().get("data", {})
                if not info:
                    continue

                def _num(v):
                    try: return float(v) if v else 0
                    except (TypeError, ValueError): return 0

                dwt = _num(info.get("deadweight"))
                draught_avg = _num(info.get("draught_avg"))
                draught_max = _num(info.get("draught_max"))
                gt = _num(info.get("gross_tonnage"))
                teu = int(_num(info.get("teu")))
                liquid_gas = int(_num(info.get("liquid_gas")))

                # Estimate load percentage from draught
                if draught_max > 0 and draught_avg > 0:
                    load_pct = round(min(draught_avg / draught_max, 1.0) * 100, 1)
                else:
                    load_pct = None

                # Estimate cargo tonnage
                if dwt > 0 and load_pct is not None:
                    est_cargo_tonnes = round(dwt * load_pct / 100)
                else:
                    est_cargo_tonnes = None

                cargo_vessels.append({
                    "mmsi": mmsi,
                    "name": info.get("name") or v.get("name"),
                    "type": info.get("type"),
                    "type_specific": info.get("type_specific"),
                    "state": v.get("state"),
                    "country": info.get("country_name"),
                    "flag": info.get("country_iso"),
                    "imo": info.get("imo"),
                    "deadweight": dwt,
                    "gross_tonnage": gt,
                    "teu_capacity": teu,
                    "liquid_gas_capacity": liquid_gas,
                    "length": info.get("length"),
                    "breadth": info.get("breadth"),
                    "draught_current": draught_avg,
                    "draught_max": draught_max,
                    "load_pct": load_pct,
                    "est_cargo_tonnes": est_cargo_tonnes,
                    "destination": v.get("name"),
                    "year_built": info.get("year_built"),
                })
                await asyncio.sleep(0.2)  # rate limit
            except Exception:
                continue

    # Aggregate by vessel type
    type_groups: dict[str, list] = {}
    for cv in cargo_vessels:
        vtype = cv["type_specific"] or cv["type"] or "Unknown"
        type_groups.setdefault(vtype, []).append(cv)

    by_type = []
    for vtype, group in sorted(type_groups.items(), key=lambda x: -len(x[1])):
        dwts = [g["deadweight"] for g in group if g["deadweight"]]
        cargos = [g["est_cargo_tonnes"] for g in group if g["est_cargo_tonnes"]]
        loads = [g["load_pct"] for g in group if g["load_pct"] is not None]
        by_type.append({
            "vessel_type": vtype,
            "count": len(group),
            "total_dwt": sum(dwts),
            "total_est_cargo": sum(cargos),
            "avg_load_pct": round(sum(loads) / len(loads), 1) if loads else None,
            "avg_dwt": round(sum(dwts) / len(dwts)) if dwts else 0,
        })

    # Port-level summary
    all_dwt = sum(cv["deadweight"] for cv in cargo_vessels if cv["deadweight"])
    all_cargo = sum(cv["est_cargo_tonnes"] for cv in cargo_vessels if cv["est_cargo_tonnes"])
    all_loads = [cv["load_pct"] for cv in cargo_vessels if cv["load_pct"] is not None]
    all_teu = sum(cv["teu_capacity"] for cv in cargo_vessels if cv["teu_capacity"])

    from congestion_engine import MONITORED_PORTS
    port_def = MONITORED_PORTS.get(locode_upper, {})

    return {
        "locode": locode_upper,
        "name": port_def.get("name", locode_upper),
        "vessels_analyzed": len(cargo_vessels),
        "summary": {
            "total_dwt": all_dwt,
            "total_est_cargo_tonnes": all_cargo,
            "total_teu_capacity": all_teu,
            "avg_load_pct": round(sum(all_loads) / len(all_loads), 1) if all_loads else None,
        },
        "by_type": by_type,
        "vessels": cargo_vessels,
    }


@app.get("/api/congestion/live/{locode}/turnaround")
async def get_live_turnaround(locode: str):
    """Return turnaround time stats by vessel type using Datalastic history."""
    from ais_stream import DATALASTIC_API_KEY
    import httpx

    locode_upper = locode.upper()
    port_detail = engine.get_port_detail(locode_upper)
    if not port_detail:
        raise HTTPException(status_code=404, detail=f"Port {locode} not monitored")

    port_def = None
    from congestion_engine import MONITORED_PORTS
    port_def = MONITORED_PORTS.get(locode_upper)

    vessels = port_detail.get("vessels", [])
    if not vessels or not DATALASTIC_API_KEY:
        # Fall back to in-memory data
        stats = engine.get_turnaround_stats(locode_upper)
        if stats:
            stats["source"] = "live"
        return stats

    # Fetch history for up to 20 vessels (to stay within API limits)
    # Prioritize anchored/berthed vessels (most interesting for turnaround)
    priority_vessels = sorted(
        [v for v in vessels if v.get("state") in ("ANCHORED", "BERTHED")],
        key=lambda v: v.get("dist_to_port_nm", 999),
    )[:20]

    visit_data = []

    async with httpx.AsyncClient() as client:
        for v in priority_vessels:
            mmsi = v.get("mmsi")
            if not mmsi:
                continue
            try:
                resp = await client.get(
                    "https://api.datalastic.com/api/v0/vessel_history",
                    params={"api-key": DATALASTIC_API_KEY, "mmsi": mmsi, "days": 30},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", {})
                positions = data.get("positions", [])
                if len(positions) < 2:
                    continue

                vtype = data.get("type_specific") or data.get("type") or v.get("ship_type") or "Unknown"
                name = data.get("name") or v.get("name") or str(mmsi)

                # Analyze positions (newest first) — find when vessel arrived in port area
                port_lat = port_def["lat"]
                port_lon = port_def["lon"]
                from congestion_engine import _haversine_nm
                inner_r = port_def["inner_radius_nm"]
                bbox = port_def["bbox"]

                anchor_seconds = 0
                berth_seconds = 0
                arrival_epoch = None
                prev_epoch = None

                # Process oldest-first
                for p in reversed(positions):
                    plat, plon = p["lat"], p["lon"]
                    epoch = p["last_position_epoch"]

                    # Check if in port bbox
                    if not (bbox[0][0] <= plat <= bbox[1][0] and bbox[0][1] <= plon <= bbox[1][1]):
                        # Outside port — reset if we haven't started counting
                        if arrival_epoch is not None and prev_epoch is not None:
                            break  # Left port, visit over
                        continue

                    if arrival_epoch is None:
                        arrival_epoch = epoch

                    speed = p.get("speed", 0) or 0
                    dist_nm = _haversine_nm(plat, plon, port_lat, port_lon)

                    if prev_epoch is not None:
                        dt = epoch - prev_epoch
                        if dt > 0 and dt < 86400:  # skip gaps > 24h
                            if speed < 0.3 and dist_nm <= inner_r:
                                berth_seconds += dt
                            elif speed < 0.5:
                                anchor_seconds += dt

                    prev_epoch = epoch

                if arrival_epoch is None:
                    continue

                last_epoch = positions[0]["last_position_epoch"]
                total_hours = (last_epoch - arrival_epoch) / 3600.0

                if total_hours < 0.5:
                    continue

                visit_data.append({
                    "mmsi": mmsi,
                    "name": name,
                    "vessel_type": vtype,
                    "total_hours": round(total_hours, 1),
                    "anchor_hours": round(anchor_seconds / 3600, 1),
                    "berth_hours": round(berth_seconds / 3600, 1),
                })

            except Exception:
                continue

    if not visit_data:
        stats = engine.get_turnaround_stats(locode_upper)
        if stats:
            stats["source"] = "live"
        return stats

    # Aggregate by vessel type
    type_groups: dict[str, list] = {}
    for vd in visit_data:
        type_groups.setdefault(vd["vessel_type"], []).append(vd)

    type_stats = []
    for vtype, visits in sorted(type_groups.items(), key=lambda x: -len(x[1])):
        n = len(visits)
        type_stats.append({
            "vessel_type": vtype,
            "count": n,
            "avg_total_hours": round(sum(v["total_hours"] for v in visits) / n, 1),
            "avg_anchor_hours": round(sum(v["anchor_hours"] for v in visits) / n, 1),
            "avg_berth_hours": round(sum(v["berth_hours"] for v in visits) / n, 1),
            "max_total_hours": round(max(v["total_hours"] for v in visits), 1),
            "max_anchor_hours": round(max(v["anchor_hours"] for v in visits), 1),
            "min_total_hours": round(min(v["total_hours"] for v in visits), 1),
        })

    all_total = [v["total_hours"] for v in visit_data]
    all_anchor = [v["anchor_hours"] for v in visit_data]
    all_berth = [v["berth_hours"] for v in visit_data]

    return {
        "locode": locode_upper,
        "name": port_def["name"],
        "source": "datalastic_history",
        "overall": {
            "total_visits": len(visit_data),
            "avg_turnaround_hours": round(sum(all_total) / len(all_total), 1),
            "avg_wait_hours": round(sum(all_anchor) / len(all_anchor), 1),
            "avg_berth_hours": round(sum(all_berth) / len(all_berth), 1),
        },
        "by_vessel_type": type_stats,
    }


# ── Port Congestion v2 ────────────────────────────────────────────────────────
#
# NOTE: These routes MUST be registered before /api/congestion/{locode} so that
# FastAPI does not match the literal path segment "v2" as a locode parameter.

_CONGESTION_V2_SQL = """
WITH time_series AS (
    SELECT port_locode, epoch,
        COUNT(DISTINCT imo) FILTER (WHERE state = 'ANCHORED') as anch,
        COUNT(DISTINCT imo) FILTER (WHERE state = 'BERTHED') as bert,
        COUNT(DISTINCT imo) FILTER (WHERE state = 'APPROACHING') as appr
    FROM vessel_port_states
    GROUP BY port_locode, epoch
),
peak_stats AS (
    SELECT port_locode,
        MAX(anch) as peak_anchored,
        MAX(bert) as peak_berthed,
        ROUND(AVG(anch)::numeric, 1) as avg_concurrent_anchored,
        ROUND(AVG(bert)::numeric, 1) as avg_concurrent_berthed,
        SUM(anch) as total_anchor_pings
    FROM time_series
    GROUP BY port_locode
),
transitions AS (
    SELECT port_locode,
        COUNT(*) FILTER (WHERE actual_wait_hrs > 0 AND actual_wait_hrs < 500) as vessels_transitioned,
        ROUND(AVG(actual_wait_hrs) FILTER (WHERE actual_wait_hrs > 0 AND actual_wait_hrs < 500)::numeric, 1) as avg_actual_wait_hrs
    FROM (
        SELECT port_locode, imo,
            (MIN(epoch) FILTER (WHERE state = 'BERTHED') - MIN(epoch) FILTER (WHERE state = 'ANCHORED'))::numeric / 3600.0 as actual_wait_hrs
        FROM vessel_port_states
        GROUP BY port_locode, imo
        HAVING MIN(epoch) FILTER (WHERE state = 'ANCHORED') IS NOT NULL
           AND MIN(epoch) FILTER (WHERE state = 'BERTHED') IS NOT NULL
    ) t
    GROUP BY port_locode
)
SELECT
    p.locode as port_locode,
    p.name,
    p.country,
    p.lat,
    p.lon,
    COALESCE(ps.peak_anchored, 0) as peak_anchored,
    COALESCE(ps.peak_berthed, 0) as peak_berthed,
    COALESCE(ps.avg_concurrent_anchored, 0) as avg_concurrent_anchored,
    COALESCE(t.avg_actual_wait_hrs, 0) as avg_actual_wait_hrs,
    COALESCE(t.vessels_transitioned, 0) as vessels_transitioned,
    LEAST(100, ROUND(
        LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
        + CASE
            WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30
            WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20
            WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10
            ELSE 0 END
        + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
    , 1)) as congestion_score,
    CASE
        WHEN LEAST(100, ROUND(
            LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
            + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
            + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
        , 1)) >= 75 THEN 'SEVERE'
        WHEN LEAST(100, ROUND(
            LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
            + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
            + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
        , 1)) >= 50 THEN 'HIGH'
        WHEN LEAST(100, ROUND(
            LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
            + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
            + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
        , 1)) >= 25 THEN 'MODERATE'
        ELSE 'LOW'
    END as congestion_level
FROM ports p
LEFT JOIN peak_stats ps ON ps.port_locode = p.locode
LEFT JOIN transitions t ON t.port_locode = p.locode
ORDER BY congestion_score DESC
"""


@app.get("/api/congestion/v2")
def get_all_congestion_v2():
    """Return v2 congestion metrics (peak concurrent counts) for all ports, sorted by severity."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_CONGESTION_V2_SQL)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"ports": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/congestion/v2/{locode}")
def get_port_congestion_v2(locode: str):
    """Return v2 congestion detail for a specific port, including vessel list and state breakdown."""
    locode_upper = locode.upper()
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Per-port v2 summary using the same CTE logic, filtered to one port
        cur.execute("""
            WITH time_series AS (
                SELECT port_locode, epoch,
                    COUNT(DISTINCT imo) FILTER (WHERE state = 'ANCHORED') as anch,
                    COUNT(DISTINCT imo) FILTER (WHERE state = 'BERTHED') as bert,
                    COUNT(DISTINCT imo) FILTER (WHERE state = 'APPROACHING') as appr
                FROM vessel_port_states
                WHERE port_locode = %s
                GROUP BY port_locode, epoch
            ),
            peak_stats AS (
                SELECT port_locode,
                    MAX(anch) as peak_anchored,
                    MAX(bert) as peak_berthed,
                    ROUND(AVG(anch)::numeric, 1) as avg_concurrent_anchored,
                    ROUND(AVG(bert)::numeric, 1) as avg_concurrent_berthed,
                    SUM(anch) as total_anchor_pings
                FROM time_series
                GROUP BY port_locode
            ),
            transitions AS (
                SELECT port_locode,
                    COUNT(*) FILTER (WHERE actual_wait_hrs > 0 AND actual_wait_hrs < 500) as vessels_transitioned,
                    ROUND(AVG(actual_wait_hrs) FILTER (WHERE actual_wait_hrs > 0 AND actual_wait_hrs < 500)::numeric, 1) as avg_actual_wait_hrs
                FROM (
                    SELECT port_locode, imo,
                        (MIN(epoch) FILTER (WHERE state = 'BERTHED') - MIN(epoch) FILTER (WHERE state = 'ANCHORED'))::numeric / 3600.0 as actual_wait_hrs
                    FROM vessel_port_states
                    WHERE port_locode = %s
                    GROUP BY port_locode, imo
                    HAVING MIN(epoch) FILTER (WHERE state = 'ANCHORED') IS NOT NULL
                       AND MIN(epoch) FILTER (WHERE state = 'BERTHED') IS NOT NULL
                ) t
                GROUP BY port_locode
            )
            SELECT
                p.locode as port_locode,
                p.name,
                p.country,
                p.lat,
                p.lon,
                COALESCE(ps.peak_anchored, 0) as peak_anchored,
                COALESCE(ps.peak_berthed, 0) as peak_berthed,
                COALESCE(ps.avg_concurrent_anchored, 0) as avg_concurrent_anchored,
                COALESCE(t.avg_actual_wait_hrs, 0) as avg_actual_wait_hrs,
                COALESCE(t.vessels_transitioned, 0) as vessels_transitioned,
                LEAST(100, ROUND(
                    LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
                    + CASE
                        WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30
                        WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20
                        WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10
                        ELSE 0 END
                    + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
                , 1)) as congestion_score,
                CASE
                    WHEN LEAST(100, ROUND(
                        LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
                        + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
                        + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
                    , 1)) >= 75 THEN 'SEVERE'
                    WHEN LEAST(100, ROUND(
                        LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
                        + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
                        + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
                    , 1)) >= 50 THEN 'HIGH'
                    WHEN LEAST(100, ROUND(
                        LEAST(50, (COALESCE(ps.peak_anchored, 0)::numeric / GREATEST(1, COALESCE(ps.peak_berthed, 1))) * 10)
                        + CASE WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 96 THEN 30 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 48 THEN 20 WHEN COALESCE(t.avg_actual_wait_hrs, 0) > 24 THEN 10 ELSE 0 END
                        + LEAST(20, COALESCE(ps.peak_anchored, 0) * 0.4)
                    , 1)) >= 25 THEN 'MODERATE'
                    ELSE 'LOW'
                END as congestion_level
            FROM ports p
            LEFT JOIN peak_stats ps ON ps.port_locode = p.locode
            LEFT JOIN transitions t ON t.port_locode = p.locode
            WHERE p.locode = %s
        """, (locode_upper, locode_upper, locode_upper))
        summary = cur.fetchone()
        if not summary:
            raise HTTPException(status_code=404, detail=f"Port {locode} not found or no data")

        # Last known state per vessel at this port (DISTINCT ON eliminates duplicates)
        cur.execute("""
            SELECT DISTINCT ON (imo)
                imo, mmsi, name, vessel_type, state,
                lat, lon, speed, course,
                ROUND(dist_to_port_m::numeric / 1852, 1) as dist_nm,
                dest_port_unlocode, dep_port_unlocode,
                to_timestamp(epoch) as last_seen
            FROM vessel_port_states
            WHERE port_locode = %s AND imo > 0
            ORDER BY imo, epoch DESC
        """, (locode_upper,))
        vessels_raw = cur.fetchall()
        # Sort by state priority then distance
        state_order = {'ANCHORED': 0, 'BERTHED': 1, 'MANEUVERING': 2, 'APPROACHING': 3, 'TRANSITING': 4}
        vessels = sorted(vessels_raw, key=lambda v: (state_order.get(v['state'], 9), v['dist_nm'] or 999))[:200]

        # State + vessel_type breakdown
        cur.execute("""
            SELECT vessel_type, state, COUNT(DISTINCT imo) as count
            FROM vessel_port_states
            WHERE port_locode = %s
            GROUP BY vessel_type, state
            ORDER BY count DESC
        """, (locode_upper,))
        breakdown = cur.fetchall()

        cur.close()
        conn.close()
        return {
            "summary": dict(summary),
            "vessels": [dict(v) for v in vessels],
            "breakdown": [dict(b) for b in breakdown],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Port Congestion (v1) per-port ─────────────────────────────────────────────

@app.get("/api/congestion/{locode}")
def get_port_congestion(locode: str):
    """Return detailed congestion data for a specific port."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Port congestion summary
        cur.execute("""
            SELECT
                m.*,
                p.name, p.country, p.lat, p.lon,
                to_timestamp(m.time_window_start) as data_from,
                to_timestamp(m.time_window_end) as data_to
            FROM port_congestion_metrics m
            JOIN ports p ON p.locode = m.port_locode
            WHERE m.port_locode = %s
        """, (locode.upper(),))
        summary = cur.fetchone()
        if not summary:
            raise HTTPException(status_code=404, detail=f"Port {locode} not found or no data")

        # Vessels currently in port zone by state
        cur.execute("""
            SELECT
                vps.imo,
                vps.name,
                vps.vessel_type,
                vps.state,
                vps.lat,
                vps.lon,
                vps.speed,
                ROUND(vps.dist_to_port_m::numeric / 1852, 1) as dist_nm,
                vps.dest_port_unlocode,
                vps.dep_port_unlocode,
                to_timestamp(vps.epoch) as last_seen
            FROM vessel_port_states vps
            WHERE vps.port_locode = %s
              AND vps.epoch = (
                  SELECT MAX(epoch) FROM vessel_port_states
                  WHERE port_locode = %s AND imo = vps.imo
              )
            ORDER BY vps.state, vps.dist_to_port_m
            LIMIT 100
        """, (locode.upper(), locode.upper()))
        vessels = cur.fetchall()

        # Vessel type breakdown
        cur.execute("""
            SELECT vessel_type, state, COUNT(DISTINCT imo) as count
            FROM vessel_port_states
            WHERE port_locode = %s
            GROUP BY vessel_type, state
            ORDER BY count DESC
        """, (locode.upper(),))
        breakdown = cur.fetchall()

        cur.close()
        conn.close()
        return {
            "summary": dict(summary),
            "vessels": [dict(v) for v in vessels],
            "breakdown": [dict(b) for b in breakdown],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/congestion/{locode}/timeline")
def get_port_timeline(locode: str):
    """Return anchor queue depth over time for a port."""
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT
                to_timestamp(epoch) as time,
                COUNT(DISTINCT imo) FILTER (WHERE state = 'ANCHORED') as anchored,
                COUNT(DISTINCT imo) FILTER (WHERE state = 'BERTHED') as berthed,
                COUNT(DISTINCT imo) FILTER (WHERE state = 'APPROACHING') as approaching,
                COUNT(DISTINCT imo) as total
            FROM vessel_port_states
            WHERE port_locode = %s
            GROUP BY epoch
            ORDER BY epoch
        """, (locode.upper(),))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {"timeline": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── AIS Stream background task ────────────────────────────────────────────────

@app.on_event("startup")
async def start_ais_stream():
    asyncio.create_task(run_ais_stream(engine))


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
