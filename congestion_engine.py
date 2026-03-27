"""
Port congestion scoring engine with in-memory vessel tracking.

Receives vessel position updates (from AIS stream), classifies vessel state,
and computes per-port congestion metrics.
"""

import math
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Helper: 1 nautical mile ~= 1/60 degree latitude.
# For longitude, adjust by cos(lat).
# ---------------------------------------------------------------------------

NM_TO_DEG_LAT = 1.0 / 60.0


def _nm_to_deg_lon(lat_deg: float) -> float:
    """Convert 1 nautical mile to degrees of longitude at a given latitude."""
    return NM_TO_DEG_LAT / math.cos(math.radians(lat_deg))


def _make_bbox(lat: float, lon: float, radius_nm: float):
    """Return [[south, west], [north, east]] bounding box for a port."""
    dlat = radius_nm * NM_TO_DEG_LAT
    dlon = radius_nm * _nm_to_deg_lon(lat)
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]]


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two points."""
    R_NM = 3440.065  # Earth radius in nautical miles
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R_NM * c


# ---------------------------------------------------------------------------
# Monitored ports
# ---------------------------------------------------------------------------

MONITORED_PORTS = {
    "INKAN": {
        "name": "Kandla",
        "country": "India",
        "lat": 22.98,
        "lon": 70.22,
        "bbox": _make_bbox(22.98, 70.22, 15),
        "inner_radius_nm": 2.0,
    },
    "INKAK": {
        "name": "Kakinada",
        "country": "India",
        "lat": 16.94,
        "lon": 82.24,
        "bbox": _make_bbox(16.94, 82.24, 8),
        "inner_radius_nm": 2.0,
    },
    "INVTZ": {
        "name": "Vishakhapatnam",
        "country": "India",
        "lat": 17.69,
        "lon": 83.30,
        "bbox": _make_bbox(17.69, 83.30, 8),
        "inner_radius_nm": 2.0,
    },
    "MGTNR": {
        "name": "Toamasina",
        "country": "Madagascar",
        "lat": -18.15,
        "lon": 49.40,
        "bbox": _make_bbox(-18.15, 49.40, 8),
        "inner_radius_nm": 2.0,
    },
    "MGTLE": {
        "name": "Toliara",
        "country": "Madagascar",
        "lat": -23.35,
        "lon": 43.67,
        "bbox": _make_bbox(-23.35, 43.67, 8),
        "inner_radius_nm": 2.0,
    },
    # European ports (strong AIS Stream coverage)
    "NLRTM": {
        "name": "Rotterdam",
        "country": "Netherlands",
        "lat": 51.92,
        "lon": 4.48,
        "bbox": _make_bbox(51.92, 4.48, 12),
        "inner_radius_nm": 2.0,
    },
    "BEANR": {
        "name": "Antwerp",
        "country": "Belgium",
        "lat": 51.23,
        "lon": 4.42,
        "bbox": _make_bbox(51.23, 4.42, 8),
        "inner_radius_nm": 1.5,
    },
    "DEHAM": {
        "name": "Hamburg",
        "country": "Germany",
        "lat": 53.54,
        "lon": 9.99,
        "bbox": _make_bbox(53.54, 9.99, 10),
        "inner_radius_nm": 2.0,
    },
}


# ---------------------------------------------------------------------------
# Vessel state classification
# ---------------------------------------------------------------------------

_STATE_PRIORITY = {
    "BERTHED": 0,
    "ANCHORED": 1,
    "APPROACHING": 2,
    "TRANSITING": 3,
}


def classify_vessel_state(
    speed: float,
    nav_status: Optional[int],
    dist_to_port_nm: float,
    inner_radius_nm: float,
) -> str:
    """Classify a vessel's operational state based on speed and position.

    Rules (evaluated in order):
        BERTHED   — speed < 0.3 kts AND within inner_radius_nm of port center
        ANCHORED  — speed < 0.5 kts
        TRANSITING — speed > 5.0 kts
        APPROACHING — everything else
    """
    if speed < 0.3 and dist_to_port_nm <= inner_radius_nm:
        return "BERTHED"
    if speed < 0.5:
        return "ANCHORED"
    if speed > 5.0:
        return "TRANSITING"
    return "APPROACHING"


# ---------------------------------------------------------------------------
# CongestionEngine
# ---------------------------------------------------------------------------


class CongestionEngine:
    """In-memory vessel tracker and port congestion scorer."""

    def __init__(self):
        # {locode: {mmsi: vessel_dict}}
        self._port_vessels: dict[str, dict[int, dict]] = {
            locode: {} for locode in MONITORED_PORTS
        }
        # Reverse lookup: mmsi -> locode (a vessel can only be in one port)
        self._vessel_port: dict[int, str] = {}

        # Stream health tracking
        self.stream_connected: bool = False
        self.connected_since: Optional[float] = None
        self.last_message_at: Optional[float] = None
        self.messages_received: int = 0

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    def update_vessel(
        self,
        mmsi: int,
        lat: float,
        lon: float,
        speed: float,
        course: float,
        heading: int,
        nav_status: Optional[int],
        ship_type: Optional[int],
        name: Optional[str],
        timestamp: float,
    ) -> None:
        """Ingest a vessel position update.

        Determines which monitored port (if any) the vessel is in,
        classifies state, and stores/updates the vessel record.
        """
        # Find which port bbox contains this position
        target_locode: Optional[str] = None
        target_port: Optional[dict] = None
        for locode, port in MONITORED_PORTS.items():
            bbox = port["bbox"]
            if (
                bbox[0][0] <= lat <= bbox[1][0]
                and bbox[0][1] <= lon <= bbox[1][1]
            ):
                target_locode = locode
                target_port = port
                break

        # If outside all ports, remove from any previous port and skip
        if target_locode is None:
            prev_locode = self._vessel_port.pop(mmsi, None)
            if prev_locode is not None:
                self._port_vessels[prev_locode].pop(mmsi, None)
            return

        # Distance from port center
        dist_nm = _haversine_nm(lat, lon, target_port["lat"], target_port["lon"])

        # Classify state
        state = classify_vessel_state(
            speed, nav_status, dist_nm, target_port["inner_radius_nm"]
        )

        now = timestamp

        # If vessel was previously in a different port, remove it
        prev_locode = self._vessel_port.get(mmsi)
        if prev_locode is not None and prev_locode != target_locode:
            self._port_vessels[prev_locode].pop(mmsi, None)

        # Get existing record (if any) for first_seen / state_since
        existing = self._port_vessels[target_locode].get(mmsi)
        first_seen = existing["first_seen"] if existing else now
        # Reset state_since when state changes
        if existing and existing["state"] == state:
            state_since = existing["state_since"]
        else:
            state_since = now

        vessel_dict = {
            "mmsi": mmsi,
            "name": name,
            "ship_type": ship_type,
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "course": course,
            "heading": heading,
            "nav_status": nav_status,
            "state": state,
            "dist_to_port_nm": round(dist_nm, 2),
            "first_seen": first_seen,
            "state_since": state_since,
            "last_seen": now,
        }

        self._port_vessels[target_locode][mmsi] = vessel_dict
        self._vessel_port[mmsi] = target_locode

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def prune_stale_vessels(self, max_age_seconds: int = 1800) -> int:
        """Remove vessels not seen for longer than *max_age_seconds*.

        Returns the number of pruned vessels.
        """
        now = time.time()
        pruned = 0
        for locode in MONITORED_PORTS:
            stale_mmsis = [
                mmsi
                for mmsi, v in self._port_vessels[locode].items()
                if (now - v["last_seen"]) > max_age_seconds
            ]
            for mmsi in stale_mmsis:
                del self._port_vessels[locode][mmsi]
                self._vessel_port.pop(mmsi, None)
                pruned += 1
        return pruned

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_port_data(self, locode: str) -> Optional[dict]:
        """Return raw vessel data for a port.

        Returns {"vessels": {mmsi: vessel_dict}} or {"vessels": {}} if
        the port has no vessels.  Returns None only for unknown locodes.
        """
        if locode not in MONITORED_PORTS:
            return None
        return {"vessels": dict(self._port_vessels[locode])}

    def get_port_metrics(self, locode: str) -> dict:
        """Compute congestion score and breakdown for a port.

        Congestion formula:
            anchor_ratio  = anchored / max(anchored + berthed, 1)
            wait_factor   = min(avg_wait_hours / 48.0, 1.0)
            queue_pressure = min(anchored / 10.0, 1.0)
            score = anchor_ratio*40 + wait_factor*35 + queue_pressure*25

        Severity thresholds: SEVERE >= 75, HIGH >= 50, MODERATE >= 25, LOW < 25
        """
        vessels = self._port_vessels.get(locode, {})

        anchored_count = 0
        berthed_count = 0
        approaching_count = 0
        transiting_count = 0
        total_wait_hours = 0.0
        now = time.time()

        for v in vessels.values():
            st = v["state"]
            if st == "ANCHORED":
                anchored_count += 1
                total_wait_hours += (now - v["state_since"]) / 3600.0
            elif st == "BERTHED":
                berthed_count += 1
            elif st == "APPROACHING":
                approaching_count += 1
            elif st == "TRANSITING":
                transiting_count += 1

        total_vessels = len(vessels)

        # Congestion score
        if total_vessels == 0:
            score = 0.0
        else:
            anchor_ratio = anchored_count / max(anchored_count + berthed_count, 1)
            avg_wait = total_wait_hours / max(anchored_count, 1)
            wait_factor = min(avg_wait / 48.0, 1.0)
            queue_pressure = min(anchored_count / 10.0, 1.0)
            score = (anchor_ratio * 40) + (wait_factor * 35) + (queue_pressure * 25)

        score = round(score, 1)

        if score >= 75:
            severity = "SEVERE"
        elif score >= 50:
            severity = "HIGH"
        elif score >= 25:
            severity = "MODERATE"
        else:
            severity = "LOW"

        return {
            "congestion_score": score,
            "severity": severity,
            "total_vessels": total_vessels,
            "anchored_count": anchored_count,
            "berthed_count": berthed_count,
            "approaching_count": approaching_count,
            "transiting_count": transiting_count,
        }

    def get_all_ports_summary(self) -> list[dict]:
        """Return a list of dicts — one per monitored port — with metrics."""
        result = []
        for locode, port in MONITORED_PORTS.items():
            metrics = self.get_port_metrics(locode)
            result.append(
                {
                    "locode": locode,
                    "name": port["name"],
                    "country": port["country"],
                    "lat": port["lat"],
                    "lon": port["lon"],
                    **metrics,
                }
            )
        return result

    def get_port_detail(self, locode: str) -> Optional[dict]:
        """Return full detail for a port including sorted vessel list.

        Vessels are sorted by state priority (BERTHED first, TRANSITING last)
        then by distance to port center.  Each vessel gets a computed
        *wait_hours* field (hours since state_since for ANCHORED, else 0).
        """
        if locode not in MONITORED_PORTS:
            return None

        port = MONITORED_PORTS[locode]
        metrics = self.get_port_metrics(locode)
        now = time.time()

        vessels_list = []
        for v in self._port_vessels[locode].values():
            vessel = dict(v)
            if vessel["state"] == "ANCHORED":
                vessel["wait_hours"] = round(
                    (now - vessel["state_since"]) / 3600.0, 2
                )
            else:
                vessel["wait_hours"] = 0
            vessels_list.append(vessel)

        # Sort: state priority ascending, then distance ascending
        vessels_list.sort(
            key=lambda v: (_STATE_PRIORITY.get(v["state"], 99), v["dist_to_port_nm"])
        )

        return {
            "locode": locode,
            "name": port["name"],
            "country": port["country"],
            "lat": port["lat"],
            "lon": port["lon"],
            **metrics,
            "vessels": vessels_list,
        }

    def get_status(self) -> dict:
        """Return stream connection health info."""
        total_tracked = sum(
            len(vessels) for vessels in self._port_vessels.values()
        )
        return {
            "stream_connected": self.stream_connected,
            "connected_since": self.connected_since,
            "last_message_at": self.last_message_at,
            "messages_received": self.messages_received,
            "total_vessels_tracked": total_tracked,
            "monitored_ports": len(MONITORED_PORTS),
        }
