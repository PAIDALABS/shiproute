"""
Port congestion scoring engine with in-memory vessel tracking.

Receives vessel position updates (from AIS stream), classifies vessel state,
and computes per-port congestion metrics.
"""

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Optional

STATE_FILE = Path("data/engine_state.json")

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
        "max_queue": 20,
    },
    "INKAK": {
        "name": "Kakinada",
        "country": "India",
        "lat": 16.94,
        "lon": 82.24,
        "bbox": _make_bbox(16.94, 82.24, 8),
        "inner_radius_nm": 2.0,
        "max_queue": 10,
    },
    "INVTZ": {
        "name": "Vishakhapatnam",
        "country": "India",
        "lat": 17.69,
        "lon": 83.30,
        "bbox": _make_bbox(17.69, 83.30, 8),
        "inner_radius_nm": 2.0,
        "max_queue": 20,
    },
    "MGTNR": {
        "name": "Toamasina",
        "country": "Madagascar",
        "lat": -18.15,
        "lon": 49.40,
        "bbox": _make_bbox(-18.15, 49.40, 8),
        "inner_radius_nm": 2.0,
        "max_queue": 10,
    },
    "MGTLE": {
        "name": "Toliara",
        "country": "Madagascar",
        "lat": -23.35,
        "lon": 43.67,
        "bbox": _make_bbox(-23.35, 43.67, 8),
        "inner_radius_nm": 2.0,
        "max_queue": 10,
    },
    # European ports (strong AIS Stream coverage)
    "NLRTM": {
        "name": "Rotterdam",
        "country": "Netherlands",
        "lat": 51.92,
        "lon": 4.48,
        "bbox": _make_bbox(51.92, 4.48, 12),
        "inner_radius_nm": 2.0,
        "max_queue": 40,
    },
    "BEANR": {
        "name": "Antwerp",
        "country": "Belgium",
        "lat": 51.23,
        "lon": 4.42,
        "bbox": _make_bbox(51.23, 4.42, 8),
        "inner_radius_nm": 1.5,
        "max_queue": 20,
    },
    "DEHAM": {
        "name": "Hamburg",
        "country": "Germany",
        "lat": 53.54,
        "lon": 9.99,
        "bbox": _make_bbox(53.54, 9.99, 10),
        "inner_radius_nm": 2.0,
        "max_queue": 40,
    },
    # East African ports
    "KEMBA": {
        "name": "Mombasa",
        "country": "Kenya",
        "lat": -4.05,
        "lon": 39.67,
        "bbox": _make_bbox(-4.05, 39.67, 10),
        "inner_radius_nm": 2.0,
        "max_queue": 15,
    },
    "TZDAR": {
        "name": "Dar es Salaam",
        "country": "Tanzania",
        "lat": -6.82,
        "lon": 39.29,
        "bbox": _make_bbox(-6.82, 39.29, 8),
        "inner_radius_nm": 2.0,
        "max_queue": 15,
    },
    # Additional Indian ports
    "INNSA": {
        "name": "Mumbai (JNPT)",
        "country": "India",
        "lat": 18.95,
        "lon": 72.95,
        "bbox": _make_bbox(18.95, 72.95, 12),
        "inner_radius_nm": 3.0,
        "max_queue": 25,
    },
    "INMUN": {
        "name": "Mundra",
        "country": "India",
        "lat": 22.84,
        "lon": 69.72,
        "bbox": _make_bbox(22.84, 69.72, 10),
        "inner_radius_nm": 2.5,
        "max_queue": 20,
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
    """Classify a vessel's operational state based on speed, AIS nav_status, and position.

    Rules (evaluated in order):
        BERTHED    — very slow + near port center, or AIS reports moored (nav_status 5)
        ANCHORED   — slow or AIS reports at anchor (nav_status 1)
        TRANSITING — speed > 5.0 kts
        APPROACHING — everything else
    """
    # BERTHED — very slow, near port center, or AIS reports moored
    if (speed < 0.3 and dist_to_port_nm <= inner_radius_nm) or nav_status == 5:
        return "BERTHED"
    # ANCHORED — slow or AIS reports at anchor
    if speed < 1.5 and (nav_status == 1 or speed < 0.5):
        return "ANCHORED"
    # TRANSITING — moving fast through port area
    if speed > 5.0:
        return "TRANSITING"
    return "APPROACHING"


# ---------------------------------------------------------------------------
# CongestionEngine
# ---------------------------------------------------------------------------


class CongestionEngine:
    """In-memory vessel tracker and port congestion scorer."""

    # Max completed visits to keep per port (rolling window)
    _MAX_VISIT_HISTORY = 500

    def __init__(self):
        # {locode: {mmsi: vessel_dict}}
        self._port_vessels: dict[str, dict[int, dict]] = {
            locode: {} for locode in MONITORED_PORTS
        }
        # Reverse lookup: mmsi -> locode (a vessel can only be in one port)
        self._vessel_port: dict[int, str] = {}
        # Guards concurrent read/write access from background poller vs request handlers
        self._lock = asyncio.Lock()

        # Completed port visits for turnaround analysis
        # {locode: [visit_dict, ...]}
        self._completed_visits: dict[str, list[dict]] = {
            locode: [] for locode in MONITORED_PORTS
        }

        # Score history for 24h delta tracking {locode: [(timestamp, score), ...]}
        self._score_history: dict[str, list[tuple[float, float]]] = {
            locode: [] for locode in MONITORED_PORTS
        }

        # Stream health tracking
        self.stream_connected: bool = False
        self.connected_since: Optional[float] = None
        self.last_message_at: Optional[float] = None
        self.messages_received: int = 0

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        """Persist vessel state to disk for restart recovery."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "port_vessels": {
                locode: {str(mmsi): v for mmsi, v in vessels.items()}
                for locode, vessels in self._port_vessels.items()
            },
            "vessel_port": {str(k): v for k, v in self._vessel_port.items()},
            "saved_at": time.time(),
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f)
        tmp.rename(STATE_FILE)  # atomic on POSIX

    def load_state(self) -> bool:
        """Restore vessel state from disk. Returns True if state was loaded."""
        if not STATE_FILE.exists():
            return False
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            saved_at = state.get("saved_at", 0)
            age_minutes = (time.time() - saved_at) / 60
            if age_minutes > 60:
                logging.info("Engine state too old (%.0f min), starting fresh", age_minutes)
                return False
            for locode, vessels in state.get("port_vessels", {}).items():
                if locode in self._port_vessels:
                    self._port_vessels[locode] = {int(mmsi): v for mmsi, v in vessels.items()}
            self._vessel_port = {int(k): v for k, v in state.get("vessel_port", {}).items()}
            logging.info("Restored engine state: %d vessels from %.0f min ago",
                        sum(len(v) for v in self._port_vessels.values()), age_minutes)
            return True
        except Exception:
            logging.exception("Failed to load engine state")
            return False

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
        ship_type: Optional[str],
        name: Optional[str],
        timestamp: float,
        # Extra fields for intelligence (optional)
        country_iso: Optional[str] = None,
        type_specific: Optional[str] = None,
        destination: Optional[str] = None,
        eta_epoch: Optional[float] = None,
        imo: Optional[str] = None,
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

        # Accumulated state hours (carry forward from existing)
        anchor_hours = existing["anchor_hours"] if existing else 0.0
        berth_hours = existing["berth_hours"] if existing else 0.0

        # Reset state_since when state changes; accumulate time in old state
        if existing and existing["state"] == state:
            state_since = existing["state_since"]
        else:
            if existing:
                elapsed = (now - existing["state_since"]) / 3600.0
                if existing["state"] == "ANCHORED":
                    anchor_hours += elapsed
                elif existing["state"] == "BERTHED":
                    berth_hours += elapsed
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
            "anchor_hours": anchor_hours,
            "berth_hours": berth_hours,
            # Intelligence fields
            "country_iso": country_iso if country_iso is not None else (existing["country_iso"] if existing else None),
            "type_specific": type_specific if type_specific is not None else (existing["type_specific"] if existing else None),
            "destination": destination if destination is not None else (existing["destination"] if existing else None),
            "eta_epoch": eta_epoch if eta_epoch is not None else (existing["eta_epoch"] if existing else None),
            "imo": imo if imo is not None else (existing["imo"] if existing else None),
        }

        self._port_vessels[target_locode][mmsi] = vessel_dict
        self._vessel_port[mmsi] = target_locode

    # ------------------------------------------------------------------
    # Departure recording
    # ------------------------------------------------------------------

    def _record_departure(self, locode: str, vessel: dict) -> None:
        """Record a completed port visit for turnaround analysis."""
        now = time.time()
        # Add final state time
        anchor_hours = vessel.get("anchor_hours", 0.0)
        berth_hours = vessel.get("berth_hours", 0.0)
        elapsed = (now - vessel["state_since"]) / 3600.0
        if vessel["state"] == "ANCHORED":
            anchor_hours += elapsed
        elif vessel["state"] == "BERTHED":
            berth_hours += elapsed

        total_hours = (now - vessel["first_seen"]) / 3600.0

        # Only record if vessel spent meaningful time (> 30 min)
        if total_hours < 0.5:
            return

        visit = {
            "mmsi": vessel["mmsi"],
            "name": vessel.get("name"),
            "ship_type": vessel.get("ship_type") or "Unknown",
            "arrival_time": vessel["first_seen"],
            "departure_time": now,
            "total_hours": round(total_hours, 2),
            "anchor_hours": round(anchor_hours, 2),
            "berth_hours": round(berth_hours, 2),
            "wait_hours": round(anchor_hours, 2),  # alias
        }

        history = self._completed_visits[locode]
        history.append(visit)
        # Trim to max size
        if len(history) > self._MAX_VISIT_HISTORY:
            self._completed_visits[locode] = history[-self._MAX_VISIT_HISTORY:]

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def prune_stale_vessels(self, max_age_seconds: int = 1800) -> int:
        """Remove vessels not seen for longer than *max_age_seconds*.

        Records completed visits before removing. Returns the number pruned.
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
                vessel = self._port_vessels[locode][mmsi]
                self._record_departure(locode, vessel)
                del self._port_vessels[locode][mmsi]
                self._vessel_port.pop(mmsi, None)
                pruned += 1
        return pruned

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_vessels_snapshot(self, locode: str) -> dict:
        """Return a snapshot copy of vessels for a port (thread-safe)."""
        return dict(self._port_vessels.get(locode, {}))

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
        vessels = dict(self._port_vessels.get(locode, {}))

        anchored_count = 0
        berthed_count = 0
        approaching_count = 0
        transiting_count = 0
        total_wait_hours = 0.0
        now = time.time()

        for v in vessels.values():
            st = v.get("state", "")
            if st == "ANCHORED":
                anchored_count += 1
                total_wait_hours += (now - v.get("state_since", now)) / 3600.0
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
            port_def = MONITORED_PORTS.get(locode, {})
            max_queue = port_def.get("max_queue", 15)
            queue_pressure = min(anchored_count / max_queue, 1.0)
            score = (anchor_ratio * 40) + (wait_factor * 35) + (queue_pressure * 25)

        # Prevent single-vessel situations from triggering HIGH/SEVERE
        if anchored_count < 3 and score > 50:
            score = min(score, 50.0)

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

    def get_turnaround_stats(self, locode: str) -> Optional[dict]:
        """Return turnaround time statistics grouped by vessel type.

        Combines completed visits (historical) with currently berthed/anchored
        vessels (in-progress) to give a full picture.
        """
        if locode not in MONITORED_PORTS:
            return None

        now = time.time()

        # Combine completed visits + current vessels (as in-progress visits)
        all_visits: list[dict] = []

        # Completed visits from history
        for v in self._completed_visits.get(locode, []):
            all_visits.append({
                "ship_type": v["ship_type"],
                "total_hours": v["total_hours"],
                "anchor_hours": v["anchor_hours"],
                "berth_hours": v["berth_hours"],
                "status": "completed",
                "name": v.get("name"),
                "mmsi": v["mmsi"],
            })

        # Current vessels (in-progress)
        for v in self._port_vessels.get(locode, {}).values():
            anchor_h = v.get("anchor_hours", 0.0)
            berth_h = v.get("berth_hours", 0.0)
            # Add current state elapsed time
            elapsed = (now - v["state_since"]) / 3600.0
            if v["state"] == "ANCHORED":
                anchor_h += elapsed
            elif v["state"] == "BERTHED":
                berth_h += elapsed

            total_h = (now - v["first_seen"]) / 3600.0

            all_visits.append({
                "ship_type": v.get("ship_type") or "Unknown",
                "total_hours": round(total_h, 2),
                "anchor_hours": round(anchor_h, 2),
                "berth_hours": round(berth_h, 2),
                "status": "in_port",
                "state": v["state"],
                "name": v.get("name"),
                "mmsi": v["mmsi"],
            })

        # Group by vessel type
        type_groups: dict[str, list[dict]] = {}
        for visit in all_visits:
            vtype = visit["ship_type"] or "Unknown"
            type_groups.setdefault(vtype, []).append(visit)

        # Compute stats per type
        type_stats = []
        for vtype, visits in sorted(type_groups.items(), key=lambda x: -len(x[1])):
            n = len(visits)
            total_hrs = [v["total_hours"] for v in visits]
            anchor_hrs = [v["anchor_hours"] for v in visits]
            berth_hrs = [v["berth_hours"] for v in visits]

            type_stats.append({
                "vessel_type": vtype,
                "count": n,
                "avg_total_hours": round(sum(total_hrs) / n, 1) if n else 0,
                "avg_anchor_hours": round(sum(anchor_hrs) / n, 1) if n else 0,
                "avg_berth_hours": round(sum(berth_hrs) / n, 1) if n else 0,
                "max_total_hours": round(max(total_hrs), 1) if total_hrs else 0,
                "max_anchor_hours": round(max(anchor_hrs), 1) if anchor_hrs else 0,
                "min_total_hours": round(min(total_hrs), 1) if total_hrs else 0,
            })

        # Overall stats
        if all_visits:
            all_total = [v["total_hours"] for v in all_visits]
            all_anchor = [v["anchor_hours"] for v in all_visits]
            all_berth = [v["berth_hours"] for v in all_visits]
            overall = {
                "total_visits": len(all_visits),
                "avg_turnaround_hours": round(sum(all_total) / len(all_total), 1) if all_total else 0,
                "avg_wait_hours": round(sum(all_anchor) / len(all_anchor), 1) if all_anchor else 0,
                "avg_berth_hours": round(sum(all_berth) / len(all_berth), 1) if all_berth else 0,
            }
        else:
            overall = {
                "total_visits": 0,
                "avg_turnaround_hours": 0,
                "avg_wait_hours": 0,
                "avg_berth_hours": 0,
            }

        return {
            "locode": locode,
            "name": MONITORED_PORTS[locode]["name"],
            "overall": overall,
            "by_vessel_type": type_stats,
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

    # ------------------------------------------------------------------
    # Score history for 24h delta tracking
    # ------------------------------------------------------------------

    def record_score(self, locode: str) -> None:
        """Record current congestion score for delta tracking."""
        metrics = self.get_port_metrics(locode)
        now = time.time()
        history = self._score_history.get(locode, [])
        history.append((now, metrics["congestion_score"]))
        # Keep only last 24 hours
        cutoff = now - 86400
        self._score_history[locode] = [(t, s) for t, s in history if t > cutoff]

    def get_score_delta(self, locode: str) -> float | None:
        """Get 24h score change. Returns None if insufficient history."""
        history = self._score_history.get(locode, [])
        if len(history) < 2:
            return None
        current = history[-1][1]
        oldest = history[0][1]
        return round(current - oldest, 1)
