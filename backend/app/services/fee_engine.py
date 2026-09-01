"""Fee engine: computes fees for a payment based on active fee rules."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import FeeRule


async def compute_fee(
    db: AsyncSession,
    *,
    tenant_id,
    amount_minor: int,
    currency: str,
    provider_key: str,
) -> int:
    """Return fee in minor units. Picks the highest-priority matching active rule."""
    res = await db.execute(
        select(FeeRule)
        .where(FeeRule.tenant_id == tenant_id, FeeRule.active.is_(True))
        .order_by(FeeRule.priority.asc())
    )
    rules = res.scalars().all()

    def matches(rule: FeeRule) -> bool:
        if rule.provider_key and rule.provider_key != provider_key:
            return False
        if rule.currency and rule.currency != currency:
            return False
        return True

    rule = next((r for r in rules if matches(r)), None)
    if rule is None:
        return 0
    fee = (amount_minor * rule.percent_bps) // 10000 + rule.fixed_minor
    return max(fee, rule.min_fee_minor)
