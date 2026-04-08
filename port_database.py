"""
Port database — downloads all ports from Datalastic port_find API and
resolves vessel destination strings to structured port records.

Mirrors global_tracker.py pattern: download → save to disk → in-memory index.
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
PORT_DB_FILE = Path("data/port_database.json")
API_BASE = "https://api.datalastic.com/api/v0"
PORT_DB_MAX_AGE_DAYS = 7

# Countries with significant maritime port activity
MARITIME_COUNTRY_CODES = [
    "CN", "SG", "NL", "DE", "BE", "GB", "FR", "IT", "ES", "GR",
    "TR", "JP", "KR", "TW", "HK", "IN", "BD", "PK", "LK", "MM",
    "TH", "VN", "PH", "ID", "MY", "AE", "SA", "KW", "OM", "IQ",
    "IR", "EG", "IL", "JO", "QA", "BH", "YE", "DJ", "SO", "KE",
    "TZ", "MZ", "ZA", "MG", "MU", "SC", "NG", "GH", "SN", "CI",
    "CM", "AO", "CD", "GA", "MA", "DZ", "TN", "LY", "US", "CA",
    "MX", "PA", "CO", "BR", "AR", "CL", "PE", "UY", "VE", "EC",
    "AU", "NZ", "FJ", "PG", "NO", "SE", "DK", "FI", "PL", "EE",
    "LV", "LT", "RU", "UA", "RO", "BG", "HR", "MT", "CY", "PT",
    "RE", "CU", "JM", "TT", "BS", "DO", "GY", "SR", "GT", "HN",
    "NI", "CR", "SV", "PY", "BO", "LB", "SY", "MM", "KH", "LA",
    "MN", "GE", "AZ", "KZ", "TM", "MV", "ET", "ER", "RW", "TG",
    "BJ", "LR", "SL", "GW", "GM", "CV", "ST", "CG", "ZM", "ZW",
]

# Destination strings that are definitely not port names
JUNK_DESTINATIONS = {
    "CLASS B", "FOR ORDERS", "FOR ORDER", "0", "NONE", "N/A", "TBC",
    "UNKNOWN", "AT SEA", "WAITING ORDERS", "ORDERS", "DIRECT",
    "FISHING GROUNDS", "FAO051", "", "8", "ANCHOR", "ANCHORAGE",
    "LAYUP", "MAINTENANCE", "REPAIR", "DRYDOCK", "DRY DOCK",
}

# In-memory indexes
_port_db: dict[str, dict] = {}          # UPPER(unlocode) -> port record
_port_name_index: dict[str, list] = {}  # UPPER(port_name) -> [unlocodes]
_db_loaded: bool = False


async def download_all_ports(api_key: str = "") -> list[dict]:
    """Download all ports from Datalastic by iterating maritime country codes."""
    api_key = api_key or DATALASTIC_API_KEY
    if not api_key:
        logger.warning("No API key for port database download")
        return []

    all_ports: list[dict] = []
    seen_uuids: set[str] = set()
    failed_countries: list[str] = []
    semaphore = asyncio.Semaphore(5)

    async def fetch_country(client: httpx.AsyncClient, country: str):
        async with semaphore:
            try:
                resp = await client.get(
                    f"{API_BASE}/port_find",
                    params={"api-key": api_key, "country_iso": country},
                    timeout=20,
                )
                if resp.status_code != 200:
                    logger.debug("port_find %s HTTP %d", country, resp.status_code)
                    failed_countries.append(country)
                    return
                data = resp.json()
                ports = data.get("data", [])
                for p in ports:
                    uuid = p.get("uuid", "")
                    if uuid and uuid not in seen_uuids:
                        seen_uuids.add(uuid)
                        all_ports.append(p)
                if ports:
                    logger.debug("port_find %s: %d ports", country, len(ports))
            except Exception:
                logger.debug("port_find %s error", country, exc_info=True)
                failed_countries.append(country)

    async with httpx.AsyncClient() as client:
        tasks = [fetch_country(client, c) for c in MARITIME_COUNTRY_CODES]
        await asyncio.gather(*tasks)

    logger.info("Port database downloaded: %d ports from %d countries (%d failed)",
                len(all_ports), len(MARITIME_COUNTRY_CODES), len(failed_countries))

    if all_ports:
        PORT_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PORT_DB_FILE, "w") as f:
            json.dump({
                "ports": all_ports,
                "count": len(all_ports),
                "downloaded_at": time.time(),
                "countries_fetched": MARITIME_COUNTRY_CODES,
                "countries_failed": failed_countries,
            }, f)

    return all_ports


def _build_indexes(ports: list[dict]):
    """Build in-memory lookup indexes from port list."""
    global _port_db, _port_name_index
    _port_db = {}
    _port_name_index = {}

    for p in ports:
        locode = (p.get("unlocode") or "").strip().upper()
        name = (p.get("port_name") or "").strip().upper()

        if locode:
            _port_db[locode] = p

        if name:
            if name not in _port_name_index:
                _port_name_index[name] = []
            if locode:
                _port_name_index[name].append(locode)

    logger.info("Port index built: %d locodes, %d names", len(_port_db), len(_port_name_index))


def load_port_database() -> list[dict]:
    """Load port database from disk and build indexes."""
    global _db_loaded
    if not PORT_DB_FILE.exists():
        return []
    try:
        with open(PORT_DB_FILE) as f:
            data = json.load(f)
        ports = data.get("ports", [])
        age_days = (time.time() - data.get("downloaded_at", 0)) / 86400
        logger.info("Loaded port database: %d ports (%.1f days old)", len(ports), age_days)
        _build_indexes(ports)
        _db_loaded = True
        return ports
    except Exception:
        logger.exception("Failed to load port database")
        return []


async def init_port_database(api_key: str = ""):
    """Load from disk; trigger background download if missing or stale."""
    global _db_loaded
    ports = load_port_database()

    if not ports:
        logger.info("Port database not found — downloading...")
        ports = await download_all_ports(api_key)
        if ports:
            _build_indexes(ports)
            _db_loaded = True
    else:
        # Check age — schedule background re-download if stale
        age_days = (time.time() - _get_db_age()) / 86400
        if age_days > PORT_DB_MAX_AGE_DAYS:
            logger.info("Port database is %.1f days old — scheduling refresh", age_days)
            asyncio.create_task(_background_refresh(api_key))


def _get_db_age() -> float:
    try:
        with open(PORT_DB_FILE) as f:
            return json.load(f).get("downloaded_at", 0)
    except Exception:
        return 0.0


async def _background_refresh(api_key: str = ""):
    ports = await download_all_ports(api_key)
    if ports:
        _build_indexes(ports)
        logger.info("Port database refreshed in background")


async def run_port_database_refresh(api_key: str = ""):
    """Background loop: refresh port database weekly."""
    while True:
        await asyncio.sleep(86400 * 7)
        try:
            await _background_refresh(api_key)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Port database refresh error")


def resolve_destination(dest: str) -> dict | None:
    """Resolve a vessel destination string to a port record.

    Strategy (in order):
    1. Exact UNLOCODE match (5-char alpha, e.g. "NLRTM")
    2. Exact port name match (e.g. "ROTTERDAM")
    3. First token of destination (e.g. "ROTTERDAM VIA SUEZ" → "ROTTERDAM")
    4. Prefix match against port names
    Returns None if unresolvable.
    """
    if not _db_loaded:
        return None

    dest = dest.strip().upper()
    if not dest or dest in JUNK_DESTINATIONS:
        return None

    # 1. Exact UNLOCODE (typically 5 chars: 2 alpha country + 3 alphanumeric)
    if dest in _port_db:
        return _port_db[dest]

    # Handle space-separated LOCODEs like "NL RTM" or "TZ DAR" → "NLRTM"/"TZDAR"
    parts = dest.split()
    if len(parts) == 2 and len(parts[0]) == 2 and len(parts[1]) <= 3:
        combined = parts[0] + parts[1]
        if combined in _port_db:
            return _port_db[combined]

    # 2. Exact name match
    if dest in _port_name_index:
        locodes = _port_name_index[dest]
        if locodes:
            return _port_db.get(locodes[0])

    # 3. First token of multi-word destination
    first_token = parts[0] if parts else ""
    if first_token and first_token != dest and first_token in _port_name_index:
        locodes = _port_name_index[first_token]
        if locodes:
            return _port_db.get(locodes[0])

    # 4. Prefix match (dest is prefix of known port name)
    if len(dest) >= 4:
        for name, locodes in _port_name_index.items():
            if name.startswith(dest) and locodes:
                return _port_db.get(locodes[0])

    return None


def get_database_status() -> dict:
    """Return port database status info."""
    count = len(_port_db)
    age_days = None
    if PORT_DB_FILE.exists():
        try:
            with open(PORT_DB_FILE) as f:
                data = json.load(f)
            age_days = round((time.time() - data.get("downloaded_at", 0)) / 86400, 1)
        except Exception:
            pass
    return {
        "loaded": _db_loaded,
        "total_ports": count,
        "total_names": len(_port_name_index),
        "age_days": age_days,
        "file_exists": PORT_DB_FILE.exists(),
    }
