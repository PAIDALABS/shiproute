"""
Datalastic REST API client for live vessel tracking.

Periodically polls the vessel_inradius endpoint for each monitored port
and feeds vessel positions into the CongestionEngine.
"""

import asyncio
import logging
import os
import time

import httpx

from congestion_engine import CongestionEngine, MONITORED_PORTS

logger = logging.getLogger("ais_stream")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATALASTIC_API_KEY = os.getenv("DATALASTIC_API_KEY", "")
DATALASTIC_BASE_URL = "https://api.datalastic.com/api/v0/vessel_inradius"

# Poll every 2 minutes (balance between freshness and API credits)
POLL_INTERVAL_SECONDS = 120
PRUNE_INTERVAL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Parse Datalastic vessel into engine format
# ---------------------------------------------------------------------------

def _parse_vessel(vessel: dict) -> dict | None:
    """Convert a Datalastic vessel dict into CongestionEngine update params."""
    try:
        mmsi = int(vessel.get("mmsi", 0))
        if mmsi == 0:
            return None

        lat = vessel.get("lat")
        lon = vessel.get("lon")
        if lat is None or lon is None:
            return None

        return {
            "mmsi": mmsi,
            "lat": float(lat),
            "lon": float(lon),
            "speed": float(vessel.get("speed", 0) or 0),
            "course": float(vessel.get("course", 0) or 0),
            "heading": int(vessel.get("heading", 0) or 0),
            "nav_status": None,  # Datalastic doesn't provide nav_status directly
            "ship_type": vessel.get("type"),
            "name": (vessel.get("name") or "").strip() or None,
            "timestamp": time.time(),
        }
    except (TypeError, ValueError) as exc:
        logger.debug("Failed to parse vessel: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Fetch vessels for one port
# ---------------------------------------------------------------------------

async def _fetch_port_vessels(
    client: httpx.AsyncClient,
    locode: str,
    port_def: dict,
) -> list[dict]:
    """Fetch vessels near a port from Datalastic API."""
    # Calculate radius from bbox (approximate nm)
    bbox = port_def["bbox"]
    dlat = bbox[1][0] - bbox[0][0]  # north - south in degrees
    radius_nm = round(dlat * 60 / 2, 1)  # convert back to approximate nm

    params = {
        "api-key": DATALASTIC_API_KEY,
        "lat": port_def["lat"],
        "lon": port_def["lon"],
        "radius": radius_nm,
    }

    try:
        resp = await client.get(DATALASTIC_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        vessels_raw = data.get("data", {}).get("vessels", [])
        total = data.get("data", {}).get("total", 0)

        if total > 0:
            logger.info(
                "  %s (%s): %d vessels found", port_def["name"], locode, total
            )

        return vessels_raw

    except httpx.HTTPStatusError as exc:
        logger.warning("Datalastic HTTP error for %s: %s", locode, exc)
        return []
    except Exception as exc:
        logger.warning("Datalastic request failed for %s: %s", locode, exc)
        return []


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------

async def run_ais_stream(engine: CongestionEngine) -> None:
    """Poll Datalastic API for all monitored ports on a regular interval.

    Despite the function name (kept for compatibility), this now uses
    Datalastic REST polling instead of AIS Stream WebSocket.
    """
    while True:
        if not DATALASTIC_API_KEY:
            logger.warning(
                "DATALASTIC_API_KEY not set — cannot fetch data. Retrying in 60s."
            )
            await asyncio.sleep(60)
            continue

        try:
            engine.stream_connected = True
            engine.connected_since = engine.connected_since or time.time()

            async with httpx.AsyncClient() as client:
                while True:
                    logger.info("Polling Datalastic for %d ports...", len(MONITORED_PORTS))
                    total_vessels = 0

                    for locode, port_def in MONITORED_PORTS.items():
                        vessels_raw = await _fetch_port_vessels(client, locode, port_def)

                        for v in vessels_raw:
                            parsed = _parse_vessel(v)
                            if parsed:
                                engine.update_vessel(**parsed)
                                engine.messages_received += 1
                                engine.last_message_at = time.time()
                                total_vessels += 1

                        # Small delay between ports to be nice to the API
                        await asyncio.sleep(0.5)

                    logger.info(
                        "Poll complete: %d vessels ingested across %d ports.",
                        total_vessels, len(MONITORED_PORTS),
                    )

                    # Prune stale vessels
                    pruned = engine.prune_stale_vessels()
                    if pruned:
                        logger.info("Pruned %d stale vessels.", pruned)

                    # Wait for next poll cycle
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Datalastic polling task cancelled.")
            engine.stream_connected = False
            engine.connected_since = None
            raise

        except Exception:
            logger.exception("Unexpected error in Datalastic polling loop.")
            engine.stream_connected = False
            engine.connected_since = None
            logger.info("Retrying in 30s...")
            await asyncio.sleep(30)
