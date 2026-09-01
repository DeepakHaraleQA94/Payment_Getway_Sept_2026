"""Daily report generation: build CSV (payments + settlements), store it, notify (adapter)."""
import csv
import io
import logging
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.storage import APP_NAME, put_object
from app.models.commerce import ScheduledReport, StoredFile
from app.models.finance import Settlement
from app.models.payment import Payment
from app.models.tenant import Tenant
from app.services import email_service

logger = logging.getLogger("cloudpay.reports")


def _fmt(minor: int) -> str:
    return f"{(minor or 0) / 100:.2f}"


async def generate_daily_report(db: AsyncSession, *, tenant: Tenant, period_date: datetime | None = None) -> ScheduledReport:
    day = (period_date or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = datetime.combine(day.date(), time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    pay_res = await db.execute(
        select(Payment).where(Payment.tenant_id == tenant.id,
                              Payment.created_at >= start, Payment.created_at < end)
        .order_by(Payment.created_at.asc())
    )
    payments = pay_res.scalars().all()
    stl_res = await db.execute(
        select(Settlement).where(Settlement.tenant_id == tenant.id,
                                 Settlement.created_at >= start, Settlement.created_at < end)
        .order_by(Settlement.created_at.asc())
    )
    settlements = stl_res.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"CloudPay Daily Report — {tenant.name}", day.date().isoformat()])
    w.writerow([])
    w.writerow(["PAYMENTS"])
    w.writerow(["reference", "status", "amount", "fee", "net", "currency", "customer_email", "created_at"])
    for p in payments:
        w.writerow([p.reference, p.status, _fmt(p.amount_minor), _fmt(p.fee_minor), _fmt(p.net_minor),
                    p.currency, p.customer_email or "", p.created_at.isoformat()])
    w.writerow([])
    w.writerow(["SETTLEMENTS"])
    w.writerow(["reference", "status", "gross", "fees", "net", "currency", "txn_count", "created_at"])
    for s in settlements:
        w.writerow([s.reference, s.status, _fmt(s.gross_minor), _fmt(s.fees_minor), _fmt(s.net_minor),
                    s.currency, s.txn_count, s.created_at.isoformat()])
    content = buf.getvalue().encode("utf-8")

    filename = f"cloudpay_report_{tenant.slug}_{day.date().isoformat()}.csv"
    stored_file = None
    try:
        path = f"{APP_NAME}/reports/{tenant.id}/{uuid.uuid4()}.csv"
        result = put_object(path, content, "text/csv")
        stored_file = StoredFile(tenant_id=tenant.id, storage_path=result["path"],
                                 original_filename=filename, content_type="text/csv",
                                 size=result.get("size", len(content)), kind="report")
        db.add(stored_file)
        await db.flush()
    except Exception as exc:
        logger.error("report storage upload failed for tenant=%s: %s", tenant.id, exc)

    # Provider-agnostic email adapter (noop until a provider is configured).
    email_result = email_service.send_email(
        to=tenant.contact_email,
        subject=f"CloudPay daily report — {day.date().isoformat()}",
        body=f"Your daily payments & settlements report for {tenant.name} is ready.",
        attachment_url=(f"/api/reports/scheduled/download/{stored_file.id}" if stored_file else None),
    )

    report = ScheduledReport(
        tenant_id=tenant.id, period_date=start, report_type="daily",
        file_id=stored_file.id if stored_file else None,
        recipient_email=tenant.contact_email,
        email_status=email_result.get("status", "skipped_no_provider"),
        payments_count=len(payments), settlements_count=len(settlements), status="generated",
    )
    db.add(report)
    await record_audit(db, action="report.generate", resource_type="scheduled_report",
                       resource_id=report.id, tenant_id=tenant.id,
                       changes={"payments": len(payments), "settlements": len(settlements),
                                "email_status": report.email_status})
    await db.flush()
    return report


async def generate_for_all_tenants(db: AsyncSession) -> int:
    res = await db.execute(select(Tenant).where(Tenant.status == "active", Tenant.is_platform.is_(False)))
    tenants = res.scalars().all()
    for tenant in tenants:
        try:
            await generate_daily_report(db, tenant=tenant)
        except Exception as exc:
            logger.error("daily report failed for tenant=%s: %s", tenant.id, exc)
    await db.commit()
    return len(tenants)
