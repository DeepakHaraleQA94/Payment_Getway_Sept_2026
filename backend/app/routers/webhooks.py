"""Webhook endpoints + delivery inspector."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.core.security import generate_token
from app.models.commerce import WebhookDelivery, WebhookEndpoint
from app.models.payment import Payment
from app.providers.stripe_provider import StripeProvider
from app.schemas import WebhookCreate, WebhookOut
from app.services import payment_state, webhook_service

logger = logging.getLogger("cloudpay.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

SUPPORTED_EVENTS = ["payment.succeeded", "payment.failed", "refund.succeeded", "refund.failed"]

# Map Stripe event types to the internal payment status we reconcile to.
_STRIPE_STATUS_MAP = {
    "payment_intent.succeeded": "succeeded",
    "payment_intent.payment_failed": "failed",
    "payment_intent.canceled": "cancelled",
}


@router.get("/events")
async def supported_events(user=Depends(get_current_user)):
    return {"events": SUPPORTED_EVENTS}


@router.get("/endpoints", response_model=list[WebhookOut])
async def list_endpoints(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tid)
                           .order_by(WebhookEndpoint.created_at.desc()))
    return res.scalars().all()


@router.post("/endpoints", response_model=WebhookOut)
async def create_endpoint(body: WebhookCreate, tenant_id: str | None = None,
                          db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("webhook.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    ep = WebhookEndpoint(tenant_id=tid, url=body.url, description=body.description,
                         events=body.events, secret=f"whsec_{generate_token(16)}",
                         enabled=True, created_by=str(user.id))
    db.add(ep)
    await db.flush()
    await record_audit(db, action="webhook.create", resource_type="webhook_endpoint", resource_id=ep.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes={"url": body.url})
    await db.commit()
    await db.refresh(ep)
    return ep


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("webhook.manage"))):
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if not ep or (not user.is_superadmin and ep.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Endpoint not found")
    await db.delete(ep)
    await db.commit()
    return {"ok": True}


@router.post("/endpoints/{endpoint_id}/test")
async def test_endpoint(endpoint_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        user=Depends(require_permission("webhook.manage"))):
    ep = await db.get(WebhookEndpoint, endpoint_id)
    if not ep or (not user.is_superadmin and ep.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Endpoint not found")
    await webhook_service.dispatch(db, tenant_id=ep.tenant_id, event="payment.succeeded",
                                   data={"test": True, "message": "CloudPay test event"})
    await db.commit()
    return {"ok": True}


@router.get("/deliveries")
async def list_deliveries(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(WebhookDelivery).where(WebhookDelivery.tenant_id == tid)
                           .order_by(WebhookDelivery.created_at.desc()).limit(200))
    d = res.scalars().all()
    return [{"id": str(x.id), "event": x.event, "event_id": str(x.event_id), "status": x.status,
             "target_url": x.target_url, "response_code": x.response_code, "attempts": x.attempts,
             "max_attempts": x.max_attempts, "retryable": x.retryable, "is_replay": x.is_replay,
             "error": x.error, "payload": x.payload,
             "last_attempt_at": x.last_attempt_at.isoformat() if x.last_attempt_at else None,
             "next_attempt_at": x.next_attempt_at.isoformat() if x.next_attempt_at else None,
             "created_at": x.created_at.isoformat()} for x in d]


@router.post("/deliveries/{delivery_id}/replay")
async def replay_delivery(delivery_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("webhook.manage"))):
    original = await db.get(WebhookDelivery, delivery_id)
    if not original or (not user.is_superadmin and original.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Delivery not found")
    new_delivery = await webhook_service.replay(db, original=original)
    await record_audit(db, action="webhook.replay", resource_type="webhook_delivery",
                       resource_id=new_delivery.id, tenant_id=original.tenant_id,
                       actor_id=str(user.id), actor_email=user.email,
                       changes={"event_id": str(original.event_id), "original_delivery_id": str(original.id)})
    await db.commit()
    return {"id": str(new_delivery.id), "event_id": str(new_delivery.event_id), "status": new_delivery.status}



# ---- Inbound Stripe webhook (provider -> CloudPay) ----
@router.post("/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive Stripe events and reconcile payment status idempotently.

    Public endpoint (no session auth). When STRIPE_WEBHOOK_SECRET is configured the
    payload signature is verified; otherwise the raw JSON is parsed (verification is
    skipped until a secret is provided). Never posts ledger entries here — the
    synchronous charge flow owns financial mutations; this only reconciles status.
    """
    provider = StripeProvider()
    if not provider.configured or provider.is_live:
        raise HTTPException(status_code=503, detail="Stripe webhook unavailable")

    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")

    if settings.stripe_webhook_secret:
        try:
            event = provider.verify_webhook(payload, sig)
        except Exception as exc:
            logger.warning("Stripe webhook signature verification failed: %s", type(exc).__name__)
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        import json
        try:
            event = json.loads(payload or b"{}")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    intent_id = obj.get("id") if event_type.startswith("payment_intent.") else obj.get("payment_intent")

    target = _STRIPE_STATUS_MAP.get(event_type)
    if not target or not intent_id:
        return {"received": True, "ignored": True}

    res = await db.execute(select(Payment).where(Payment.provider_txn_id == intent_id,
                                                 Payment.provider_key == "stripe"))
    payment = res.scalar_one_or_none()
    if not payment:
        return {"received": True, "unmatched": True}

    prev = payment.status
    if prev == target:
        return {"received": True, "already": target}
    if not payment_state.can_transition(prev, target):
        # Not a valid transition (e.g. already refunded) — acknowledge without change.
        return {"received": True, "skipped": True}

    payment_state.validate_transition(prev, target)
    payment.status = target
    await record_audit(db, action="payment.webhook_reconcile", resource_type="payment",
                       resource_id=payment.id, tenant_id=payment.tenant_id, actor_id=None,
                       actor_email="stripe:webhook",
                       changes={"previous_state": prev, "new_state": target,
                                "event_type": event_type, "correlation_id": str(payment.id)})
    await db.commit()
    return {"received": True, "reconciled": True, "status": target}
