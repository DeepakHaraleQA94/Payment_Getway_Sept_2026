"""Hosted checkout: authenticated session creation, public pay page, API-key API."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, get_tenant_from_api_key, require_feature, require_permission, resolve_tenant_id
from app.core.ratelimit import rate_limit
from app.core.security import generate_token
from app.models.commerce import CheckoutSession
from app.models.tenant import Tenant
from app.schemas import CheckoutCreate, CheckoutOut, CheckoutPay
from app.services import payment_engine

router = APIRouter(prefix="/api", tags=["checkout"])


async def _create_session(db: AsyncSession, *, tenant_id, body: CheckoutCreate, actor_id=None) -> CheckoutSession:
    session = CheckoutSession(
        tenant_id=tenant_id,
        token=generate_token(24),
        reference=body.reference or f"CHK-{uuid.uuid4().hex[:8].upper()}",
        amount_minor=body.amount_minor,
        currency=body.currency,
        description=body.description,
        customer_email=body.customer_email,
        success_url=body.success_url,
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        created_by=str(actor_id) if actor_id else None,
    )
    db.add(session)
    await db.flush()
    return session


# ---- Dashboard (authenticated) ----
@router.get("/checkout/sessions", response_model=list[CheckoutOut])
async def list_sessions(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(CheckoutSession).where(CheckoutSession.tenant_id == tid)
                           .order_by(CheckoutSession.created_at.desc()).limit(200))
    return res.scalars().all()


@router.post("/checkout/sessions", response_model=CheckoutOut)
async def create_session(body: CheckoutCreate, tenant_id: str | None = None,
                         db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("checkout.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    await require_feature(db, tid, "checkout", bypass=user.is_superadmin)
    session = await _create_session(db, tenant_id=tid, body=body, actor_id=user.id)
    await db.commit()
    await db.refresh(session)
    return session


# ---- API-key programmatic creation (from the tenant's own site) ----
@router.post("/v1/checkout/sessions")
async def api_create_session(body: CheckoutCreate, request_tenant: Tenant = Depends(get_tenant_from_api_key),
                             db: AsyncSession = Depends(get_db),
                             _rl: None = Depends(rate_limit("checkout_api_create", 60, 60))):
    session = await _create_session(db, tenant_id=request_tenant.id, body=body)
    await db.commit()
    await db.refresh(session)
    return {"id": str(session.id), "token": session.token, "status": session.status,
            "checkout_url": f"/checkout/{session.token}"}


# ---- Public checkout page (no auth) ----
@router.get("/public/checkout/{token}")
async def public_get_session(token: str, db: AsyncSession = Depends(get_db),
                             _rl: None = Depends(rate_limit("checkout_get", 60, 60))):
    res = await db.execute(select(CheckoutSession).where(CheckoutSession.token == token))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    tenant = await db.get(Tenant, session.tenant_id)
    logo_url = f"/api/public/files/{tenant.brand_logo_file_id}" if tenant and tenant.brand_logo_file_id else None
    expired = session.expires_at and session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) \
        if session.expires_at and session.expires_at.tzinfo is None else \
        (session.expires_at and session.expires_at < datetime.now(timezone.utc))
    return {
        "token": session.token,
        "merchant": tenant.name if tenant else "Merchant",
        "brand_accent": tenant.brand_accent if tenant else "#3B82F6",
        "logo_url": logo_url,
        "reference": session.reference,
        "amount_minor": session.amount_minor,
        "currency": session.currency,
        "description": session.description,
        "status": "expired" if (expired and session.status == "open") else session.status,
        "success_url": session.success_url,
    }


@router.post("/public/checkout/{token}/pay")
async def public_pay(token: str, body: CheckoutPay, db: AsyncSession = Depends(get_db),
                     _rl: None = Depends(rate_limit("checkout_pay", 20, 60))):
    res = await db.execute(select(CheckoutSession).where(CheckoutSession.token == token))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    if session.status == "paid":
        raise HTTPException(status_code=400, detail="This checkout has already been paid")
    if session.expires_at:
        exp = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="This checkout has expired")

    payment = await payment_engine.create_payment(
        db, tenant_id=session.tenant_id, actor=None, reference=session.reference,
        amount_minor=session.amount_minor, currency=session.currency, provider_key=session.provider_key,
        description=session.description, customer_email=body.customer_email or session.customer_email,
        idempotency_key=f"checkout_{session.token}", metadata={"source": "hosted_checkout"},
    )
    if payment.status in ("succeeded", "captured"):
        session.status = "paid"
        session.payment_id = payment.id
        await db.commit()
        return {"status": "paid", "payment_id": str(payment.id), "success_url": session.success_url}
    await db.commit()
    raise HTTPException(status_code=402, detail="Payment was declined by the sandbox provider")
