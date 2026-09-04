"""Line-level reconciliation & matching API (report-only). Additive; does not touch existing routers."""
import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import require_permission, resolve_tenant_id
from app.models.finance import ReconciliationItem, ReconciliationRun
from app.models.payment import Payment
from app.services import reconciliation_engine

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])

IMPORT_COLUMNS = ["provider_txn_id", "reference", "amount_minor", "currency", "status"]
MAX_ROWS = 10000


def _run_out(r: ReconciliationRun) -> dict:
    return {
        "id": str(r.id), "source": r.source, "filename": r.filename, "currency": r.currency,
        "total_lines": r.total_lines, "matched_count": r.matched_count,
        "discrepancy_count": r.discrepancy_count, "summary": r.summary, "run_ref": r.run_ref,
        "created_at": r.created_at.isoformat(),
    }


def _item_out(i: ReconciliationItem) -> dict:
    return {
        "id": str(i.id), "outcome": i.outcome, "provider_txn_id": i.provider_txn_id,
        "reference": i.reference, "payment_id": str(i.payment_id) if i.payment_id else None,
        "provider_amount_minor": i.provider_amount_minor,
        "internal_amount_minor": i.internal_amount_minor, "currency": i.currency,
        "provider_status": i.provider_status, "internal_status": i.internal_status,
        "detail": i.detail,
    }


@router.get("/template")
async def reconciliation_template(user=Depends(require_permission("reconciliation.run"))):
    return {"columns": IMPORT_COLUMNS,
            "example": "provider_txn_id,reference,amount_minor,currency,status\n"
                       "mock_abc123,ORD-1001,10000,USD,succeeded\n"}


@router.post("/run")
async def run_reconciliation(file: UploadFile | None = File(default=None), tenant_id: str | None = None,
                             source: str = "both", currency: str | None = None,
                             run_ref: str | None = None, db: AsyncSession = Depends(get_db),
                             user=Depends(require_permission("reconciliation.run"))):
    """Run a line-level reconciliation. Optional provider-lines CSV upload + provider-pull.

    Report-only: records matched/mismatch/missing/duplicate findings; never mutates financial state.
    Idempotent per (tenant, run_ref). Tenant-isolated; requires reconciliation.run.
    """
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")

    provider_lines = []
    filename = None
    if file is not None:
        filename = file.filename
        raw = await file.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "provider_txn_id" not in reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV must include a 'provider_txn_id' column")
        provider_lines = list(reader)
        if len(provider_lines) > MAX_ROWS:
            raise HTTPException(status_code=400, detail=f"Too many rows (max {MAX_ROWS})")

    if source in ("upload", "both") and file is None and source == "upload":
        raise HTTPException(status_code=400, detail="upload source requires a CSV file")

    try:
        run = await reconciliation_engine.run_reconciliation(
            db, tenant_id=tid, actor=user, source=source, provider_lines=provider_lines,
            currency=currency, filename=filename, run_ref=run_ref)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await record_audit(db, action="reconciliation.run", resource_type="reconciliation_run",
                       resource_id=run.id, tenant_id=tid, actor_id=str(user.id), actor_email=user.email,
                       changes={"source": run.source, "filename": run.filename,
                                "total_lines": run.total_lines, "matched": run.matched_count,
                                "discrepancies": run.discrepancy_count})
    await db.commit()
    await db.refresh(run)
    return _run_out(run)


@router.get("/runs")
async def list_runs(tenant_id: str | None = None, limit: int = 50, db: AsyncSession = Depends(get_db),
                    user=Depends(require_permission("reconciliation.view"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    res = await db.execute(select(ReconciliationRun).where(ReconciliationRun.tenant_id == tid)
                           .order_by(ReconciliationRun.created_at.desc()).limit(min(max(limit, 1), 200)))
    return [_run_out(r) for r in res.scalars().all()]


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, tenant_id: str | None = None, outcome: str | None = None,
                     db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("reconciliation.view"))):
    tid = resolve_tenant_id(user, tenant_id)
    run = await db.get(ReconciliationRun, run_id)
    if not run or (not user.is_superadmin and run.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    if not user.is_superadmin and tid is not None and run.tenant_id != tid:
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    q = select(ReconciliationItem).where(ReconciliationItem.run_id == run.id)
    if outcome:
        q = q.where(ReconciliationItem.outcome == outcome)
    items = (await db.execute(q.order_by(ReconciliationItem.outcome).limit(2000))).scalars().all()
    pids = [i.payment_id for i in items if i.payment_id]
    pmap = {}
    if pids:
        prows = (await db.execute(select(Payment).where(Payment.id.in_(pids)))).scalars().all()
        pmap = {p.id: p for p in prows}
    items_out = [{**_item_out(i), "method": _pm_from_payment(pmap.get(i.payment_id))} for i in items]
    method_summary = {}
    for it in items_out:
        method_summary[it["method"]] = method_summary.get(it["method"], 0) + 1
    return {"run": _run_out(run), "items": items_out, "method_summary": method_summary}


def _pm_from_payment(p) -> str:
    if p is None:
        return "unknown"
    m = str(p.payment_method or (p.metadata_json or {}).get("method") or "").lower()
    if not m:
        m = "upi" if p.provider_key == "demo_upi" else "card"
    return "upi" if "upi" in m else "card"


@router.get("/runs/{run_id}/export.csv")
async def export_run(run_id: str, tenant_id: str | None = None, outcome: str | None = None,
                     db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("reconciliation.view"))):
    """Per-line reconciliation export with a payment-method column (split payouts by rail)."""
    tid = resolve_tenant_id(user, tenant_id)
    run = await db.get(ReconciliationRun, run_id)
    if not run or (not user.is_superadmin and run.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    if not user.is_superadmin and tid is not None and run.tenant_id != tid:
        raise HTTPException(status_code=404, detail="Reconciliation run not found")
    q = select(ReconciliationItem).where(ReconciliationItem.run_id == run.id)
    if outcome:
        q = q.where(ReconciliationItem.outcome == outcome)
    items = (await db.execute(q.order_by(ReconciliationItem.outcome).limit(5000))).scalars().all()
    # Resolve method for each linked payment (one lookup batch).
    pids = [i.payment_id for i in items if i.payment_id]
    pmap = {}
    if pids:
        prows = (await db.execute(select(Payment).where(Payment.id.in_(pids)))).scalars().all()
        pmap = {p.id: p for p in prows}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["outcome", "method", "provider_txn_id", "reference", "provider_amount", "internal_amount",
                "currency", "provider_status", "internal_status", "detail"])
    breakdown = {}
    for i in items:
        method = _pm_from_payment(pmap.get(i.payment_id))
        breakdown[method] = breakdown.get(method, 0) + 1
        w.writerow([
            i.outcome, method, i.provider_txn_id or "", i.reference or "",
            f"{(i.provider_amount_minor or 0) / 100:.2f}", f"{(i.internal_amount_minor or 0) / 100:.2f}",
            i.currency or "", i.provider_status or "", i.internal_status or "", i.detail or "",
        ])
    w.writerow([])
    w.writerow(["METHOD BREAKDOWN"])
    w.writerow(["method", "line_count"])
    for method, count in sorted(breakdown.items()):
        w.writerow([method, count])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="reconciliation_{run.run_ref or run_id}.csv"'})
