"""
API integration tests using FastAPI's TestClient.

Only tests endpoints that work with the in-memory CongestionEngine
(no external Datalastic / database calls required).

NOTE: The module-level ``client`` shares a single CongestionEngine
instance (the one created at ``main`` import time).  Tests that only
*read* state are safe; tests that *write* vessels should use the
shared fixtures from ``conftest.py`` instead.
"""

import pytest
from starlette.testclient import TestClient
from main import app, engine


client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Port search
# ---------------------------------------------------------------------------


def test_port_search_returns_results():
    resp = client.get("/api/ports/search", params={"q": "Rotterdam"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    names = [p["name"] for p in data]
    assert any("Rotterdam" in n for n in names)


def test_port_search_empty_query():
    resp = client.get("/api/ports/search", params={"q": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_port_search_limit():
    resp = client.get("/api/ports/search", params={"q": "a", "limit": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) <= 3


# ---------------------------------------------------------------------------
# Live congestion
# ---------------------------------------------------------------------------


def test_live_congestion_returns_all_ports():
    resp = client.get("/api/congestion/live")
    assert resp.status_code == 200
    data = resp.json()
    assert "ports" in data
    assert len(data["ports"]) == 12


def test_live_congestion_port_detail():
    resp = client.get("/api/congestion/live/INKAN")
    assert resp.status_code == 200
    data = resp.json()
    assert "vessels" in data


def test_live_congestion_unknown_port():
    resp = client.get("/api/congestion/live/XXXXX")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


# ---------------------------------------------------------------------------
# Market intel
# ---------------------------------------------------------------------------


def test_market_overview():
    resp = client.get("/api/market/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert "ports" in data
    assert "summary" in data


def test_africa_corridor():
    resp = client.get("/api/market/africa-corridor")
    assert resp.status_code == 200
    data = resp.json()
    assert "corridor" in data


# ---------------------------------------------------------------------------
# Arrival advisory
# ---------------------------------------------------------------------------


def test_arrival_advisory_monitored_port():
    resp = client.get("/api/port-watch/INKAN/arrival-advisory")
    assert resp.status_code == 200
    data = resp.json()
    # When no vessels are tracked the endpoint returns monitored=False;
    # when vessels exist it returns congestion_score.
    assert "congestion_score" in data or "monitored" in data


def test_arrival_advisory_unknown_port():
    resp = client.get("/api/port-watch/XXXXX/arrival-advisory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["monitored"] is False


# ---------------------------------------------------------------------------
# Route calculation (multi-leg)
# ---------------------------------------------------------------------------


def test_multi_route_valid():
    payload = {
        "waypoints": [
            {"port_id": "NLRTM"},
            {"port_id": "BEANR"},
        ],
        "speed_knots": 14.0,
    }
    resp = client.post("/api/route/multi", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "legs" in data


def test_multi_route_invalid_speed():
    payload = {
        "waypoints": [
            {"port_id": "NLRTM"},
            {"port_id": "BEANR"},
        ],
        "speed_knots": 0,
    }
    resp = client.post("/api/route/multi", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_vessel_search_invalid_lat():
    resp = client.get("/api/vessels/search", params={"lat": 999, "lon": 0})
    assert resp.status_code == 422


def test_vessel_track_invalid_days():
    resp = client.get("/api/vessels/123456789/track", params={"days": 0})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_alerts_endpoint():
    resp = client.get("/api/alerts/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert "alerts" in data


# ---------------------------------------------------------------------------
# Response-shape validation
# ---------------------------------------------------------------------------


def test_live_congestion_response_structure():
    """Verify each port in the live congestion response has the expected fields."""
    resp = client.get("/api/congestion/live")
    data = resp.json()
    for port in data["ports"]:
        assert "locode" in port
        assert "name" in port
        assert "congestion_score" in port
        assert "severity" in port
        assert "total_vessels" in port
        assert "anchored_count" in port
        assert "avg_wait_hours" in port
        assert "score_delta_24h" in port


def test_health_returns_ok():
    """Health endpoint returns an ok or degraded status."""
    resp = client.get("/health")
    data = resp.json()
    assert data["status"] in ("ok", "degraded")


def test_live_status_endpoint():
    """Live status endpoint returns vessel tracking metadata."""
    resp = client.get("/api/congestion/live/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_vessels_tracked" in data
    assert "monitored_ports" in data


def test_fleet_overview():
    resp = client.get("/api/fleet/all")
    assert resp.status_code == 200
    data = resp.json()
    assert "vessels" in data
    assert "ports" in data
    assert "inbound" in data
    assert data["ports_monitored"] == 12
