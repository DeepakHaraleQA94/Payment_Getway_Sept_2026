"""Webhook endpoints + delivery inspector.

Provider-agnostic: this module owns OUTBOUND tenant webhook delivery only. Inbound
provider webhooks are handled generically by the provider plugin contract via
`POST /api/providers/{provider_key}/webhook` (see routers/config.py); no provider-specific
logic lives here.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_feature, require_permission, resolve_tenant_id
from app.core.security import generate_token
from app.models.commerce import WebhookDelivery, WebhookEndpoint
from app.schemas import WebhookCreate, WebhookOut
from app.services import webhook_service

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

SUPPORTED_EVENTS = ["payment.succeeded", "payment.failed", "refund.succeeded", "refund.failed"]


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
    await require_feature(db, tid, "webhooks", bypass=user.is_superadmin)
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
