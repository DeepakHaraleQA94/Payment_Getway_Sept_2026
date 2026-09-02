"""Settlement / reconciliation engine (foundation).

Groups settled/captured payments into a settlement batch and reconciles totals.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import Settlement
from app.models.payment import Payment


async def generate_settlement(db: AsyncSession, *, tenant_id, currency: str,
                              provider_settlement_ref: str | None = None) -> Settlement:
    # Idempotency: when a stable provider settlement reference is supplied, a repeated settlement
    # file/response/retry for the SAME reference must NOT create a second settlement (no double
    # credit). Return the previously-processed settlement instead.
    if provider_settlement_ref:
        existing = await db.execute(select(Settlement).where(
            Settlement.tenant_id == tenant_id,
            Settlement.provider_settlement_ref == provider_settlement_ref))
        prior = existing.scalar_one_or_none()
        if prior is not None:
            return prior

    res = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount_minor), 0),
            func.coalesce(func.sum(Payment.fee_minor), 0),
            func.coalesce(func.sum(Payment.net_minor), 0),
            func.count(Payment.id),
        ).where(
            Payment.tenant_id == tenant_id,
            Payment.currency == currency,
            Payment.status.in_(["succeeded", "captured"]),
        )
    )
    gross, fees, net, count = res.one()
    settlement = Settlement(
        tenant_id=tenant_id,
        reference=f"STL-{uuid.uuid4().hex[:10].upper()}",
        currency=currency,
        gross_minor=int(gross),
        fees_minor=int(fees),
        net_minor=int(net),
        txn_count=int(count),
        status="settled",
        settled_at=datetime.now(timezone.utc),
        provider_settlement_ref=provider_settlement_ref,
    )
    db.add(settlement)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent worker processing the same provider settlement reference won the race;
        # return that record (idempotent, no second credit).
        await db.rollback()
        if provider_settlement_ref:
            existing = await db.execute(select(Settlement).where(
                Settlement.tenant_id == tenant_id,
                Settlement.provider_settlement_ref == provider_settlement_ref))
            prior = existing.scalar_one_or_none()
            if prior is not None:
                return prior
        raise
    return settlement
