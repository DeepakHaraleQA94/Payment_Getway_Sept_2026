"""Report generation: build CSV (payments + settlements) for a period, store it, notify (adapter).

Supports daily, weekly and monthly reports. Daily covers the report's own calendar day; weekly
covers the trailing 7 days (Mon-Sun when run on a Monday); monthly covers the previous calendar
month. The window start is stored as the report's period_date.
"""
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

VALID_TYPES = ("daily", "weekly", "monthly")


def _fmt(minor: int) -> str:
    return f"{(minor or 0) / 100:.2f}"


def _period_window(report_type: str, period_date: datetime | None) -> tuple[datetime, datetime, str]:
    """Return (start, end, label) for the report window. `start` is stored as period_date."""
    d = (period_date or datetime.now(timezone.utc)).astimezone(timezone.utc)
    midnight = datetime.combine(d.date(), time.min, tzinfo=timezone.utc)
    if report_type == "weekly":
        start = midnight - timedelta(days=7)
        end = midnight
        label = f"{start.date().isoformat()} → {(end - timedelta(days=1)).date().isoformat()}"
    elif report_type == "monthly":
        first_this = midnight.replace(day=1)
        start = (first_this - timedelta(days=1)).replace(day=1)
        end = first_this
        label = start.strftime("%B %Y")
    else:  # daily
        start = midnight
        end = midnight + timedelta(days=1)
        label = start.date().isoformat()
    return start, end, label


async def generate_report(db: AsyncSession, *, tenant: Tenant, report_type: str = "daily",
                          period_date: datetime | None = None) -> ScheduledReport:
    if report_type not in VALID_TYPES:
        raise ValueError(f"invalid report_type: {report_type}")
    start, end, label = _period_window(report_type, period_date)

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
    w.writerow([f"CloudPay {report_type.capitalize()} Report — {tenant.name}", label])
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

    filename = f"cloudpay_{report_type}_{tenant.slug}_{start.date().isoformat()}.csv"
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
        subject=f"CloudPay {report_type} report — {label}",
        body=f"Your {report_type} payments & settlements report for {tenant.name} ({label}) is ready.",
        attachment_url=(f"/api/reports/scheduled/download/{stored_file.id}" if stored_file else None),
    )

    report = ScheduledReport(
        tenant_id=tenant.id, period_date=start, report_type=report_type,
        file_id=stored_file.id if stored_file else None,
        recipient_email=tenant.contact_email,
        email_status=email_result.get("status", "skipped_no_provider"),
        payments_count=len(payments), settlements_count=len(settlements), status="generated",
    )
    db.add(report)
    await record_audit(db, action="report.generate", resource_type="scheduled_report",
                       resource_id=report.id, tenant_id=tenant.id,
                       changes={"report_type": report_type, "payments": len(payments),
                                "settlements": len(settlements), "email_status": report.email_status})
    await db.flush()
    return report


async def generate_daily_report(db: AsyncSession, *, tenant: Tenant,
                                period_date: datetime | None = None) -> ScheduledReport:
    return await generate_report(db, tenant=tenant, report_type="daily", period_date=period_date)


async def generate_for_all_tenants(db: AsyncSession, report_type: str = "daily") -> int:
    res = await db.execute(select(Tenant).where(Tenant.status == "active", Tenant.is_platform.is_(False)))
    tenants = res.scalars().all()
    for tenant in tenants:
        try:
            await generate_report(db, tenant=tenant, report_type=report_type)
        except Exception as exc:
            logger.error("%s report failed for tenant=%s: %s", report_type, tenant.id, exc)
    await db.commit()
    return len(tenants)
