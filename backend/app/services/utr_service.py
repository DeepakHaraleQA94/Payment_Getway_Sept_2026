"""UTR verification engine.

A UTR (bank Unique Transaction Reference) is submitted to claim credit for an out-of-band bank
transfer. The platform NEVER credits a balance solely because a UTR was entered:

    submit  -> under_review           (no financial impact)
    confirm -> match amount/currency  -> ledger credit (once)      -> confirmed
    reject  -> no financial impact    -> rejected

Safety invariants:
- Server-side validation + authorization (submit vs verify permissions).
- Duplicate UTR use blocked (unique per tenant); a UTR can credit at most one submission.
- Manual confirmation only — no fabricated/automatic bank verification.
- Amount / currency / (linked) payment-status mismatches are rejected.
- Ledger impact only after a valid confirmation; idempotent (row lock + status guard).
- Strict tenant isolation; every transition audited.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.payment import Payment, UtrSubmission
from app.services import fee_engine, ledger_service, payment_state


async def submit_utr(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    utr: str,
    amount_minor: int,
    currency: str,
    payment_id=None,
) -> UtrSubmission:
    utr = (utr or "").strip()
    if not utr:
        raise ValueError("UTR is required")
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")

    # When linked, the payment must belong to the same tenant (no cross-tenant matching).
    if payment_id is not None:
        payment = await db.get(Payment, payment_id)
        if payment is None or payment.tenant_id != tenant_id:
            raise ValueError("Linked payment not found for this tenant")

    submission = UtrSubmission(
        tenant_id=tenant_id, payment_id=payment_id, utr=utr, amount_minor=amount_minor,
        currency=currency.upper(), status="under_review",
        created_by=str(getattr(actor, "id", "")) or None,
    )
    db.add(submission)
    try:
        await db.flush()
    except IntegrityError:
        # A UTR is unique per tenant: it may not be reused for another (or the same) transaction.
        await db.rollback()
        raise ValueError("This UTR has already been submitted")

    await record_audit(
        db, action="utr.submit", resource_type="utr_submission", resource_id=submission.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"utr": utr, "amount_minor": amount_minor, "currency": submission.currency,
                 "payment_id": str(payment_id) if payment_id else None, "status": "under_review"})
    await db.commit()
    await db.refresh(submission)
    return submission


async def review_utr(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    submission_id,
    decision: str,
    expected_amount_minor: int | None = None,
    expected_currency: str | None = None,
    reason: str | None = None,
) -> UtrSubmission:
    if decision not in ("confirm", "reject"):
        raise ValueError("decision must be 'confirm' or 'reject'")

    # Row lock serializes concurrent confirmations of the same UTR.
    from sqlalchemy import select
    locked = await db.execute(
        select(UtrSubmission).where(UtrSubmission.id == submission_id).with_for_update())
    submission = locked.scalar_one_or_none()
    if submission is None or submission.tenant_id != tenant_id:
        raise ValueError("UTR submission not found for this tenant")

    # Idempotency: a resolved submission is returned as-is (no second credit).
    if submission.status == "confirmed":
        return submission
    if submission.status == "rejected":
        if decision == "reject":
            return submission
        raise ValueError("UTR submission was already rejected")

    if decision == "reject":
        submission.status = "rejected"
        submission.reason = reason or "manual_reject"
        submission.reviewed_by = str(getattr(actor, "id", "")) or None
        await record_audit(
            db, action="utr.reject", resource_type="utr_submission", resource_id=submission.id,
            tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
            actor_email=getattr(actor, "email", None),
            changes={"status": "rejected", "reason": submission.reason})
        await db.commit()
        await db.refresh(submission)
        return submission

    # ---- confirm path: strict matching before any credit ----
    if expected_amount_minor is not None and expected_amount_minor != submission.amount_minor:
        raise ValueError("Amount mismatch: submitted UTR amount does not match verified amount")
    if expected_currency is not None and expected_currency.upper() != submission.currency:
        raise ValueError("Currency mismatch: submitted UTR currency does not match verified currency")

    payment = None
    if submission.payment_id is not None:
        payment = await db.get(Payment, submission.payment_id)
        if payment is None or payment.tenant_id != tenant_id:
            raise ValueError("Linked payment not found for this tenant")
        if payment.amount_minor != submission.amount_minor:
            raise ValueError("Amount mismatch: UTR amount does not match the payment amount")
        if payment.currency != submission.currency:
            raise ValueError("Currency mismatch: UTR currency does not match the payment currency")
        if payment.status not in ("pending", "authorized"):
            raise ValueError(
                f"Status mismatch: payment is '{payment.status}', not awaiting a bank transfer")

    # Confirmed: post the single ledger credit for this UTR.
    if payment is not None:
        fee = await fee_engine.compute_fee(
            db, tenant_id=tenant_id, amount_minor=payment.amount_minor,
            currency=payment.currency, provider_key=payment.provider_key)
        payment.fee_minor = fee
        payment.net_minor = payment.amount_minor - fee
        prev = payment.status
        payment_state.validate_transition(prev, "succeeded")
        payment.status = "succeeded"
        credit_amount = payment.net_minor
    else:
        credit_amount = submission.amount_minor

    await ledger_service.post_entry(
        db, tenant_id=tenant_id, currency=submission.currency, direction="credit",
        amount_minor=int(credit_amount), ref_type="utr", ref_id=submission.id,
        description=f"UTR confirmed {submission.utr}")

    submission.status = "confirmed"
    submission.reviewed_by = str(getattr(actor, "id", "")) or None
    await record_audit(
        db, action="utr.confirm", resource_type="utr_submission", resource_id=submission.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"status": "confirmed", "amount_minor": int(credit_amount),
                 "currency": submission.currency,
                 "payment_id": str(submission.payment_id) if submission.payment_id else None,
                 "correlation_id": str(submission.id)})
    await db.commit()
    await db.refresh(submission)
    return submission
