"""Notifications service. Logs every event; routes email-worthy events to the email adapter.

Password-reset and email-verification links are delivered via `email_service` (Resend when
configured, noop otherwise). Nothing here stores or logs secrets beyond the one-time link the
user themselves requested.
"""
import logging

from app.services import email_service

logger = logging.getLogger("cloudpay.notifications")

# event -> (subject, body builder from payload). Only events with a recipient email are emailed.
_EMAIL_EVENTS = {
    "auth.password_reset_requested": (
        "Reset your CloudPay password",
        lambda p: ("We received a request to reset your CloudPay password.\n\n"
                   f"Reset it here: {p.get('reset_link')}\n\n"
                   "If you didn't request this, you can safely ignore this email."),
    ),
    "auth.email_verification_requested": (
        "Verify your CloudPay email",
        lambda p: f"Confirm your email address to finish setting up CloudPay:\n\n{p.get('verify_link')}",
    ),
    "auth.password_changed": (
        "Your CloudPay password was changed",
        lambda p: "This is a security notice: your CloudPay password was just changed. "
                  "If this wasn't you, contact your administrator immediately.",
    ),
}


def notify(*, tenant_id, event: str, payload: dict) -> dict:
    logger.info("notification tenant=%s event=%s", tenant_id, event)
    recipient = (payload or {}).get("email")
    spec = _EMAIL_EVENTS.get(event)
    if spec and recipient:
        subject, build_body = spec
        result = email_service.send_email(to=recipient, subject=subject, body=build_body(payload))
        return {"delivered": result.get("delivered", False), "channel": "email",
                "event": event, "status": result.get("status")}
    return {"delivered": True, "channel": "log", "event": event}
