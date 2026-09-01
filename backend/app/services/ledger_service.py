"""Balance & ledger service: append-only entries + balance accounts."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import LedgerAccount, LedgerEntry


async def get_or_create_account(
    db: AsyncSession, *, tenant_id, currency: str, account_type: str = "available"
) -> LedgerAccount:
    res = await db.execute(
        select(LedgerAccount).where(
            LedgerAccount.tenant_id == tenant_id,
            LedgerAccount.currency == currency,
            LedgerAccount.account_type == account_type,
        )
    )
    acc = res.scalar_one_or_none()
    if acc is None:
        acc = LedgerAccount(
            tenant_id=tenant_id, currency=currency, account_type=account_type, balance_minor=0
        )
        db.add(acc)
        await db.flush()
    return acc


async def post_entry(
    db: AsyncSession,
    *,
    tenant_id,
    currency: str,
    direction: str,
    amount_minor: int,
    ref_type: str | None = None,
    ref_id=None,
    description: str | None = None,
    account_type: str = "available",
) -> LedgerEntry:
    acc = await get_or_create_account(
        db, tenant_id=tenant_id, currency=currency, account_type=account_type
    )
    delta = amount_minor if direction == "credit" else -amount_minor
    acc.balance_minor += delta
    entry = LedgerEntry(
        tenant_id=tenant_id,
        account_id=acc.id,
        direction=direction,
        amount_minor=amount_minor,
        currency=currency,
        balance_after_minor=acc.balance_minor,
        ref_type=ref_type,
        ref_id=ref_id,
        description=description,
    )
    db.add(entry)
    await db.flush()
    return entry
