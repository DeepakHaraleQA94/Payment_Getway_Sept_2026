"""Payment engine endpoints: payments and refunds."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_feature, require_permission, resolve_tenant_id
from app.models.payment import Payment, Refund
from app.schemas import PaymentCreate, PaymentOut, RefundCreate, RefundOut
from app.services import payment_engine

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("", response_model=list[PaymentOut])
async def list_payments(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(Payment).where(Payment.tenant_id == tid)
                           .order_by(Payment.created_at.desc()).limit(200))
    return res.scalars().all()


@router.post("", response_model=PaymentOut)
async def create_payment(body: PaymentCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("payment.create"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    try:
        payment = await payment_engine.create_payment(
            db, tenant_id=tid, actor=user, reference=body.reference, amount_minor=body.amount_minor,
            currency=body.currency, provider_key=body.provider_key, description=body.description,
            customer_email=body.customer_email, idempotency_key=body.idempotency_key, metadata=body.metadata,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return payment


@router.get("/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                      user=Depends(get_current_user)):
    payment = await db.get(Payment, payment_id)
    if not payment or (not user.is_superadmin and payment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.post("/{payment_id}/refunds", response_model=RefundOut)
async def refund_payment(payment_id: uuid.UUID, body: RefundCreate, db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("refund.create"))):
    res = await db.execute(select(Payment).options(selectinload(Payment.refunds))
                           .where(Payment.id == payment_id))
    payment = res.scalar_one_or_none()
    if not payment or (not user.is_superadmin and payment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    # Feature entitlement: refunds can be disabled per tenant (enforced server-side).
    await require_feature(db, payment.tenant_id, "refunds")
    try:
        refund = await payment_engine.create_refund(
            db, tenant_id=payment.tenant_id, actor=user, payment=payment,
            amount_minor=body.amount_minor, reason=body.reason, idempotency_key=body.idempotency_key,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return refund


@router.get("/refunds/all", response_model=list[RefundOut])
async def list_refunds(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                       user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(Refund).where(Refund.tenant_id == tid)
                           .order_by(Refund.created_at.desc()).limit(200))
    return res.scalars().all()
