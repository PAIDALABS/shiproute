"""
Global vessel tracker — downloads vessel database and polls live positions.

Uses Datalastic API:
- vessel_find: paginated vessel database (500/page, static specs)
- vessel_bulk: live positions for up to 100 vessels per call (lat, lon, speed, dest, ETA)
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DATALASTIC_API_KEY = os.getenv("DATALASTIC_API_KEY", "")
VESSEL_DB_FILE = Path("data/vessel_database.json")
API_BASE = "https://api.datalastic.com/api/v0"

# Vessel types we care about for cargo/shipping intelligence
TRACKED_TYPES = ["Cargo", "Tanker"]

# In-memory store of all tracked vessels with live positions
_global_vessels: dict[str, dict] = {}  # mmsi -> vessel dict with live position
_last_refresh: float = 0
_db_loaded: bool = False


async def download_vessel_database(api_key: str = "", max_pages: int = 100) -> list[dict]:
    """Download full vessel database from Datalastic vessel_find endpoint.

    Paginates through all Cargo and Tanker vessels, saves to disk.
    Returns list of vessel specs (no live positions).
    """
    api_key = api_key or DATALASTIC_API_KEY
    if not api_key:
        logger.warning("No API key for vessel database download")
        return []

    all_vessels = []
    async with httpx.AsyncClient(timeout=30) as client:
        for vessel_type in TRACKED_TYPES:
            cursor = None
            page = 0
            while page < max_pages:
                params = {"api-key": api_key, "type": vessel_type}
                if cursor:
                    params["next"] = cursor

                try:
                    resp = await client.get(f"{API_BASE}/vessel_find", params=params)
                    if resp.status_code != 200:
                        logger.warning("vessel_find failed: HTTP %d", resp.status_code)
                        break

                    data = resp.json()
                    vessels = data.get("data", [])
                    if not vessels:
                        break

                    all_vessels.extend(vessels)
                    page += 1
                    cursor = data.get("meta", {}).get("next")

                    if not cursor:
                        break

                    logger.info("vessel_find %s page %d: %d vessels (total: %d)",
                               vessel_type, page, len(vessels), len(all_vessels))
                    await asyncio.sleep(0.3)  # rate limit courtesy

                except Exception:
                    logger.exception("vessel_find page %d failed", page)
                    break

    # Save to disk
    if all_vessels:
        VESSEL_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(VESSEL_DB_FILE, "w") as f:
            json.dump({
                "vessels": all_vessels,
                "count": len(all_vessels),
                "downloaded_at": time.time(),
                "types": TRACKED_TYPES,
            }, f)
        logger.info("Vessel database saved: %d vessels", len(all_vessels))

    return all_vessels


def load_vessel_database() -> list[dict]:
    """Load vessel database from disk."""
    if not VESSEL_DB_FILE.exists():
        return []
    try:
        with open(VESSEL_DB_FILE) as f:
            data = json.load(f)
        vessels = data.get("vessels", [])
        age_hours = (time.time() - data.get("downloaded_at", 0)) / 3600
        logger.info("Loaded vessel database: %d vessels (%.1f hours old)", len(vessels), age_hours)
        return vessels
    except Exception:
        logger.exception("Failed to load vessel database")
        return []


async def poll_live_positions(api_key: str = "", batch_size: int = 100) -> int:
    """Poll live positions for all vessels in the database using vessel_bulk.

    Sends batches of up to 100 MMSIs per API call.
    Updates _global_vessels in-memory store.
    Returns number of vessels with live positions.
    """
    global _last_refresh, _db_loaded

    api_key = api_key or DATALASTIC_API_KEY
    if not api_key:
        return 0

    # Load database if not loaded
    if not _db_loaded:
        db = load_vessel_database()
        if not db:
            logger.info("No vessel database found, downloading...")
            db = await download_vessel_database(api_key, max_pages=5)  # Start with 5 pages for quick boot
        for v in db:
            mmsi = v.get("mmsi")
            if mmsi:
                _global_vessels[str(mmsi)] = {
                    "mmsi": str(mmsi),
                    "name": v.get("name") or v.get("name_ais"),
                    "imo": v.get("imo"),
                    "type": v.get("type"),
                    "type_specific": v.get("type_specific"),
                    "country_iso": v.get("country_iso"),
                    "deadweight": v.get("deadweight"),
                    "year_built": v.get("year_built"),
                    "length": v.get("length"),
                    "breadth": v.get("breadth"),
                    # Live fields (to be updated by vessel_bulk)
                    "lat": None, "lon": None, "speed": None,
                    "destination": None, "eta_epoch": None,
                    "last_position_epoch": None,
                }
        _db_loaded = True
        logger.info("Database loaded: %d vessels to track", len(_global_vessels))

    # Poll live positions in batches
    mmsi_list = list(_global_vessels.keys())
    updated = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(mmsi_list), batch_size):
            batch = mmsi_list[i:i + batch_size]
            params = [("api-key", api_key)]
            for mmsi in batch:
                params.append(("mmsi", mmsi))

            try:
                resp = await client.get(f"{API_BASE}/vessel_bulk", params=params)
                if resp.status_code != 200:
                    logger.debug("vessel_bulk batch %d failed: HTTP %d", i // batch_size, resp.status_code)
                    continue

                data = resp.json()
                for v in data.get("data", {}).get("vessels", []):
                    mmsi = str(v.get("mmsi", ""))
                    if mmsi in _global_vessels:
                        _global_vessels[mmsi].update({
                            "lat": v.get("lat"),
                            "lon": v.get("lon"),
                            "speed": v.get("speed"),
                            "course": v.get("course"),
                            "destination": v.get("destination"),
                            "eta_epoch": v.get("eta_epoch"),
                            "eta_utc": v.get("eta_UTC"),
                            "last_position_epoch": v.get("last_position_epoch"),
                            "last_position_utc": v.get("last_position_UTC"),
                            "navigation_status": v.get("navigation_status"),
                        })
                        updated += 1

                await asyncio.sleep(0.2)  # rate limit

            except Exception:
                logger.debug("vessel_bulk batch %d error", i // batch_size, exc_info=True)
                continue

    _last_refresh = time.time()
    logger.info("Live positions updated: %d of %d vessels", updated, len(mmsi_list))
    return updated


def get_all_tracked_vessels() -> dict:
    """Return all tracked vessels with live positions."""
    vessels_with_pos = [v for v in _global_vessels.values() if v.get("lat") is not None]

    # Build destination summary
    dest_counts: dict[str, int] = {}
    for v in vessels_with_pos:
        dest = (v.get("destination") or "UNKNOWN").strip().upper()
        if dest and dest != "CLASS B":
            dest_counts[dest] = dest_counts.get(dest, 0) + 1

    # Top destinations
    top_dests = sorted(dest_counts.items(), key=lambda x: -x[1])[:20]

    return {
        "total_in_database": len(_global_vessels),
        "total_with_position": len(vessels_with_pos),
        "vessels": vessels_with_pos,
        "top_destinations": [{"destination": d, "count": c} for d, c in top_dests],
        "last_refresh": _last_refresh,
        "types_tracked": TRACKED_TYPES,
    }


def get_vessels_heading_to(port_name: str, port_locode: str = "") -> list[dict]:
    """Find all tracked vessels whose destination matches a port."""
    keywords = [port_name.upper()]
    if port_locode:
        keywords.append(port_locode.upper())

    matches = []
    for v in _global_vessels.values():
        if v.get("lat") is None:
            continue
        dest = (v.get("destination") or "").upper()
        if dest and any(kw in dest for kw in keywords):
            matches.append(v)

    return sorted(matches, key=lambda v: v.get("eta_epoch") or float("inf"))


async def run_global_tracker(api_key: str = ""):
    """Background loop: poll live positions every 5 minutes."""
    api_key = api_key or DATALASTIC_API_KEY
    backoff = 60

    while True:
        try:
            updated = await poll_live_positions(api_key)
            if updated > 0:
                backoff = 300  # 5 minutes on success
                logger.info("Global tracker: %d vessels updated, next poll in %ds", updated, backoff)
            else:
                backoff = min(backoff * 2, 600)
        except asyncio.CancelledError:
            logger.info("Global tracker stopped")
            return
        except Exception:
            logger.exception("Global tracker error")
            backoff = min(backoff * 2, 600)

        await asyncio.sleep(backoff)
