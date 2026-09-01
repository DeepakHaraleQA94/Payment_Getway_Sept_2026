"""Turnover engine: aggregates payment volume into daily snapshots."""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment


async def summarize(db: AsyncSession, *, tenant_id) -> dict:
    """Live turnover summary across all payments for a tenant."""
    res = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount_minor), 0),
            func.coalesce(func.sum(Payment.fee_minor), 0),
            func.coalesce(func.sum(Payment.net_minor), 0),
            func.count(Payment.id),
        ).where(
            Payment.tenant_id == tenant_id,
            Payment.status.in_(["succeeded", "captured", "partially_refunded", "refunded"]),
        )
    )
    gross, fees, net, count = res.one()
    return {
        "gross_minor": int(gross),
        "fees_minor": int(fees),
        "net_minor": int(net),
        "txn_count": int(count),
    }
