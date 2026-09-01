"""Balance/ledger, turnover, settlements, reports, FX endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.models.finance import LedgerAccount, LedgerEntry, Settlement
from app.services import fx_service, report_service, settlement_service, turnover_engine

router = APIRouter(prefix="/api", tags=["finance"])


@router.get("/ledger/accounts")
async def ledger_accounts(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(LedgerAccount).where(LedgerAccount.tenant_id == tid))
    accounts = res.scalars().all()
    return [{"id": str(a.id), "account_type": a.account_type, "currency": a.currency,
             "balance_minor": a.balance_minor} for a in accounts]


@router.get("/ledger/entries")
async def ledger_entries(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(LedgerEntry).where(LedgerEntry.tenant_id == tid)
                           .order_by(LedgerEntry.created_at.desc()).limit(200))
    entries = res.scalars().all()
    return [{"id": str(e.id), "direction": e.direction, "amount_minor": e.amount_minor,
             "currency": e.currency, "balance_after_minor": e.balance_after_minor,
             "ref_type": e.ref_type, "description": e.description,
             "created_at": e.created_at.isoformat()} for e in entries]


@router.get("/turnover")
async def turnover(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                   user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    return await turnover_engine.summarize(db, tenant_id=tid)


@router.get("/settlements")
async def list_settlements(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                           user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(Settlement).where(Settlement.tenant_id == tid)
                           .order_by(Settlement.created_at.desc()))
    s = res.scalars().all()
    return [{"id": str(x.id), "reference": x.reference, "currency": x.currency,
             "gross_minor": x.gross_minor, "fees_minor": x.fees_minor, "net_minor": x.net_minor,
             "txn_count": x.txn_count, "status": x.status,
             "created_at": x.created_at.isoformat()} for x in s]


@router.post("/settlements/generate")
async def generate_settlement(currency: str = "USD", tenant_id: str | None = None,
                              db: AsyncSession = Depends(get_db),
                              user=Depends(require_permission("settlement.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    s = await settlement_service.generate_settlement(db, tenant_id=tid, currency=currency)
    await db.commit()
    await db.refresh(s)
    return {"id": str(s.id), "reference": s.reference, "net_minor": s.net_minor, "status": s.status}


@router.get("/reports/payments-by-status")
async def report_payments(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    return await report_service.payments_by_status(db, tenant_id=tid)


@router.get("/fx/convert")
async def fx_convert(amount_minor: int, base: str, quote: str, db: AsyncSession = Depends(get_db),
                     user=Depends(get_current_user)):
    return await fx_service.convert(db, amount_minor=amount_minor, base=base.upper(), quote=quote.upper())
