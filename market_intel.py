"""Market intelligence — cargo trends, Africa corridor, port comparison."""

import time
from collections import defaultdict, Counter
from congestion_engine import CongestionEngine, MONITORED_PORTS
from vessel_finder import AFRICA_KEYWORDS, BAGGED_CARGO_TYPES
import global_tracker


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
    """Analyze India ↔ East Africa trade corridor.

    Searches both the 12-port AIS snapshots AND the global vessel tracker
    (108K+ vessels) to capture ships in transit across the Indian Ocean.
    """
    india_ports = ["INKAN", "INKAK", "INVTZ", "INNSA", "INMUN"]
    africa_ports = ["MGTNR", "MGTLE", "KEMBA", "TZDAR"]

    india_keywords = [
        "KANDLA", "KAKINADA", "VIZAG", "VISHAKHAPATNAM", "INDIA",
        "MUMBAI", "MUNDRA", "NHAVA", "JNPT", "CHENNAI", "HALDIA",
        "PARADIP", "TUTICORIN", "KOLKATA", "KOCHI",
    ]

    seen_mmsi: set = set()
    corridor_vessels = []

    def _add_vessel(mmsi, name, vtype, state, port_label, port_locode, dest, flag, lat, lon):
        key = str(mmsi)
        if key in seen_mmsi:
            return
        seen_mmsi.add(key)
        corridor_vessels.append({
            "mmsi": mmsi,
            "name": name,
            "type": vtype,
            "state": state,
            "port": port_label,
            "port_locode": port_locode,
            "destination": dest,
            "flag": flag,
            "lat": lat,
            "lon": lon,
        })

    # ── Search monitored-port snapshots ─────────────────────────────────────
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
            ):
                _add_vessel(
                    v["mmsi"], v.get("name"), vtype, v["state"],
                    port_def.get("name", locode), locode,
                    v.get("destination"), v.get("country_iso"),
                    v["lat"], v["lon"],
                )

    # ── Search global tracker (vessels in transit across Indian Ocean) ───────
    for v in global_tracker._global_vessels.values():
        lat = v.get("lat")
        lon = v.get("lon")
        if lat is None or lon is None:
            continue

        dest = (v.get("destination") or "").upper()
        if dest in ("CLASS B", "0", "", "NONE"):
            continue

        vtype = (v.get("type_specific") or v.get("type") or "").strip()
        is_bagged_type = any(bt.lower() in vtype.lower() for bt in BAGGED_CARGO_TYPES)
        if not is_bagged_type:
            continue

        is_africa_dest = any(kw in dest for kw in AFRICA_KEYWORDS)
        is_india_dest = any(kw in dest for kw in india_keywords)

        # India area: bounding box roughly 5–30°N, 60–92°E
        in_india_area = (5 <= lat <= 30) and (60 <= lon <= 92)
        # East Africa / Indian Ocean: bounding box -30–12°N, 30–80°E
        in_africa_area = (-30 <= lat <= 12) and (30 <= lon <= 80)

        if (is_africa_dest and in_india_area) or (is_india_dest and in_africa_area):
            _add_vessel(
                v["mmsi"], v.get("name"), vtype, "AT SEA",
                "Indian Ocean", "",
                v.get("destination"), v.get("country_iso"),
                lat, lon,
            )

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
        "data_note": f"Includes vessels at monitored ports and {len(global_tracker._global_vessels):,} globally tracked vessels",
    }


def get_global_market_overview(top_n: int = 100) -> dict:
    """Global port rankings by inbound vessel count from 86K vessel tracker.

    Uses destination field from all globally tracked vessels to rank ports
    by traffic. Resolves destination strings to structured port records
    via port_database.
    """
    from port_database import resolve_destination, JUNK_DESTINATIONS

    vessels = list(global_tracker._global_vessels.values())
    vessels_with_pos = [v for v in vessels if v.get("lat") is not None]

    dest_counts: dict[str, int] = {}
    dest_to_port: dict[str, dict] = {}
    dest_type_counts: dict[str, Counter] = {}
    vessels_with_dest = 0

    for v in vessels_with_pos:
        raw_dest = (v.get("destination") or "").strip().upper()
        if not raw_dest or raw_dest in JUNK_DESTINATIONS:
            continue
        # Skip obviously junk multi-word non-port strings
        if any(raw_dest.startswith(j) for j in ("FOR ", "WAIT", "CLASS")):
            continue

        vessels_with_dest += 1
        port = resolve_destination(raw_dest)
        key = port["unlocode"] if port else raw_dest

        dest_counts[key] = dest_counts.get(key, 0) + 1
        if port and key not in dest_to_port:
            dest_to_port[key] = port
        if key not in dest_type_counts:
            dest_type_counts[key] = Counter()
        vtype = v.get("type_specific") or v.get("type") or "Unknown"
        dest_type_counts[key][vtype] += 1

    # Build sorted port rows
    top_items = sorted(dest_counts.items(), key=lambda x: -x[1])[:top_n]
    port_rows = []
    for key, count in top_items:
        p = dest_to_port.get(key)
        types = dest_type_counts.get(key, Counter())
        port_rows.append({
            "key": key,
            "name": p["port_name"] if p else key,
            "country": p["country_name"] if p else "",
            "country_iso": p["country_iso"] if p else "",
            "unlocode": p["unlocode"] if p else "",
            "lat": p["lat"] if p else None,
            "lon": p["lon"] if p else None,
            "inbound_vessels": count,
            "top_types": dict(types.most_common(3)),
            "resolved": p is not None,
        })

    # Overall type distribution
    type_counts = Counter(
        v.get("type_specific") or v.get("type") or "Unknown"
        for v in vessels_with_pos
    )

    return {
        "ports": port_rows,
        "summary": {
            "total_in_database": len(vessels),
            "vessels_with_position": len(vessels_with_pos),
            "vessels_with_destination": vessels_with_dest,
            "active_destinations": len(dest_counts),
            "ports_shown": len(port_rows),
        },
        "vessel_types": dict(type_counts.most_common(10)),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
