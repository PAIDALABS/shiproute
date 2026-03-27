import time
import pytest

from congestion_engine import MONITORED_PORTS, CongestionEngine


def test_monitored_ports_have_required_fields():
    for locode, port in MONITORED_PORTS.items():
        assert "name" in port
        assert "lat" in port
        assert "lon" in port
        assert "bbox" in port
        bbox = port["bbox"]
        assert len(bbox) == 2
        assert len(bbox[0]) == 2
        assert len(bbox[1]) == 2
        assert bbox[0][0] < bbox[1][0]  # south < north
        assert bbox[0][1] < bbox[1][1]  # west < east


def test_five_monitored_ports():
    assert len(MONITORED_PORTS) == 5
    assert "INKAN" in MONITORED_PORTS
    assert "INKAK" in MONITORED_PORTS
    assert "INVTZ" in MONITORED_PORTS
    assert "MGTNR" in MONITORED_PORTS
    assert "MGTLE" in MONITORED_PORTS


def test_classify_anchored_vessel():
    engine = CongestionEngine()
    engine.update_vessel(
        mmsi=123456789, lat=22.95, lon=70.18,
        speed=0.3, course=180.0, heading=175,
        nav_status=1, ship_type=70, name="TEST VESSEL",
        timestamp=time.time(),
    )
    port_data = engine.get_port_data("INKAN")
    assert port_data is not None
    vessels = port_data["vessels"]
    assert len(vessels) == 1
    assert vessels[123456789]["state"] == "ANCHORED"


def test_classify_approaching_vessel():
    engine = CongestionEngine()
    engine.update_vessel(
        mmsi=111111111, lat=22.90, lon=70.15,
        speed=8.0, course=45.0, heading=44,
        nav_status=0, ship_type=70, name="FAST VESSEL",
        timestamp=time.time(),
    )
    port_data = engine.get_port_data("INKAN")
    vessels = port_data["vessels"]
    assert len(vessels) == 1
    # Speed is 8.0 which is > 5.0, so this should be TRANSITING
    assert vessels[111111111]["state"] == "TRANSITING"


def test_classify_berthed_vessel():
    engine = CongestionEngine()
    engine.update_vessel(
        mmsi=222222222, lat=22.98, lon=70.22,
        speed=0.1, course=0.0, heading=0,
        nav_status=5, ship_type=70, name="DOCKED VESSEL",
        timestamp=time.time(),
    )
    port_data = engine.get_port_data("INKAN")
    vessels = port_data["vessels"]
    assert len(vessels) == 1
    assert vessels[222222222]["state"] == "BERTHED"


def test_vessel_outside_all_ports_ignored():
    engine = CongestionEngine()
    engine.update_vessel(
        mmsi=999999999, lat=0.0, lon=0.0,
        speed=12.0, course=90.0, heading=90,
        nav_status=0, ship_type=70, name="OCEAN VESSEL",
        timestamp=time.time(),
    )
    for locode in MONITORED_PORTS:
        port_data = engine.get_port_data(locode)
        assert len(port_data["vessels"]) == 0


def test_congestion_score_empty_port():
    engine = CongestionEngine()
    metrics = engine.get_port_metrics("INKAN")
    assert metrics["congestion_score"] == 0
    assert metrics["severity"] == "LOW"
    assert metrics["anchored_count"] == 0
    assert metrics["berthed_count"] == 0


def test_congestion_score_with_anchored_vessels():
    engine = CongestionEngine()
    now = time.time()
    for i in range(5):
        engine.update_vessel(
            mmsi=100000000 + i,
            lat=22.95 + i * 0.001, lon=70.18,
            speed=0.2, course=0.0, heading=0,
            nav_status=1, ship_type=70, name=f"ANCHOR {i}",
            timestamp=now,
        )
    metrics = engine.get_port_metrics("INKAN")
    assert metrics["anchored_count"] == 5
    assert metrics["congestion_score"] > 0
    assert metrics["severity"] in ("LOW", "MODERATE", "HIGH", "SEVERE")


def test_prune_stale_vessels():
    engine = CongestionEngine()
    old_time = time.time() - 3600
    engine.update_vessel(
        mmsi=444444444, lat=22.95, lon=70.18,
        speed=0.2, course=0.0, heading=0,
        nav_status=1, ship_type=70, name="OLD VESSEL",
        timestamp=old_time,
    )
    engine.prune_stale_vessels(max_age_seconds=1800)
    port_data = engine.get_port_data("INKAN")
    assert len(port_data["vessels"]) == 0
