"""Balance/ledger, turnover, settlements, reports, FX endpoints."""
import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
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
             "ref_type": e.ref_type, "ref_id": str(e.ref_id) if e.ref_id else None,
             "description": e.description,
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
                              provider_settlement_ref: str | None = None,
                              db: AsyncSession = Depends(get_db),
                              user=Depends(require_permission("settlement.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    s = await settlement_service.generate_settlement(
        db, tenant_id=tid, currency=currency, provider_settlement_ref=provider_settlement_ref)
    await db.commit()
    await db.refresh(s)
    return {"id": str(s.id), "reference": s.reference, "net_minor": s.net_minor, "status": s.status,
            "provider_settlement_ref": s.provider_settlement_ref}


SETTLEMENT_IMPORT_COLUMNS = ["provider_settlement_ref", "currency", "gross_minor",
                             "fees_minor", "net_minor", "txn_count"]
MAX_IMPORT_ROWS = 5000


@router.get("/settlements/import-template")
async def settlement_import_template(user=Depends(require_permission("settlement.manage"))):
    """The expected CSV shape for provider settlement imports (one row = one settlement batch)."""
    return {"columns": SETTLEMENT_IMPORT_COLUMNS,
            "example": "provider_settlement_ref,currency,gross_minor,fees_minor,net_minor,txn_count\n"
                       "PSP-2026-06-01,USD,1000000,29000,971000,120\n"}


@router.post("/settlements/import")
async def import_settlements(file: UploadFile = File(...), tenant_id: str | None = None,
                             dry_run: bool = False, db: AsyncSession = Depends(get_db),
                             user=Depends(require_permission("settlement.manage"))):
    """Upload a provider settlement CSV and reconcile it idempotently (batch-only, no ledger credit).

    Re-uploading the same file is safe: rows whose provider_settlement_ref already exists are
    skipped as duplicates. With `dry_run=true`, nothing is written — the response classifies each
    row (new / duplicate / error) so operators can preview before committing. Tenant-isolated;
    requires settlement.manage.
    """
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "provider_settlement_ref" not in reader.fieldnames:
        raise HTTPException(status_code=400,
                            detail="CSV must include a 'provider_settlement_ref' column")
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV has no data rows")
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=400, detail=f"Too many rows (max {MAX_IMPORT_ROWS})")

    summary = await settlement_service.import_settlements(
        db, tenant_id=tid, actor=user, rows=rows, dry_run=dry_run)
    if dry_run:
        # Preview only: no persistence, no audit trail entry.
        await db.rollback()
        return summary
    await record_audit(db, action="settlement.import", resource_type="settlement", resource_id=None,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email,
                       changes={"filename": file.filename, "created": summary["created_count"],
                                "duplicates": summary["duplicate_count"],
                                "errors": summary["error_count"]})
    await db.commit()
    return summary


@router.get("/settlements/imports")
async def settlement_import_history(tenant_id: str | None = None, limit: int = 50,
                                    db: AsyncSession = Depends(get_db),
                                    user=Depends(require_permission("settlement.manage"))):
    """Log of past settlement-file imports for the tenant (who ran it + new/duplicate/error tallies).

    Sourced from the append-only audit trail (action='settlement.import'); no secrets exposed.
    """
    from app.models.platform import AuditLog
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    res = await db.execute(
        select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "settlement.import")
        .order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 200)))
    entries = res.scalars().all()
    return [{
        "id": str(e.id),
        "created_at": e.created_at.isoformat(),
        "actor_email": e.actor_email,
        "filename": (e.changes or {}).get("filename"),
        "created": (e.changes or {}).get("created", 0),
        "duplicates": (e.changes or {}).get("duplicates", 0),
        "errors": (e.changes or {}).get("errors", 0),
    } for e in entries]


@router.get("/reports/payments-by-status")
async def report_payments(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    return await report_service.payments_by_status(db, tenant_id=tid)


@router.get("/fx/convert")
async def fx_convert(amount_minor: int, base: str, quote: str, db: AsyncSession = Depends(get_db),
                     user=Depends(get_current_user)):
    return await fx_service.convert(db, amount_minor=amount_minor, base=base.upper(), quote=quote.upper())
