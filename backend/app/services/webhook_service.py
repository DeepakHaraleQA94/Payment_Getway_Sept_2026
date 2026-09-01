"""Webhook dispatch service with HMAC signing + delivery inspector records."""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("cloudpay.webhooks")


def sign_payload(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


async def _attempt(delivery: WebhookDelivery, url: str, secret: str, body: str) -> None:
    delivery.attempts += 1
    delivery.target_url = url
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-CloudPay-Event": delivery.event,
                    "X-CloudPay-Signature": f"sha256={sign_payload(secret, body)}",
                },
            )
        delivery.response_code = resp.status_code
        delivery.status = "success" if 200 <= resp.status_code < 300 else "failed"
        if delivery.status == "failed":
            delivery.error = f"HTTP {resp.status_code}"
    except Exception as exc:  # network error, unreachable endpoint, etc.
        delivery.status = "failed"
        delivery.error = str(exc)[:400]


async def dispatch(db: AsyncSession, *, tenant_id, event: str, data: dict) -> None:
    """Send an event to all enabled endpoints subscribed to it; record each attempt."""
    res = await db.execute(
        select(WebhookEndpoint).where(
            WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.enabled.is_(True)
        )
    )
    endpoints = res.scalars().all()
    body = json.dumps(
        {"event": event, "created_at": datetime.now(timezone.utc).isoformat(), "data": data},
        default=str,
    )

    matched = [e for e in endpoints if not e.events or event in e.events]
    if not matched:
        # Still record the event so the inspector shows activity even with no endpoint.
        db.add(WebhookDelivery(tenant_id=tenant_id, endpoint_id=None, event=event,
                               payload=json.loads(body), status="no_endpoint"))
        await db.flush()
        return

    for ep in matched:
        delivery = WebhookDelivery(tenant_id=tenant_id, endpoint_id=ep.id, event=event,
                                   payload=json.loads(body), status="pending")
        db.add(delivery)
        await db.flush()
        await _attempt(delivery, ep.url, ep.secret, body)
    await db.flush()
