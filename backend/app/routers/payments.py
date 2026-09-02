"""Payment engine endpoints: payments and refunds."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user, require_feature, require_permission, resolve_tenant_id
from app.models.payment import Payment, Refund, UtrSubmission
from app.schemas import (
    CaptureCreate,
    PaymentCreate,
    PaymentOut,
    RefundCreate,
    RefundOut,
    ReversalOut,
    ReverseCreate,
    UtrOut,
    UtrReview,
    UtrSubmitCreate,
    VoidCreate,
)
from app.services import payment_engine, reversal_service, utr_service

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
            currency=body.currency, provider_key=body.provider_key, environment=body.environment,
            description=body.description, customer_email=body.customer_email,
            idempotency_key=body.idempotency_key, metadata=body.metadata,
            country=body.country, payment_method=body.payment_method, flow=body.flow,
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
    await require_feature(db, payment.tenant_id, "refunds", bypass=user.is_superadmin)
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


@router.post("/{payment_id}/reverse", response_model=ReversalOut)
async def reverse_payment(payment_id: uuid.UUID, body: ReverseCreate, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("payment.reverse"))):
    """Fully reverse an eligible original transaction. Authorized (payment.reverse), tenant-isolated,
    idempotent (one reversal per payment), and never creates money."""
    payment = await db.get(Payment, payment_id)
    if not payment or (not user.is_superadmin and payment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        reversal = await reversal_service.create_reversal(
            db, tenant_id=payment.tenant_id, actor=user, payment=payment,
            reason=body.reason, idempotency_key=body.idempotency_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return reversal


@router.post("/{payment_id}/capture", response_model=PaymentOut)
async def capture_payment(payment_id: uuid.UUID, body: CaptureCreate, db: AsyncSession = Depends(get_db),
                          user=Depends(require_permission("payment.capture"))):
    """Capture an eligible AUTHORIZED payment. Authorized (payment.capture), tenant-isolated,
    idempotent, provider-agnostic, and never duplicates a ledger credit."""
    payment = await db.get(Payment, payment_id)
    if not payment or (not user.is_superadmin and payment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        result = await payment_engine.capture_payment(
            db, tenant_id=payment.tenant_id, actor=user, payment=payment,
            amount_minor=body.amount_minor, reason=body.reason, idempotency_key=body.idempotency_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/{payment_id}/void", response_model=PaymentOut)
async def void_payment(payment_id: uuid.UUID, body: VoidCreate, db: AsyncSession = Depends(get_db),
                       user=Depends(require_permission("payment.void"))):
    """Void/cancel an eligible AUTHORIZED payment before capture. Authorized (payment.void),
    tenant-isolated, idempotent, provider-agnostic, and creates no money."""
    payment = await db.get(Payment, payment_id)
    if not payment or (not user.is_superadmin and payment.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        result = await payment_engine.void_payment(
            db, tenant_id=payment.tenant_id, actor=user, payment=payment,
            reason=body.reason, idempotency_key=body.idempotency_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/utr", response_model=UtrOut)
async def submit_utr(body: UtrSubmitCreate, tenant_id: str | None = None,
                     db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("utr.submit"))):
    """Submit a bank UTR to claim credit. Never credits on submission — starts in 'under_review'."""
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    try:
        submission = await utr_service.submit_utr(
            db, tenant_id=tid, actor=user, utr=body.utr, amount_minor=body.amount_minor,
            currency=body.currency, payment_id=body.payment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return submission


@router.post("/utr/{submission_id}/review", response_model=UtrOut)
async def review_utr(submission_id: uuid.UUID, body: UtrReview,
                     db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("utr.verify"))):
    """Manually confirm or reject a UTR. Confirmation is the ONLY path that credits the ledger."""
    submission = await db.get(UtrSubmission, submission_id)
    if not submission or (not user.is_superadmin and submission.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="UTR submission not found")
    try:
        result = await utr_service.review_utr(
            db, tenant_id=submission.tenant_id, actor=user, submission_id=submission_id,
            decision=body.decision, expected_amount_minor=body.expected_amount_minor,
            expected_currency=body.expected_currency, reason=body.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/utr/list", response_model=list[UtrOut])
async def list_utr(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                   user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(UtrSubmission).where(UtrSubmission.tenant_id == tid)
                           .order_by(UtrSubmission.created_at.desc()).limit(200))
    return res.scalars().all()
