"""
Congestion alerting — tracks severity changes and fires webhooks/logs.
"""

import logging
import os
import time

import httpx

from congestion_engine import CongestionEngine, MONITORED_PORTS

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")

# Track last known severity per port
_last_severity: dict[str, str] = {}
_last_alert_time: dict[str, float] = {}
ALERT_COOLDOWN_SECONDS = 1800  # 30 min between alerts for same port

# Recent alerts buffer (read-only access via get_recent_alerts)
_recent_alerts: list[dict] = []
_MAX_RECENT = 100


def get_recent_alerts() -> list[dict]:
    """Read-only: return recent alerts without mutating state."""
    return list(_recent_alerts)


def _update_severity_baseline(locode: str, severity: str) -> None:
    """Update the last-known severity for a port (write operation)."""
    _last_severity[locode] = severity


def check_alerts(engine: CongestionEngine) -> list[dict]:
    """Detect severity changes and update baseline. Called only from background loop."""
    alerts: list[dict] = []
    now = time.time()

    for locode in MONITORED_PORTS:
        metrics = engine.get_port_metrics(locode)
        severity = metrics["severity"]
        prev = _last_severity.get(locode)
        port_name = MONITORED_PORTS[locode]["name"]

        # Detect severity escalation
        severity_order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3}
        if prev is not None and severity_order.get(severity, 0) > severity_order.get(prev, 0):
            # Cooldown check
            last_alert = _last_alert_time.get(locode, 0)
            if now - last_alert > ALERT_COOLDOWN_SECONDS:
                alert = {
                    "type": "congestion_escalation",
                    "locode": locode,
                    "port": port_name,
                    "previous_severity": prev,
                    "current_severity": severity,
                    "congestion_score": metrics["congestion_score"],
                    "anchored": metrics["anchored_count"],
                    "berthed": metrics["berthed_count"],
                    "timestamp": now,
                    "message": f"{port_name} congestion escalated from {prev} to {severity} "
                               f"(score: {metrics['congestion_score']}, "
                               f"queue: {metrics['anchored_count']} vessels)",
                }
                alerts.append(alert)
                _last_alert_time[locode] = now
                logger.warning("ALERT: %s", alert["message"])

        # Detect severity de-escalation
        if prev is not None and severity_order.get(severity, 0) < severity_order.get(prev, 0):
            if severity_order.get(prev, 0) >= 2:  # Only alert on de-escalation from HIGH or SEVERE
                alert = {
                    "type": "congestion_de_escalation",
                    "locode": locode,
                    "port": port_name,
                    "previous_severity": prev,
                    "current_severity": severity,
                    "congestion_score": metrics["congestion_score"],
                    "anchored": metrics["anchored_count"],
                    "berthed": metrics["berthed_count"],
                    "timestamp": now,
                    "message": f"{port_name} congestion eased from {prev} to {severity} "
                               f"(score: {metrics['congestion_score']})",
                }
                alerts.append(alert)
                logger.info("CLEARED: %s", alert["message"])

        _update_severity_baseline(locode, severity)

    # Buffer recent alerts for read-only access
    _recent_alerts.extend(alerts)
    if len(_recent_alerts) > _MAX_RECENT:
        del _recent_alerts[:-_MAX_RECENT]

    return alerts


async def fire_webhook(alerts: list[dict]) -> None:
    """Send alerts to configured webhook URL."""
    if not WEBHOOK_URL or not alerts:
        return
    try:
        async with httpx.AsyncClient() as client:
            for alert in alerts:
                await client.post(
                    WEBHOOK_URL,
                    json=alert,
                    timeout=10,
                )
    except Exception:
        logger.error("Failed to fire alert webhook", exc_info=True)


async def process_alerts(engine: CongestionEngine) -> list[dict]:
    """Check for alerts and fire webhooks. Returns triggered alerts."""
    alerts = check_alerts(engine)
    if alerts:
        await fire_webhook(alerts)
    return alerts
