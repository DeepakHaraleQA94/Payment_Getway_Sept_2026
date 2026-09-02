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


async def import_settlements(db: AsyncSession, *, tenant_id, actor, rows: list[dict]) -> dict:
    """Import provider settlement rows from an uploaded file (batch-only, NO ledger credit).

    Idempotent: a row whose (tenant_id, provider_settlement_ref) already exists is SKIPPED as a
    duplicate — re-uploading the same file never creates a second settlement (no double record).
    Uses per-row SAVEPOINTs so a duplicate/error never discards successfully-created rows.
    Amounts are taken verbatim from the file (the provider is the source of truth).
    """
    created: list[dict] = []
    duplicates: list[str] = []
    errors: list[dict] = []
    seen: set[str] = set()

    for i, row in enumerate(rows, start=1):
        ref = str(row.get("provider_settlement_ref") or "").strip()
        if not ref:
            errors.append({"row": i, "error": "provider_settlement_ref is required"})
            continue
        if ref in seen:
            duplicates.append(ref)
            continue
        try:
            currency = (str(row.get("currency") or "USD").strip().upper())[:3]
            gross = int(row.get("gross_minor") or 0)
            fees = int(row.get("fees_minor") or 0)
            net_raw = row.get("net_minor")
            net = int(net_raw) if net_raw not in (None, "") else gross - fees
            count = int(row.get("txn_count") or 0)
        except (ValueError, TypeError):
            errors.append({"row": i, "error": "invalid numeric value"})
            continue
        if gross < 0 or fees < 0 or count < 0:
            errors.append({"row": i, "error": "amounts and count must be non-negative"})
            continue

        existing = await db.execute(select(Settlement).where(
            Settlement.tenant_id == tenant_id, Settlement.provider_settlement_ref == ref))
        if existing.scalar_one_or_none() is not None:
            duplicates.append(ref)
            seen.add(ref)
            continue

        settlement = Settlement(
            tenant_id=tenant_id, reference=ref[:64], currency=currency, gross_minor=gross,
            fees_minor=fees, net_minor=net, txn_count=count, status="settled",
            settled_at=datetime.now(timezone.utc), provider_settlement_ref=ref,
            created_by=str(getattr(actor, "id", "")) or None)
        try:
            async with db.begin_nested():
                db.add(settlement)
                await db.flush()
        except IntegrityError:
            # Concurrent import inserted the same reference first — idempotent skip.
            duplicates.append(ref)
            seen.add(ref)
            continue
        seen.add(ref)
        created.append({"id": str(settlement.id), "provider_settlement_ref": ref,
                        "currency": currency, "net_minor": net})

    return {"created": created, "duplicates": duplicates, "errors": errors,
            "created_count": len(created), "duplicate_count": len(duplicates),
            "error_count": len(errors)}
