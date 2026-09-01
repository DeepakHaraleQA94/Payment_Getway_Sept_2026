"""Scheduled reports: list, download, and on-demand generation."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.core.storage import get_object
from app.models.commerce import ScheduledReport, StoredFile
from app.models.tenant import Tenant
from app.services import report_generation

router = APIRouter(prefix="/api/reports/scheduled", tags=["reports"])


@router.get("")
async def list_reports(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                       user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(ScheduledReport).where(ScheduledReport.tenant_id == tid)
                           .order_by(ScheduledReport.period_date.desc(), ScheduledReport.created_at.desc()).limit(200))
    reports = res.scalars().all()
    return [{"id": str(r.id), "period_date": r.period_date.date().isoformat(), "report_type": r.report_type,
             "payments_count": r.payments_count, "settlements_count": r.settlements_count,
             "recipient_email": r.recipient_email, "email_status": r.email_status, "status": r.status,
             "file_id": str(r.file_id) if r.file_id else None,
             "created_at": r.created_at.isoformat()} for r in reports]


@router.post("/run")
async def run_now(tenant_id: str | None = None, report_type: str = "daily",
                  db: AsyncSession = Depends(get_db),
                  user=Depends(require_permission("report.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    if report_type not in report_generation.VALID_TYPES:
        raise HTTPException(status_code=400, detail="report_type must be daily, weekly or monthly")
    tenant = await db.get(Tenant, tid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    report = await report_generation.generate_report(db, tenant=tenant, report_type=report_type)
    await db.commit()
    return {"id": str(report.id), "report_type": report.report_type,
            "payments_count": report.payments_count,
            "settlements_count": report.settlements_count, "email_status": report.email_status,
            "file_id": str(report.file_id) if report.file_id else None}


@router.get("/download/{file_id}")
async def download_report(file_id: uuid.UUID, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    sf = await db.get(StoredFile, file_id)
    if not sf or sf.is_deleted or sf.kind != "report":
        raise HTTPException(status_code=404, detail="Report file not found")
    if not user.is_superadmin and sf.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    data, ct = get_object(sf.storage_path)
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{sf.original_filename}"'})
