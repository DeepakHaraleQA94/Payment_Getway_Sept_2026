"""Reversal engine: fully unwinds an eligible original transaction.

Safety invariants:
- References the original payment; one reversal per payment (DB unique + row lock).
- Only eligible lifecycle states may be reversed (authorized/captured/succeeded).
- Never reverses a payment that already has succeeded refunds (no double money-out).
- Never creates money: any compensating ledger DEBIT only unwinds a credit that already
  existed for the original payment (looked up from the ledger).
- Idempotent (idempotency_key + unique payment_id); duplicate reversal rejected.
- Tenant-isolated; audited; participates in reconciliation via the reversal record + ledger ref.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.finance import LedgerEntry
from app.models.payment import Payment, Refund, Reversal
from app.providers.registry import get_provider
from app.services import ledger_service, payment_receipt_service, payment_state, webhook_service


async def _existing_by_idem(db, tenant_id, idempotency_key):
    if not idempotency_key:
        return None
    res = await db.execute(select(Reversal).where(
        Reversal.tenant_id == tenant_id, Reversal.idempotency_key == idempotency_key))
    return res.scalar_one_or_none()


async def _existing_for_payment(db, payment_id):
    res = await db.execute(select(Reversal).where(Reversal.payment_id == payment_id))
    return res.scalar_one_or_none()


async def create_reversal(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    payment: Payment,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> Reversal:
    # Lock the original payment row to serialize concurrent reversal/refund attempts.
    locked = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update())
    payment = locked.scalar_one()

    # Idempotency: same key returns the prior reversal (no second effect).
    prior = await _existing_by_idem(db, tenant_id, idempotency_key)
    if prior is not None:
        return prior
    # Duplicate reversal guard (even without an idempotency key): one per payment.
    if await _existing_for_payment(db, payment.id) is not None:
        raise ValueError("Payment has already been reversed")

    if not payment_state.is_reversible(payment.status):
        raise ValueError("Payment is not in a reversible state")

    # Refund + reversal must not both remove funds for the same payment.
    refunded = (await db.execute(
        select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
            Refund.payment_id == payment.id, Refund.status == "succeeded"))).scalar_one()
    if refunded > 0:
        raise ValueError("Cannot reverse a payment that has refunds")

    # The only funds a reversal may unwind are those the original payment actually credited.
    credited = (await db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.tenant_id == tenant_id, LedgerEntry.ref_type == "payment",
            LedgerEntry.ref_id == payment.id, LedgerEntry.direction == "credit"))).scalar_one()

    reversal = Reversal(
        tenant_id=tenant_id, payment_id=payment.id, amount_minor=int(credited),
        currency=payment.currency, reason=reason, status="pending",
        idempotency_key=idempotency_key, created_by=str(getattr(actor, "id", "")) or None,
    )
    payment_id_val = payment.id
    db.add(reversal)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent reversal won the race; return that one (idempotent).
        await db.rollback()
        existing = await _existing_by_idem(db, tenant_id, idempotency_key)
        if existing is None:
            existing = await _existing_for_payment(db, payment_id_val)
        if existing is not None:
            return existing
        raise

    # For captured/settled funds, reflect the reversal at the PSP via the generic provider
    # contract; if the PSP declines, abort without any internal financial mutation.
    if credited > 0:
        result = get_provider(payment.provider_key).refund(
            payment.provider_txn_id or "", int(credited), payment.currency)
        if not result.success:
            reversal.status = "failed"
            await record_audit(
                db, action="payment.reverse_failed", resource_type="reversal",
                resource_id=reversal.id, tenant_id=tenant_id,
                actor_id=str(getattr(actor, "id", "")) or None,
                actor_email=getattr(actor, "email", None),
                changes={"payment_id": str(payment.id), "reason": "provider_reversal_failed"})
            await db.commit()
            await db.refresh(reversal)
            raise ValueError("Provider reversal failed")
        reversal.provider_ref = result.provider_txn_id
        # Compensating DEBIT: unwinds exactly the credit that existed. Never creates money.
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=payment.currency, direction="debit",
            amount_minor=int(credited), ref_type="reversal", ref_id=reversal.id,
            description=f"Reversal of {payment.reference}")

    prev_status = payment.status
    payment_state.validate_transition(prev_status, "reversed")
    payment.status = "reversed"
    reversal.status = "succeeded"

    await record_audit(
        db, action="payment.reverse", resource_type="reversal", resource_id=reversal.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"payment_id": str(payment.id), "previous_state": prev_status,
                 "new_state": "reversed", "amount_minor": int(credited),
                 "currency": payment.currency, "correlation_id": str(reversal.id)})
    await webhook_service.dispatch(
        db, tenant_id=tenant_id, event="payment.reversed",
        data={"reversal_id": str(reversal.id), "payment_id": str(payment.id),
              "amount_minor": int(credited), "currency": payment.currency, "status": "reversed"})
    # Customer reversal notice (idempotent per reversal, best-effort — never aborts the reversal).
    await payment_receipt_service.send_transaction_notice(
        db, payment=payment, kind="reversal", amount_minor=int(credited),
        ref_id=reversal.id, currency=payment.currency)
    await db.commit()
    await db.refresh(reversal)
    return reversal
