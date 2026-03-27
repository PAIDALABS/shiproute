"""
Cargo Flow Analysis — estimates cargo throughput, en-route cargo,
and weekly patterns using Datalastic vessel data.
"""

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from congestion_engine import CongestionEngine, MONITORED_PORTS, _haversine_nm

DATALASTIC_API_KEY = os.getenv("DATALASTIC_API_KEY", "")
SNAPSHOT_DIR = Path("data/cargo_snapshots")


# ── Daily snapshot persistence ────────────────────────────────────────────────

def save_daily_snapshot(engine: CongestionEngine):
    """Save current cargo state to disk for historical analysis."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = SNAPSHOT_DIR / f"{today}.json"

    snapshot = {}
    for locode, port_def in MONITORED_PORTS.items():
        metrics = engine.get_port_metrics(locode)
        vessels = engine._port_vessels.get(locode, {})

        # Compute cargo estimate from in-memory data
        total_dwt = 0
        cargo_count = 0
        for v in vessels.values():
            if v.get("ship_type") in ("Cargo", "Tanker") and v.get("state") in ("ANCHORED", "BERTHED"):
                cargo_count += 1
                # We don't have DWT in engine, so count vessels as proxy
        snapshot[locode] = {
            "name": port_def["name"],
            "total_vessels": metrics["total_vessels"],
            "anchored": metrics["anchored_count"],
            "berthed": metrics["berthed_count"],
            "cargo_vessels": cargo_count,
            "congestion_score": metrics["congestion_score"],
            "timestamp": time.time(),
        }

    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2)
    return path


def load_historical_snapshots(days: int = 365) -> list[dict]:
    """Load all available daily snapshots."""
    if not SNAPSHOT_DIR.exists():
        return []

    snapshots = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    for path in sorted(SNAPSHOT_DIR.glob("*.json")):
        try:
            date_str = path.stem  # YYYY-MM-DD
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt < cutoff:
                continue
            with open(path) as f:
                data = json.load(f)
            snapshots.append({"date": date_str, "data": data})
        except (ValueError, json.JSONDecodeError):
            continue

    return snapshots


# ── Cargo flow computation ────────────────────────────────────────────────────

async def compute_cargo_flow(
    engine: CongestionEngine,
    locode: str,
    api_key: str,
) -> dict:
    """Compute comprehensive cargo flow for a single port."""
    port_def = MONITORED_PORTS.get(locode)
    if not port_def:
        return {}

    vessels = list(engine._port_vessels.get(locode, {}).values())
    now = time.time()

    # ── Current cargo at port ────────────────────────────────
    anchored_cargo = [v for v in vessels if v.get("state") == "ANCHORED"
                      and v.get("ship_type") in ("Cargo", "Tanker", "Tanker - Hazard B",
                                                   "Cargo - Hazard A (Major)")]
    berthed_cargo = [v for v in vessels if v.get("state") == "BERTHED"
                     and v.get("ship_type") in ("Cargo", "Tanker", "Tanker - Hazard B",
                                                  "Cargo - Hazard A (Major)")]
    approaching = [v for v in vessels if v.get("state") == "APPROACHING"
                   and v.get("ship_type") in ("Cargo", "Tanker", "Tanker - Hazard B",
                                                "Cargo - Hazard A (Major)")]

    # Fetch vessel_info for DWT data (up to 20 vessels)
    target_mmsis = [v["mmsi"] for v in (berthed_cargo + anchored_cargo + approaching)[:20]]

    vessel_specs = {}
    if api_key and target_mmsis:
        async with httpx.AsyncClient() as client:
            for mmsi in target_mmsis:
                try:
                    resp = await client.get(
                        "https://api.datalastic.com/api/v0/vessel_info",
                        params={"api-key": api_key, "mmsi": mmsi},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        info = resp.json().get("data", {})
                        if info:
                            def _n(val):
                                try: return float(val) if val else 0
                                except (TypeError, ValueError): return 0
                            dwt = _n(info.get("deadweight"))
                            davg = _n(info.get("draught_avg"))
                            dmax = _n(info.get("draught_max"))
                            load = min(davg / dmax, 1.0) if dmax > 0 and davg > 0 else 0.8
                            vessel_specs[mmsi] = {
                                "dwt": dwt,
                                "est_cargo": round(dwt * load),
                                "load_pct": round(load * 100, 1),
                                "type": info.get("type_specific") or info.get("type"),
                            }
                    await asyncio.sleep(0.15)
                except Exception:
                    continue

    def _sum_cargo(vessel_list):
        total = 0
        count = 0
        for v in vessel_list:
            spec = vessel_specs.get(v["mmsi"])
            if spec:
                total += spec["est_cargo"]
                count += 1
        return total, count

    berthed_tonnes, berthed_with_spec = _sum_cargo(berthed_cargo)
    anchored_tonnes, anchored_with_spec = _sum_cargo(anchored_cargo)
    approaching_tonnes, approaching_with_spec = _sum_cargo(approaching)

    current = {
        "at_berth": {
            "vessels": len(berthed_cargo),
            "est_cargo_tonnes": berthed_tonnes,
            "vessels_with_data": berthed_with_spec,
        },
        "at_anchor": {
            "vessels": len(anchored_cargo),
            "est_cargo_tonnes": anchored_tonnes,
            "vessels_with_data": anchored_with_spec,
        },
        "approaching": {
            "vessels": len(approaching),
            "est_cargo_tonnes": approaching_tonnes,
            "vessels_with_data": approaching_with_spec,
        },
        "total_at_port_tonnes": berthed_tonnes + anchored_tonnes,
        "total_enroute_tonnes": approaching_tonnes,
    }

    # ── 30-day arrival pattern from vessel history ───────────
    # For vessels currently at port, trace when they arrived
    weekly_arrivals = defaultdict(lambda: {"count": 0, "est_tonnes": 0})

    cargo_at_port = [v for v in (berthed_cargo + anchored_cargo) if v["mmsi"] in vessel_specs][:12]

    if api_key and cargo_at_port:
        async with httpx.AsyncClient() as client:
            for v in cargo_at_port:
                try:
                    resp = await client.get(
                        "https://api.datalastic.com/api/v0/vessel_history",
                        params={"api-key": api_key, "mmsi": v["mmsi"], "days": 30},
                        timeout=15,
                    )
                    if resp.status_code != 200:
                        continue
                    positions = resp.json().get("data", {}).get("positions", [])
                    if len(positions) < 5:
                        continue

                    # Find arrival: when vessel first entered port bbox
                    bbox = port_def["bbox"]
                    arrival_epoch = None
                    for p in reversed(positions):  # oldest first
                        in_bbox = (bbox[0][0] <= p["lat"] <= bbox[1][0]
                                   and bbox[0][1] <= p["lon"] <= bbox[1][1])
                        if in_bbox and arrival_epoch is None:
                            arrival_epoch = p["last_position_epoch"]
                            break

                    if arrival_epoch:
                        dt = datetime.fromtimestamp(arrival_epoch, tz=timezone.utc)
                        # Week number (ISO)
                        week_key = dt.strftime("%Y-W%W")
                        spec = vessel_specs.get(v["mmsi"], {})
                        weekly_arrivals[week_key]["count"] += 1
                        weekly_arrivals[week_key]["est_tonnes"] += spec.get("est_cargo", 0)

                    await asyncio.sleep(0.2)
                except Exception:
                    continue

    # Sort weeks chronologically
    weekly_pattern = [
        {"week": k, "arrivals": v["count"], "est_tonnes": v["est_tonnes"]}
        for k, v in sorted(weekly_arrivals.items())
    ]

    # ── Historical snapshots ─────────────────────────────────
    snapshots = load_historical_snapshots(365)
    historical = []
    for snap in snapshots:
        port_snap = snap["data"].get(locode, {})
        if port_snap:
            historical.append({
                "date": snap["date"],
                "total_vessels": port_snap.get("total_vessels", 0),
                "cargo_vessels": port_snap.get("cargo_vessels", 0),
                "congestion_score": port_snap.get("congestion_score", 0),
            })

    return {
        "locode": locode,
        "name": port_def["name"],
        "current": current,
        "weekly_arrivals_30d": weekly_pattern,
        "historical_daily": historical,
        "snapshot_days_available": len(historical),
    }


async def compute_all_ports_flow(engine: CongestionEngine, api_key: str) -> dict:
    """Compute cargo flow summary for all monitored ports."""
    results = []
    for locode in MONITORED_PORTS:
        flow = await compute_cargo_flow(engine, locode, api_key)
        if flow:
            results.append(flow)
    return {
        "ports": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
