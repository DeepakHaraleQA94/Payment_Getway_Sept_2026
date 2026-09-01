"""Webhook endpoints + delivery inspector."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
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
    return [{"id": str(x.id), "event": x.event, "status": x.status, "target_url": x.target_url,
             "response_code": x.response_code, "attempts": x.attempts, "error": x.error,
             "payload": x.payload, "created_at": x.created_at.isoformat()} for x in d]
