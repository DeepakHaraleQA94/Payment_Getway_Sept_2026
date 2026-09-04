"""CSV exports for payments, settlements and ledger entries (finance reconciliation)."""
import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, resolve_tenant_id
from app.models.finance import LedgerEntry, Settlement
from app.models.payment import Payment

router = APIRouter(prefix="/api/reports/export", tags=["reports"])


def _payment_method(p) -> str:
    m = str((p.metadata_json or {}).get("method") or ("upi" if p.provider_key == "demo_upi" else "card")).lower()
    return "upi" if "upi" in m else "card"


def _csv_response(headers: list[str], rows: list[list], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/payments.csv")
async def export_payments(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(Payment).where(Payment.tenant_id == tid).order_by(Payment.created_at.desc()))
    rows = [[
        str(p.id), p.reference, p.provider_key, _payment_method(p), p.provider_txn_id or "", p.status,
        f"{p.amount_minor / 100:.2f}", f"{p.fee_minor / 100:.2f}", f"{p.net_minor / 100:.2f}",
        p.currency, p.customer_email or "", p.risk_score, p.created_at.isoformat(),
    ] for p in res.scalars().all()]
    return _csv_response(
        ["id", "reference", "provider", "method", "provider_txn_id", "status", "amount", "fee", "net",
         "currency", "customer_email", "risk_score", "created_at"],
        rows, "payments.csv")


@router.get("/settlements.csv")
async def export_settlements(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                             user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(Settlement).where(Settlement.tenant_id == tid).order_by(Settlement.created_at.desc()))
    rows = [[
        str(s.id), s.reference, s.status, f"{s.gross_minor / 100:.2f}", f"{s.fees_minor / 100:.2f}",
        f"{s.net_minor / 100:.2f}", s.currency, s.txn_count, s.created_at.isoformat(),
    ] for s in res.scalars().all()]
    return _csv_response(
        ["id", "reference", "status", "gross", "fees", "net", "currency", "txn_count", "created_at"],
        rows, "settlements.csv")


@router.get("/ledger.csv")
async def export_ledger(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(LedgerEntry).where(LedgerEntry.tenant_id == tid).order_by(LedgerEntry.created_at.desc()))
    rows = [[
        str(e.id), e.direction, f"{e.amount_minor / 100:.2f}", f"{e.balance_after_minor / 100:.2f}",
        e.currency, e.ref_type or "", e.description or "", e.created_at.isoformat(),
    ] for e in res.scalars().all()]
    return _csv_response(
        ["id", "direction", "amount", "balance_after", "currency", "ref_type", "description", "created_at"],
        rows, "ledger.csv")
