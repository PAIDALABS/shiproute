"""Tests for market_intel module."""

import time
from congestion_engine import CongestionEngine, MONITORED_PORTS
from market_intel import get_market_overview, get_africa_corridor


def _place_vessel(
    engine: CongestionEngine,
    mmsi: int,
    lat: float,
    lon: float,
    name: str = "TEST VESSEL",
    ship_type: str = "Cargo",
    type_specific: str = "General Cargo",
    destination: str = "",
    speed: float = 0.2,
    nav_status: int = 1,
) -> None:
    """Helper to place a test vessel inside a monitored port's bbox."""
    engine.update_vessel(
        mmsi=mmsi,
        lat=lat,
        lon=lon,
        speed=speed,
        course=0.0,
        heading=0,
        nav_status=nav_status,
        ship_type=ship_type,
        name=name,
        timestamp=time.time(),
        destination=destination,
        type_specific=type_specific,
    )


class TestGetMarketOverview:
    def test_market_overview_empty_engine(self):
        """Fresh CongestionEngine -> dict with 'ports' list of correct length, all congestion scores 0."""
        engine = CongestionEngine()
        result = get_market_overview(engine)

        assert "ports" in result
        assert len(result["ports"]) == len(MONITORED_PORTS)

        for port in result["ports"]:
            assert port["congestion_score"] == 0
            assert port["total_vessels"] == 0

        assert result["summary"]["total_vessels_all_ports"] == 0
        assert result["summary"]["ports_monitored"] == len(MONITORED_PORTS)


class TestGetAfricaCorridor:
    def test_africa_corridor_empty_engine(self):
        """Fresh CongestionEngine -> dict with total_vessels=0."""
        engine = CongestionEngine()
        result = get_africa_corridor(engine)

        assert result["total_vessels"] == 0
        assert result["vessels"] == []
        assert result["corridor"] == "India \u2194 East Africa"

    def test_africa_corridor_direction_filter(self):
        """Vessel at MGTNR (Madagascar) with destination SINGAPORE and type Cargo
        should NOT be in corridor vessels (not India-bound)."""
        engine = CongestionEngine()
        # Place vessel inside MGTNR bbox (lat=-18.15, lon=49.40)
        _place_vessel(
            engine,
            mmsi=123456789,
            lat=-18.15,
            lon=49.40,
            name="NON CORRIDOR SHIP",
            ship_type="Cargo",
            type_specific="Cargo",
            destination="SINGAPORE",
        )

        # Verify vessel was actually placed at MGTNR
        assert 123456789 in engine._port_vessels.get("MGTNR", {})

        result = get_africa_corridor(engine)
        corridor_mmsis = [v["mmsi"] for v in result["vessels"]]
        assert 123456789 not in corridor_mmsis
        assert result["total_vessels"] == 0

    def test_africa_corridor_india_to_africa(self):
        """Vessel at INKAN with destination MOMBASA and type General Cargo
        SHOULD be in corridor vessels."""
        engine = CongestionEngine()
        # Place vessel inside INKAN bbox (lat=22.95, lon=70.18)
        _place_vessel(
            engine,
            mmsi=987654321,
            lat=22.95,
            lon=70.18,
            name="CORRIDOR SHIP",
            ship_type="Cargo",
            type_specific="General Cargo",
            destination="MOMBASA",
        )

        # Verify vessel was actually placed at INKAN
        assert 987654321 in engine._port_vessels.get("INKAN", {})

        result = get_africa_corridor(engine)
        corridor_mmsis = [v["mmsi"] for v in result["vessels"]]
        assert 987654321 in corridor_mmsis
        assert result["total_vessels"] >= 1

        # Verify vessel details in the result
        vessel = [v for v in result["vessels"] if v["mmsi"] == 987654321][0]
        assert vessel["name"] == "CORRIDOR SHIP"
        assert vessel["destination"] == "MOMBASA"
        assert vessel["port_locode"] == "INKAN"
