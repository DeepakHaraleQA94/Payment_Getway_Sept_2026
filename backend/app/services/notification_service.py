"""Notifications service (foundation). Logs events; pluggable channel adapters later."""
import logging

logger = logging.getLogger("cloudpay.notifications")


def notify(*, tenant_id, event: str, payload: dict) -> dict:
    logger.info("notification tenant=%s event=%s payload=%s", tenant_id, event, payload)
    return {"delivered": True, "channel": "log", "event": event}
