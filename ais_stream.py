"""
AIS Stream WebSocket client.

Connects to wss://stream.aisstream.io/v0/stream, subscribes to bounding
boxes around the monitored ports, parses incoming AIS messages, and feeds
them into the CongestionEngine.
"""

import asyncio
import json
import logging
import os
import time

import websockets

from congestion_engine import CongestionEngine, MONITORED_PORTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AIS_STREAM_URL = "wss://stream.aisstream.io/v0/stream"
AIS_STREAM_API_KEY = os.getenv("AIS_STREAM_API_KEY", "")

PRUNE_INTERVAL_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_bbox_list() -> list[list[list[float]]]:
    """Build list of bounding boxes from MONITORED_PORTS for subscription.

    Each bbox is [[south, west], [north, east]].
    """
    bboxes = []
    for port in MONITORED_PORTS.values():
        bboxes.append(port["bbox"])
    return bboxes


def _build_subscription_message() -> str:
    """Return the JSON subscription message for AIS Stream."""
    payload = {
        "APIKey": AIS_STREAM_API_KEY,
        "BoundingBoxes": _build_bbox_list(),
        "FilterMessageTypes": [
            "PositionReport",
            "ShipStaticData",
            "StandardClassBPositionReport",
        ],
    }
    return json.dumps(payload)


def _parse_ais_message(msg: dict) -> dict | None:
    """Extract vessel data from an AIS Stream message.

    Returns a dict with keys: mmsi, lat, lon, speed, course, heading,
    nav_status, ship_type, name, timestamp.
    Returns None if the message cannot be parsed.
    """
    try:
        meta = msg["MetaData"]
        mmsi = int(meta["MMSI"])
        lat = float(meta["latitude"])
        lon = float(meta["longitude"])
        name = meta.get("ShipName", "").strip() or None

        message_type = msg.get("MessageType", "")

        speed = 0.0
        course = 0.0
        heading = 0
        nav_status = None
        ship_type = None

        if message_type == "PositionReport":
            report = msg["Message"]["PositionReport"]
            speed = float(report.get("Sog", 0))
            course = float(report.get("Cog", 0))
            heading = int(report.get("TrueHeading", 0))
            nav_status = report.get("NavigationalStatus")

        elif message_type == "StandardClassBPositionReport":
            report = msg["Message"]["StandardClassBPositionReport"]
            speed = float(report.get("Sog", 0))
            course = float(report.get("Cog", 0))
            heading = int(report.get("TrueHeading", 0))

        elif message_type == "ShipStaticData":
            static = msg["Message"]["ShipStaticData"]
            ship_type = static.get("Type")
            speed = 0.0
            course = 0.0
            heading = 0

        else:
            return None

        return {
            "mmsi": mmsi,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "heading": heading,
            "nav_status": nav_status,
            "ship_type": ship_type,
            "name": name,
            "timestamp": time.time(),
        }

    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("Failed to parse AIS message: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------


async def run_ais_stream(engine: CongestionEngine) -> None:
    """Connect to AIS Stream and feed vessel updates into the engine.

    Reconnects automatically with exponential backoff on failure.
    """
    backoff = 1.0

    while True:
        # Guard: no API key
        if not AIS_STREAM_API_KEY:
            logger.warning(
                "AIS_STREAM_API_KEY not set — cannot connect. Retrying in 60s."
            )
            await asyncio.sleep(60)
            continue

        try:
            logger.info("Connecting to AIS Stream at %s …", AIS_STREAM_URL)
            async with websockets.connect(AIS_STREAM_URL) as ws:
                # Send subscription
                await ws.send(_build_subscription_message())
                logger.info("Subscribed to %d port bounding boxes.", len(MONITORED_PORTS))

                # Mark engine as connected
                engine.stream_connected = True
                engine.connected_since = time.time()

                # Reset backoff on successful connection
                backoff = 1.0

                last_prune = time.time()

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("Non-JSON message received, skipping.")
                        continue

                    parsed = _parse_ais_message(msg)
                    if parsed is not None:
                        engine.update_vessel(**parsed)
                        engine.messages_received += 1
                        engine.last_message_at = time.time()

                    # Periodic prune
                    now = time.time()
                    if now - last_prune >= PRUNE_INTERVAL_SECONDS:
                        pruned = engine.prune_stale_vessels()
                        if pruned:
                            logger.info("Pruned %d stale vessels.", pruned)
                        last_prune = now

        except (
            websockets.ConnectionClosed,
            ConnectionError,
            OSError,
        ) as exc:
            logger.warning("AIS Stream connection lost: %s", exc)

        except asyncio.CancelledError:
            logger.info("AIS Stream task cancelled.")
            engine.stream_connected = False
            engine.connected_since = None
            raise

        except Exception:
            logger.exception("Unexpected error in AIS Stream loop.")

        # Mark disconnected and back off
        engine.stream_connected = False
        engine.connected_since = None

        logger.info("Reconnecting in %.0fs …", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)
