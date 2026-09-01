"""Reports service (foundation): summary report over payments."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment


async def payments_by_status(db: AsyncSession, *, tenant_id) -> list[dict]:
    res = await db.execute(
        select(Payment.status, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_minor), 0))
        .where(Payment.tenant_id == tenant_id)
        .group_by(Payment.status)
    )
    return [
        {"status": row[0], "count": int(row[1]), "amount_minor": int(row[2])}
        for row in res.all()
    ]
