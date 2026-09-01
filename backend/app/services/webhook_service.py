"""Webhook dispatch with HMAC signing, retry/backoff and replay + inspector records."""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.commerce import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("cloudpay.webhooks")


def sign_payload(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_retryable(status_code: int | None) -> bool:
    """Retry only transient failures: 5xx, 429, and network/timeout (no code)."""
    if status_code is None:
        return True  # network error / timeout
    if status_code == 429 or 500 <= status_code < 600:
        return True
    return False  # permanent 4xx (validation/auth) are not auto-retried


def _backoff_delay(attempts: int) -> int:
    delay = settings.webhook_base_delay_sec * (2 ** max(attempts - 1, 0))
    return min(delay, settings.webhook_max_backoff_sec)


async def _attempt(db: AsyncSession, delivery: WebhookDelivery, secret: str) -> None:
    delivery.attempts += 1
    delivery.last_attempt_at = datetime.now(timezone.utc)
    body = json.dumps(delivery.payload, default=str)
    status_code: int | None = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                delivery.target_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-CloudPay-Event": delivery.event,
                    "X-CloudPay-Event-Id": str(delivery.event_id),
                    "X-CloudPay-Signature": f"sha256={sign_payload(secret, body)}",
                },
            )
        status_code = resp.status_code
        delivery.response_code = status_code
        if 200 <= status_code < 300:
            delivery.status = "success"
            delivery.retryable = False
            delivery.next_attempt_at = None
            return
        delivery.error = f"HTTP {status_code}"
    except Exception as exc:
        delivery.error = str(exc)[:400]
        delivery.response_code = None

    # Failure path: decide whether to retry.
    if _is_retryable(status_code) and delivery.attempts < delivery.max_attempts:
        delivery.status = "retrying"
        delivery.retryable = True
        delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=_backoff_delay(delivery.attempts))
    elif _is_retryable(status_code):
        delivery.status = "exhausted"
        delivery.retryable = False
        delivery.next_attempt_at = None
    else:
        delivery.status = "failed"
        delivery.retryable = False
        delivery.next_attempt_at = None


async def _endpoint_secret(db: AsyncSession, endpoint_id) -> str:
    if not endpoint_id:
        return ""
    ep = await db.get(WebhookEndpoint, endpoint_id)
    return ep.secret if ep else ""


async def dispatch(db: AsyncSession, *, tenant_id, event: str, data: dict) -> None:
    """Send an event to all enabled endpoints subscribed to it; record each attempt."""
    res = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.enabled.is_(True)
        )
    )
    endpoints = res.scalars().all()
    event_id = uuid.uuid4()
    payload = {"event": event, "event_id": str(event_id),
               "created_at": datetime.now(timezone.utc).isoformat(), "data": data}

    matched = [e for e in endpoints if not e.events or event in e.events]
    if not matched:
        db.add(WebhookDelivery(tenant_id=tenant_id, endpoint_id=None, event_id=event_id, event=event,
                               payload=payload, status="no_endpoint",
                               max_attempts=settings.webhook_max_attempts))
        await db.flush()
        return

    for ep in matched:
        delivery = WebhookDelivery(tenant_id=tenant_id, endpoint_id=ep.id, event_id=event_id, event=event,
                                   target_url=ep.url, payload=payload, status="pending",
                                   max_attempts=settings.webhook_max_attempts)
        db.add(delivery)
        await db.flush()
        await _attempt(db, delivery, ep.secret)
    await db.flush()


async def process_due_retries(db: AsyncSession) -> int:
    """Re-attempt deliveries whose backoff window has elapsed. Returns count processed."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.status == "retrying",
            WebhookDelivery.retryable.is_(True),
            WebhookDelivery.next_attempt_at <= now,
        ).limit(50)
    )
    due = res.scalars().all()
    for delivery in due:
        secret = await _endpoint_secret(db, delivery.endpoint_id)
        await _attempt(db, delivery, secret)
    if due:
        await db.commit()
    return len(due)


async def replay(db: AsyncSession, *, original: WebhookDelivery) -> WebhookDelivery:
    """Create a NEW delivery attempt preserving the original event_id (idempotent resend).

    Replay only re-sends the webhook HTTP call; it never re-processes payments/refunds,
    so it cannot cause duplicate financial mutations.
    """
    new_delivery = WebhookDelivery(
        tenant_id=original.tenant_id,
        endpoint_id=original.endpoint_id,
        event_id=original.event_id,  # preserved
        event=original.event,
        target_url=original.target_url,
        payload=original.payload,
        status="pending",
        is_replay=True,
        max_attempts=settings.webhook_max_attempts,
    )
    db.add(new_delivery)
    await db.flush()
    secret = await _endpoint_secret(db, original.endpoint_id)
    if new_delivery.target_url:
        await _attempt(db, new_delivery, secret)
    else:
        new_delivery.status = "no_endpoint"
    await db.flush()
    return new_delivery
