"""Market intelligence — cargo trends, Africa corridor, port comparison."""

import time
from collections import defaultdict
from congestion_engine import CongestionEngine, MONITORED_PORTS
from vessel_finder import AFRICA_KEYWORDS, BAGGED_CARGO_TYPES


def get_market_overview(engine: CongestionEngine) -> dict:
    """Cross-port comparison of current activity."""
    ports = []
    for locode, port_def in MONITORED_PORTS.items():
        metrics = engine.get_port_metrics(locode)
        vessels = engine.get_vessels_snapshot(locode)

        # Count by type
        type_counts = defaultdict(int)
        africa_count = 0
        for v in vessels.values():
            t = v.get("ship_type") or "Unknown"
            type_counts[t] += 1
            dest = (v.get("destination") or "").upper()
            if any(kw in dest for kw in AFRICA_KEYWORDS):
                africa_count += 1

        ports.append({
            "locode": locode,
            "name": port_def["name"],
            "country": port_def["country"],
            "total_vessels": metrics["total_vessels"],
            "anchored": metrics["anchored_count"],
            "berthed": metrics["berthed_count"],
            "congestion_score": metrics["congestion_score"],
            "severity": metrics["severity"],
            "dominant_type": max(type_counts.items(), key=lambda x: x[1])[0] if len(type_counts) > 0 else "N/A",
            "africa_trade_vessels": africa_count,
            "vessel_types": dict(type_counts),
        })

    ports.sort(key=lambda p: -p["total_vessels"])

    total_vessels = sum(p["total_vessels"] for p in ports)
    total_africa = sum(p["africa_trade_vessels"] for p in ports)
    busiest = ports[0] if ports else None
    quietest = min(ports, key=lambda p: p["total_vessels"]) if ports else None

    return {
        "ports": ports,
        "summary": {
            "total_vessels_all_ports": total_vessels,
            "total_africa_trade": total_africa,
            "busiest_port": busiest["name"] if busiest else "N/A",
            "quietest_port": quietest["name"] if quietest else "N/A",
            "ports_monitored": len(ports),
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def get_africa_corridor(engine: CongestionEngine) -> dict:
    """Analyze India ↔ East Africa trade corridor."""
    india_ports = ["INKAN", "INKAK", "INVTZ", "INNSA", "INMUN"]
    africa_ports = ["MGTNR", "MGTLE", "KEMBA", "TZDAR"]

    india_keywords = [
        "KANDLA", "KAKINADA", "VIZAG", "VISHAKHAPATNAM", "INDIA",
        "MUMBAI", "MUNDRA", "NHAVA", "JNPT", "CHENNAI", "HALDIA",
        "PARADIP", "TUTICORIN", "KOLKATA", "KOCHI",
    ]

    corridor_vessels = []

    for locode in india_ports + africa_ports:
        vessels = engine.get_vessels_snapshot(locode)
        port_def = MONITORED_PORTS.get(locode, {})

        for v in vessels.values():
            dest = (v.get("destination") or "").upper()
            vtype = v.get("type_specific") or v.get("ship_type") or ""

            is_bagged_type = any(bt.lower() in vtype.lower() for bt in BAGGED_CARGO_TYPES)
            is_africa_dest = any(kw in dest for kw in AFRICA_KEYWORDS)

            is_india_dest = any(kw in dest for kw in india_keywords)
            if is_bagged_type and (
                (locode in india_ports and is_africa_dest)
                or (locode in africa_ports and is_india_dest)
                or (locode in africa_ports and locode in india_ports)  # shouldn't happen but safe
            ):
                corridor_vessels.append({
                    "mmsi": v["mmsi"],
                    "name": v.get("name"),
                    "type": vtype,
                    "state": v["state"],
                    "port": port_def.get("name", locode),
                    "port_locode": locode,
                    "destination": v.get("destination"),
                    "flag": v.get("country_iso"),
                    "lat": v["lat"],
                    "lon": v["lon"],
                })

    return {
        "corridor": "India ↔ East Africa",
        "total_vessels": len(corridor_vessels),
        "vessels": corridor_vessels,
        "by_port": {
            locode: len([v for v in corridor_vessels if v["port_locode"] == locode])
            for locode in india_ports + africa_ports
        },
        "by_type": dict(defaultdict(int, {
            vtype: len([v for v in corridor_vessels if v["type"] == vtype])
            for vtype in set(v["type"] for v in corridor_vessels)
        })),
    }
