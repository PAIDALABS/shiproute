"""Shared test fixtures for ShipRoute test suite."""

import time
import pytest
from congestion_engine import CongestionEngine, MONITORED_PORTS


@pytest.fixture
def fresh_engine():
    """Create a fresh CongestionEngine with no vessels."""
    return CongestionEngine()


@pytest.fixture
def seeded_engine():
    """Create a CongestionEngine with test vessels in key ports."""
    engine = CongestionEngine()
    now = time.time()

    # Place vessels at Kandla (INKAN: lat=22.98, lon=70.22)
    for i in range(5):
        engine.update_vessel(
            mmsi=200000001 + i, lat=22.95 + i * 0.001, lon=70.18,
            speed=0.2, course=0.0, heading=0,
            nav_status=1, ship_type="Cargo", name=f"TEST ANCHOR {i}",
            timestamp=now,
            country_iso="IN", destination="MOMBASA",
        )

    # Place 2 berthed vessels at Kandla
    for i in range(2):
        engine.update_vessel(
            mmsi=200000010 + i, lat=22.98, lon=70.22,
            speed=0.1, course=0.0, heading=0,
            nav_status=5, ship_type="Cargo", name=f"TEST BERTH {i}",
            timestamp=now,
            country_iso="IN",
        )

    return engine


@pytest.fixture
def make_vessel():
    """Factory fixture for creating vessel dicts for update_vessel."""
    _counter = [200000100]

    def _make(engine, locode, **overrides):
        port = MONITORED_PORTS.get(locode)
        if not port:
            raise ValueError(f"Unknown locode: {locode}")
        _counter[0] += 1
        defaults = {
            "mmsi": _counter[0],
            "lat": port["lat"],
            "lon": port["lon"],
            "speed": 0.2,
            "course": 0.0,
            "heading": 0,
            "nav_status": 1,
            "ship_type": "Cargo",
            "name": f"VESSEL-{_counter[0]}",
            "timestamp": time.time(),
        }
        defaults.update(overrides)
        engine.update_vessel(**defaults)
        return defaults["mmsi"]

    return _make
