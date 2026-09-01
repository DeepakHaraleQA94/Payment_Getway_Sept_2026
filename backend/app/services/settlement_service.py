"""Settlement / reconciliation engine (foundation).

Groups settled/captured payments into a settlement batch and reconciles totals.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import Settlement
from app.models.payment import Payment


async def generate_settlement(db: AsyncSession, *, tenant_id, currency: str) -> Settlement:
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
    )
    db.add(settlement)
    await db.flush()
    return settlement
