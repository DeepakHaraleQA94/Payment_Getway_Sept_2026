"""Hosted checkout: authenticated session creation, public pay page, API-key API."""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, get_tenant_from_api_key, require_feature, require_permission, resolve_tenant_id
from app.core.ratelimit import rate_limit
from app.core.security import generate_token
from app.models.commerce import CheckoutSession
from app.models.acceptance import PaymentAcceptanceAccount
from app.models.payment import Payment
from app.models.tenant import Tenant
from app.schemas import CheckoutCreate, CheckoutOut, CheckoutPay, DemoUpiPay
from app.services import payment_engine

router = APIRouter(prefix="/api", tags=["checkout"])


async def _create_session(db: AsyncSession, *, tenant_id, body: CheckoutCreate, actor_id=None) -> CheckoutSession:
    session = CheckoutSession(
        tenant_id=tenant_id,
        token=generate_token(24),
        reference=body.reference or f"CHK-{uuid.uuid4().hex[:8].upper()}",
        amount_minor=body.amount_minor,
        currency=body.currency,
        provider_key=body.provider_key or "mock",
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
    # ADDITIVE: surface the tenant's highest-priority eligible UPI acceptance account as the pay-to
    # destination for a future UPI checkout. Display-only; no processing happens here.
    acc_res = await db.execute(
        select(PaymentAcceptanceAccount).where(
            PaymentAcceptanceAccount.tenant_id == session.tenant_id,
            PaymentAcceptanceAccount.enabled.is_(True),
            PaymentAcceptanceAccount.currency == session.currency,
        ).order_by(PaymentAcceptanceAccount.priority, PaymentAcceptanceAccount.created_at))
    acc = acc_res.scalars().first()
    acceptance = {"display_name": acc.display_name, "upi_vpa": acc.upi_vpa,
                  "bank_name": acc.bank_name, "account_type": acc.account_type,
                  "verification_status": acc.verification_status} if acc else None
    return {
        "token": session.token,
        "merchant": tenant.name if tenant else "Merchant",
        "brand_accent": tenant.brand_accent if tenant else "#3B82F6",
        "logo_url": logo_url,
        "reference": session.reference,
        "amount_minor": session.amount_minor,
        "currency": session.currency,
        "provider_key": session.provider_key,
        "description": session.description,
        "status": "expired" if (expired and session.status == "open") else session.status,
        "success_url": session.success_url,
        "acceptance": acceptance,
    }


@router.get("/public/receipts/{token}")
async def public_get_receipt(token: str, db: AsyncSession = Depends(get_db),
                             _rl: None = Depends(rate_limit("receipt_get", 60, 60))):
    """Public, non-enumerable hosted payment receipt (no auth). Never exposes secrets.

    The receipt token is a 192-bit random value stored on the payment when its receipt is sent;
    the payment is looked up by that token and must be in a final success state.
    """
    res = await db.execute(select(Payment).where(
        Payment.metadata_json["receipt_token"].astext == token,
        Payment.status.in_(("succeeded", "captured"))))
    payment = res.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Receipt not found")
    tenant = await db.get(Tenant, payment.tenant_id)
    logo_url = f"/api/public/files/{tenant.brand_logo_file_id}" if tenant and tenant.brand_logo_file_id else None
    return {
        "reference": payment.reference,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "status": payment.status,
        "provider_txn_id": payment.provider_txn_id,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "merchant": tenant.name if tenant else "Merchant",
        "support_email": tenant.contact_email if tenant else None,
        "brand_accent": tenant.brand_accent if tenant else "#3B82F6",
        "logo_url": logo_url,
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


# ---- Demo UPI checkout (sandbox-only walkthrough for the demo_upi provider) ----
_DEMO_UPI_APPS = [
    {"key": "phonepe", "label": "PhonePe"},
    {"key": "gpay", "label": "Google Pay"},
    {"key": "paytm", "label": "Paytm"},
    {"key": "bhim", "label": "BHIM"},
    {"key": "other", "label": "Other UPI App"},
    {"key": "qr", "label": "Scan QR"},
]


@router.get("/public/checkout/{token}/upi")
async def public_demo_upi_info(token: str, db: AsyncSession = Depends(get_db),
                               _rl: None = Depends(rate_limit("checkout_get", 60, 60))):
    """Demo UPI checkout context (public): the app choices + a scannable UPI deep-link payload.

    Sandbox demonstration only. The payload is a standard UPI deep link (contains no card data)
    built from the tenant's highest-priority eligible acceptance VPA, or a demo VPA fallback.
    """
    res = await db.execute(select(CheckoutSession).where(CheckoutSession.token == token))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    if session.provider_key != "demo_upi":
        raise HTTPException(status_code=400, detail="This checkout is not a Demo UPI checkout")
    tenant = await db.get(Tenant, session.tenant_id)
    acc_res = await db.execute(
        select(PaymentAcceptanceAccount).where(
            PaymentAcceptanceAccount.tenant_id == session.tenant_id,
            PaymentAcceptanceAccount.enabled.is_(True),
            PaymentAcceptanceAccount.currency == session.currency,
        ).order_by(PaymentAcceptanceAccount.priority, PaymentAcceptanceAccount.created_at))
    acc = acc_res.scalars().first()
    vpa = (acc.upi_vpa if acc and acc.upi_vpa else "cloudpay@mockbank")
    payee = (tenant.name if tenant else "CloudPay")
    amount = f"{session.amount_minor / 100:.2f}"
    upi_link = (f"upi://pay?pa={vpa}&pn={payee}&am={amount}"
                f"&cu={session.currency}&tn={session.reference}")
    return {
        "vpa": vpa,
        "payee": payee,
        "upi_link": upi_link,
        "apps": _DEMO_UPI_APPS,
        "amount_minor": session.amount_minor,
        "currency": session.currency,
    }


@router.post("/public/checkout/{token}/upi/pay")
async def public_demo_upi_pay(token: str, body: DemoUpiPay, db: AsyncSession = Depends(get_db),
                              _rl: None = Depends(rate_limit("checkout_pay", 20, 60))):
    """Authorize a DEMO UPI checkout (sandbox demo_upi provider only).

    outcome == "success" -> processes a genuine sandbox payment through the demo_upi provider
    (payment_method=upi, country=IN) and marks the session paid. outcome in {failed, pending} is
    a SIMULATED walkthrough state (no payment is recorded, the session stays open) so operators
    can see each UPI result screen. No real money moves; the demo_upi plugin is sandbox-only.
    """
    res = await db.execute(select(CheckoutSession).where(CheckoutSession.token == token))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    if session.provider_key != "demo_upi":
        raise HTTPException(status_code=400, detail="This checkout is not a Demo UPI checkout")
    if session.status == "paid":
        raise HTTPException(status_code=400, detail="This checkout has already been paid")
    if session.expires_at:
        exp = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="This checkout has expired")

    if body.outcome != "success":
        # Simulated walkthrough state — no payment recorded, session stays open.
        return {"status": "simulated", "outcome": body.outcome, "upi_app": body.upi_app}

    payment = await payment_engine.create_payment(
        db, tenant_id=session.tenant_id, actor=None, reference=session.reference,
        amount_minor=session.amount_minor, currency=session.currency, provider_key="demo_upi",
        environment="sandbox", description=session.description,
        customer_email=body.customer_email or session.customer_email,
        idempotency_key=f"demoupi_{session.token}", country="IN", payment_method="upi", flow="direct",
        metadata={"source": "demo_upi_checkout", "upi_app": body.upi_app},
    )
    if payment.status in ("succeeded", "captured"):
        session.status = "paid"
        session.payment_id = payment.id
        await db.commit()
        return {"status": "paid", "payment_id": str(payment.id), "success_url": session.success_url}
    await db.commit()
    raise HTTPException(status_code=402, detail="Payment was declined by the sandbox provider")


# Maps Resend event types to a compact delivery state stored on the payment.
_RESEND_DELIVERY = {
    "email.sent": "sent", "email.delivered": "delivered",
    "email.delivery_delayed": "delayed", "email.bounced": "bounced",
    "email.complained": "complained", "email.failed": "failed",
}


@router.post("/webhooks/resend")
async def resend_delivery_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Inbound Resend webhook for email DELIVERY TRACKING (public, signature-verified).

    Updates a payment's receipt delivery state (delivered / bounced / complained / ...) by matching
    the Resend email id we stored when the receipt was sent. Idempotent, best-effort, never leaks
    secrets. No-op (still 200) when RESEND_WEBHOOK_SECRET is not configured.
    """
    payload = await request.body()
    secret = settings.resend_webhook_secret
    if not secret:
        return {"received": True, "disabled": True}

    import resend
    try:
        event = resend.Webhooks.verify({
            "payload": payload.decode(),
            "headers": {
                "id": request.headers.get("svix-id"),
                "timestamp": request.headers.get("svix-timestamp"),
                "signature": request.headers.get("svix-signature"),
            },
            "webhook_secret": secret,
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event.get("type", "")
    data = event.get("data") or {}
    email_id = data.get("email_id") or data.get("id")
    delivery = _RESEND_DELIVERY.get(etype)
    if not email_id or not delivery:
        return {"received": True, "ignored": True}

    res = await db.execute(select(Payment).where(
        Payment.metadata_json["receipt_email_id"].astext == email_id))
    payment = res.scalar_one_or_none()
    if not payment:
        return {"received": True, "unmatched": True}
    md = dict(payment.metadata_json or {})
    md["receipt_delivery"] = delivery
    md["receipt_delivery_at"] = datetime.now(timezone.utc).isoformat()
    payment.metadata_json = md
    await db.commit()
    return {"received": True, "delivery": delivery}
