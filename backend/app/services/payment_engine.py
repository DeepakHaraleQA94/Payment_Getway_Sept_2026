"""Payment engine: orchestrates provider charge, fee engine, ledger and audit.

All financial mutations are server-side validated, tenant-isolated, idempotent
(via idempotency_key) and audit-logged.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.payment import Payment, Refund
from app.providers.base import ChargeRequest
from app.providers.registry import get_provider
from app.services import fee_engine, ledger_service, payment_state, risk_service, webhook_service


async def _existing_payment(db, tenant_id, idempotency_key):
    res = await db.execute(select(Payment).where(
        Payment.tenant_id == tenant_id, Payment.idempotency_key == idempotency_key))
    return res.scalar_one_or_none()


async def _existing_refund(db, tenant_id, idempotency_key):
    res = await db.execute(select(Refund).where(
        Refund.tenant_id == tenant_id, Refund.idempotency_key == idempotency_key))
    return res.scalar_one_or_none()


async def create_payment(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    reference: str,
    amount_minor: int,
    currency: str,
    provider_key: str = "mock",
    description: str | None = None,
    customer_email: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
) -> Payment:
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")

    # Idempotency: return existing payment if key already used for this tenant.
    if idempotency_key:
        existing = await _existing_payment(db, tenant_id, idempotency_key)
        if existing:
            return existing

    risk = risk_service.score_payment(amount_minor=amount_minor, customer_email=customer_email)

    payment = Payment(
        tenant_id=tenant_id,
        reference=reference,
        idempotency_key=idempotency_key,
        provider_key=provider_key,
        amount_minor=amount_minor,
        currency=currency,
        status="pending",
        description=description,
        customer_email=customer_email,
        risk_score=risk,
        metadata_json=metadata or {},
        created_by=str(getattr(actor, "id", "")) or None,
    )
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent request with the same idempotency key won the race; return that one.
        await db.rollback()
        existing = await _existing_payment(db, tenant_id, idempotency_key)
        if existing:
            return existing
        raise

    # Idempotency is claimed above by inserting the payment row (unique constraint on
    # tenant_id + idempotency_key) BEFORE dispatching the external charge. The same
    # idempotency_key is forwarded so the provider (e.g. Stripe) de-dupes on its side too.
    provider = get_provider(provider_key)
    result = provider.charge(
        ChargeRequest(
            amount_minor=amount_minor,
            currency=currency,
            reference=reference,
            description=description,
            customer_email=customer_email,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
        )
    )

    payment.provider_txn_id = result.provider_txn_id
    prev_status = payment.status
    payment_state.validate_transition(prev_status, result.status)
    payment.status = result.status

    if result.success:
        fee = await fee_engine.compute_fee(
            db, tenant_id=tenant_id, amount_minor=amount_minor, currency=currency, provider_key=provider_key
        )
        payment.fee_minor = fee
        payment.net_minor = amount_minor - fee
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=currency, direction="credit",
            amount_minor=payment.net_minor, ref_type="payment", ref_id=payment.id,
            description=f"Payment {reference}",
        )

    await record_audit(
        db, action="payment.create", resource_type="payment", resource_id=payment.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"previous_state": prev_status, "new_state": payment.status,
                 "amount_minor": amount_minor, "currency": currency,
                 "correlation_id": str(payment.id)},
    )
    await webhook_service.dispatch(
        db, tenant_id=tenant_id,
        event="payment.succeeded" if result.success else "payment.failed",
        data={"payment_id": str(payment.id), "reference": reference, "amount_minor": amount_minor,
              "currency": currency, "status": payment.status, "provider_txn_id": payment.provider_txn_id},
    )
    await db.commit()
    await db.refresh(payment)
    return payment


async def create_refund(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    payment: Payment,
    amount_minor: int,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> Refund:
    if not payment_state.is_refundable(payment.status):
        raise ValueError("Payment is not in a refundable state")
    already = sum(r.amount_minor for r in payment.refunds if r.status == "succeeded")
    if amount_minor <= 0 or already + amount_minor > payment.amount_minor:
        raise ValueError("Refund exceeds refundable amount")

    if idempotency_key:
        existing = await _existing_refund(db, tenant_id, idempotency_key)
        if existing:
            return existing

    refund = Refund(
        tenant_id=tenant_id, payment_id=payment.id, amount_minor=amount_minor,
        currency=payment.currency, reason=reason, status="pending",
        idempotency_key=idempotency_key, created_by=str(getattr(actor, "id", "")) or None,
    )
    db.add(refund)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _existing_refund(db, tenant_id, idempotency_key)
        if existing:
            return existing
        raise

    provider = get_provider(payment.provider_key)
    result = provider.refund(payment.provider_txn_id or "", amount_minor, payment.currency)
    refund.status = result.status if result.success else "failed"
    refund.provider_refund_id = result.provider_txn_id

    prev_status = payment.status
    if result.success:
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=payment.currency, direction="debit",
            amount_minor=amount_minor, ref_type="refund", ref_id=refund.id,
            description=f"Refund for {payment.reference}",
        )
        new_total = already + amount_minor
        target = "refunded" if new_total >= payment.amount_minor else "partially_refunded"
        payment_state.validate_transition(prev_status, target)
        payment.status = target

    await record_audit(
        db, action="refund.create", resource_type="refund", resource_id=refund.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"refund_status": refund.status, "previous_state": prev_status,
                 "new_state": payment.status, "amount_minor": amount_minor,
                 "correlation_id": str(refund.id)},
    )
    await webhook_service.dispatch(
        db, tenant_id=tenant_id,
        event="refund.succeeded" if result.success else "refund.failed",
        data={"refund_id": str(refund.id), "payment_id": str(payment.id),
              "amount_minor": amount_minor, "currency": payment.currency, "status": refund.status},
    )
    await db.commit()
    await db.refresh(refund)
    return refund
