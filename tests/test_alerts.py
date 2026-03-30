"""Tests for the alerts module — severity change detection & buffer."""

import time
import pytest

from alerts import (
    check_alerts,
    get_recent_alerts,
    _last_severity,
    _last_alert_time,
    _recent_alerts,
    ALERT_COOLDOWN_SECONDS,
)
from congestion_engine import MONITORED_PORTS


@pytest.fixture(autouse=True)
def reset_alert_state():
    """Reset module-level alert state between tests."""
    _last_severity.clear()
    _last_alert_time.clear()
    _recent_alerts.clear()
    yield


# ── Helpers ────────────────────────────────────────────────────────────────

def _add_anchored_vessels(engine, locode, count, *, mmsi_start=300000000,
                          wait_hours=0):
    """Add *count* anchored vessels outside the inner radius of *locode*.

    Places vessels ~3 nm from port centre so they classify as ANCHORED
    (not BERTHED).  Optionally back-dates state_since for wait-time
    contribution.
    """
    port = MONITORED_PORTS[locode]
    # Offset ~3 nm north of port centre (1 degree lat ≈ 60 nm)
    anchor_lat = port["lat"] + 0.05
    anchor_lon = port["lon"]
    now = time.time()
    for i in range(count):
        engine.update_vessel(
            mmsi=mmsi_start + i,
            lat=anchor_lat + i * 0.0001,
            lon=anchor_lon,
            speed=0.2,
            course=0.0,
            heading=0,
            nav_status=1,
            ship_type="Cargo",
            name=f"ALERT-TEST-{i}",
            timestamp=now - (wait_hours * 3600),
        )


def _remove_all_vessels(engine, locode):
    """Remove all vessels from a port by pruning with zero max-age."""
    # Set all vessel timestamps far in the past so they prune
    for mmsi, v in engine._port_vessels[locode].items():
        v["last_seen"] = 0.0
    engine.prune_stale_vessels(max_age_seconds=0)


# ── Tests ──────────────────────────────────────────────────────────────────

def test_check_alerts_no_change(fresh_engine):
    """Second call on empty engine returns no alerts (LOW -> LOW = no change)."""
    first = check_alerts(fresh_engine)
    # First call sets baseline; may or may not have alerts (all LOW initially)
    second = check_alerts(fresh_engine)
    assert second == [], "No severity change should produce no alerts"


def test_check_alerts_escalation(fresh_engine):
    """Adding many anchored vessels should trigger an escalation alert."""
    locode = "INKAN"

    # Set baseline — empty port → LOW severity
    check_alerts(fresh_engine)

    # Add enough anchored vessels with wait time to push above MODERATE
    _add_anchored_vessels(fresh_engine, locode, count=10, wait_hours=24)

    alerts = check_alerts(fresh_engine)

    escalations = [a for a in alerts if a["type"] == "congestion_escalation"]
    assert len(escalations) >= 1, "Should detect at least one escalation"

    esc = next(a for a in escalations if a["locode"] == locode)
    assert esc["previous_severity"] == "LOW"
    assert esc["current_severity"] in ("MODERATE", "HIGH", "SEVERE")
    assert esc["congestion_score"] > 0


def test_check_alerts_de_escalation(fresh_engine):
    """Removing vessels from a HIGH/SEVERE port triggers de-escalation."""
    locode = "INKAN"

    # Build up to HIGH/SEVERE
    _add_anchored_vessels(fresh_engine, locode, count=15, wait_hours=48)
    baseline = check_alerts(fresh_engine)

    # Verify baseline reached HIGH or SEVERE
    baseline_sev = _last_severity.get(locode)
    assert baseline_sev in ("HIGH", "SEVERE"), (
        f"Expected HIGH or SEVERE baseline, got {baseline_sev}"
    )

    # Remove all vessels → severity drops to LOW
    _remove_all_vessels(fresh_engine, locode)

    alerts = check_alerts(fresh_engine)
    de_escs = [a for a in alerts
               if a["type"] == "congestion_de_escalation" and a["locode"] == locode]
    assert len(de_escs) == 1, "Should detect de-escalation from HIGH/SEVERE"
    assert de_escs[0]["current_severity"] == "LOW"


def test_get_recent_alerts_readonly(fresh_engine):
    """get_recent_alerts returns a copy; calling it should not mutate state."""
    # Trigger an alert so there is something in the buffer
    _add_anchored_vessels(fresh_engine, "INKAN", count=10, wait_hours=24)
    check_alerts(fresh_engine)  # baseline
    check_alerts(fresh_engine)  # should not produce more since no change

    snapshot_a = get_recent_alerts()
    snapshot_b = get_recent_alerts()

    assert snapshot_a == snapshot_b, "Multiple reads should return identical data"
    assert len(snapshot_a) == len(_recent_alerts), "Snapshot length should match buffer"

    # Mutating the returned list must not affect the module buffer
    snapshot_a.append({"fake": True})
    assert len(get_recent_alerts()) == len(snapshot_b), (
        "Appending to snapshot should not grow the internal buffer"
    )


def test_alert_cooldown(fresh_engine):
    """Same escalation within cooldown window should be suppressed."""
    locode = "INKAN"

    # Baseline
    check_alerts(fresh_engine)

    # Escalate → MODERATE+
    _add_anchored_vessels(fresh_engine, locode, count=10, wait_hours=24)
    first_alerts = check_alerts(fresh_engine)
    assert any(a["locode"] == locode and a["type"] == "congestion_escalation"
               for a in first_alerts), "First escalation should fire"

    # Reset severity back to LOW manually, then re-escalate immediately.
    # The cooldown timestamp should suppress a second escalation alert.
    _last_severity[locode] = "LOW"
    second_alerts = check_alerts(fresh_engine)
    escalations = [a for a in second_alerts
                   if a["locode"] == locode and a["type"] == "congestion_escalation"]
    assert len(escalations) == 0, (
        "Second escalation within cooldown window should be suppressed"
    )


def test_alert_has_required_fields(fresh_engine):
    """Triggered alert must contain all required fields."""
    locode = "INKAN"
    required_fields = {
        "type", "locode", "port", "previous_severity",
        "current_severity", "congestion_score", "timestamp", "message",
    }

    # Baseline
    check_alerts(fresh_engine)

    # Trigger escalation
    _add_anchored_vessels(fresh_engine, locode, count=10, wait_hours=24)
    alerts = check_alerts(fresh_engine)

    assert len(alerts) >= 1, "Should produce at least one alert"
    for alert in alerts:
        missing = required_fields - alert.keys()
        assert not missing, f"Alert missing fields: {missing}"
        assert isinstance(alert["timestamp"], float)
        assert isinstance(alert["congestion_score"], (int, float))
        assert alert["message"]  # non-empty string
