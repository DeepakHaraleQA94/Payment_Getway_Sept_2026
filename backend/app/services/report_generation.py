"""Report generation: build CSV (payments + settlements) for a period, store it, notify (adapter).

Supports daily, weekly, monthly and custom-range reports. Daily covers the report's own calendar
day; weekly the trailing 7 days; monthly the previous calendar month; custom an explicit start/end.
Email delivery stays behind the provider-agnostic `email_service` adapter — per-tenant settings
(recipient, enable/disable, frequencies, CSV attachment) are honoured so a real provider (Resend/
SendGrid/SES) can be connected later without touching the core reporting logic.
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

VALID_TYPES = ("daily", "weekly", "monthly", "custom")

DEFAULT_EMAIL_SETTINGS = {
    "enabled": False,
    "recipient_email": None,
    "frequencies": ["weekly", "monthly"],
    "attach_csv": True,
}


def _fmt(minor: int) -> str:
    return f"{(minor or 0) / 100:.2f}"


def _period_window(report_type: str, period_date: datetime | None,
                   start: datetime | None = None, end: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Return (start, end, label). `start` is stored as the report's period_date. `end` is exclusive."""
    if report_type == "custom":
        if not start or not end:
            raise ValueError("custom report requires start and end dates")
        s = start.astimezone(timezone.utc)
        e = end.astimezone(timezone.utc)
        if e <= s:
            raise ValueError("end must be after start")
        label = f"{s.date().isoformat()} → {(e - timedelta(days=1)).date().isoformat()}"
        return s, e, label

    d = (period_date or datetime.now(timezone.utc)).astimezone(timezone.utc)
    midnight = datetime.combine(d.date(), time.min, tzinfo=timezone.utc)
    if report_type == "weekly":
        ws = midnight - timedelta(days=7)
        return ws, midnight, f"{ws.date().isoformat()} → {(midnight - timedelta(days=1)).date().isoformat()}"
    if report_type == "monthly":
        first_this = midnight.replace(day=1)
        ms = (first_this - timedelta(days=1)).replace(day=1)
        return ms, first_this, ms.strftime("%B %Y")
    return midnight, midnight + timedelta(days=1), midnight.date().isoformat()  # daily


async def get_email_settings(db: AsyncSession, tenant_id) -> dict:
    tenant = await db.get(Tenant, tenant_id)
    cfg = dict(DEFAULT_EMAIL_SETTINGS)
    if tenant:
        cfg.update((tenant.settings or {}).get("report_email", {}))
        if not cfg.get("recipient_email"):
            cfg["recipient_email"] = tenant.contact_email
    return cfg


async def set_email_settings(db: AsyncSession, tenant_id, *, enabled=None, recipient_email=None,
                             frequencies=None, attach_csv=None) -> dict:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("tenant not found")
    current = dict(tenant.settings or {})
    cfg = dict(current.get("report_email", {}))
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    if recipient_email is not None:
        cfg["recipient_email"] = recipient_email or None
    if frequencies is not None:
        valid = {"daily", "weekly", "monthly"}
        cfg["frequencies"] = [f for f in frequencies if f in valid]
    if attach_csv is not None:
        cfg["attach_csv"] = bool(attach_csv)
    current["report_email"] = cfg
    tenant.settings = current
    await db.commit()
    return await get_email_settings(db, tenant_id)


async def generate_report(db: AsyncSession, *, tenant: Tenant, report_type: str = "daily",
                          period_date: datetime | None = None,
                          start: datetime | None = None, end: datetime | None = None) -> ScheduledReport:
    if report_type not in VALID_TYPES:
        raise ValueError(f"invalid report_type: {report_type}")
    win_start, win_end, label = _period_window(report_type, period_date, start, end)

    pay_res = await db.execute(
        select(Payment).where(Payment.tenant_id == tenant.id,
                              Payment.created_at >= win_start, Payment.created_at < win_end)
        .order_by(Payment.created_at.asc())
    )
    payments = pay_res.scalars().all()
    stl_res = await db.execute(
        select(Settlement).where(Settlement.tenant_id == tenant.id,
                                 Settlement.created_at >= win_start, Settlement.created_at < win_end)
        .order_by(Settlement.created_at.asc())
    )
    settlements = stl_res.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"CloudPay {report_type.capitalize()} Report — {tenant.name}", label])
    w.writerow([])
    w.writerow(["PAYMENTS"])
    w.writerow(["reference", "method", "status", "amount", "fee", "net", "currency", "customer_email", "created_at"])
    for p in payments:
        _pm = str(p.payment_method or (p.metadata_json or {}).get("method") or "").lower()
        if not _pm:
            _pm = "upi" if p.provider_key == "demo_upi" else "card"
        _pm = "upi" if "upi" in _pm else "card"
        w.writerow([p.reference, _pm, p.status, _fmt(p.amount_minor), _fmt(p.fee_minor), _fmt(p.net_minor),
                    p.currency, p.customer_email or "", p.created_at.isoformat()])
    w.writerow([])
    w.writerow(["SETTLEMENTS"])
    w.writerow(["reference", "status", "gross", "fees", "net", "currency", "txn_count", "created_at"])
    for s in settlements:
        w.writerow([s.reference, s.status, _fmt(s.gross_minor), _fmt(s.fees_minor), _fmt(s.net_minor),
                    s.currency, s.txn_count, s.created_at.isoformat()])
    # Method breakdown so payouts can be split by rail (UPI vs Card) across the report's payments.
    w.writerow([])
    w.writerow(["METHOD BREAKDOWN"])
    w.writerow(["method", "count", "gross", "fees", "net"])
    _mb: dict[str, list[int]] = {}
    for p in payments:
        _m = str((p.metadata_json or {}).get("method") or ("upi" if p.provider_key == "demo_upi" else "card")).lower()
        _m = "upi" if "upi" in _m else "card"
        agg = _mb.setdefault(_m, [0, 0, 0, 0])
        agg[0] += 1
        agg[1] += p.amount_minor or 0
        agg[2] += p.fee_minor or 0
        agg[3] += p.net_minor or 0
    for _m, agg in sorted(_mb.items()):
        w.writerow([_m, agg[0], _fmt(agg[1]), _fmt(agg[2]), _fmt(agg[3])])
    content = buf.getvalue().encode("utf-8")

    filename = f"cloudpay_{report_type}_{tenant.slug}_{win_start.date().isoformat()}.csv"
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

    # Provider-agnostic email adapter, gated by per-tenant settings (noop until a provider is wired).
    ecfg = {**DEFAULT_EMAIL_SETTINGS, **((tenant.settings or {}).get("report_email", {}))}
    recipient = ecfg.get("recipient_email") or tenant.contact_email
    freqs = ecfg.get("frequencies") or []
    should_send = bool(ecfg.get("enabled")) and (report_type == "custom" or report_type in freqs)
    if should_send:
        attach = None
        attachment_url = None
        if stored_file and ecfg.get("attach_csv", True):
            # Attach the CSV bytes directly (already in memory) so the recipient gets the file.
            attach = {"filename": filename, "content": content, "content_type": "text/csv"}
            attachment_url = f"/api/reports/scheduled/download/{stored_file.id}"
        email_result = email_service.send_email(
            to=recipient,
            subject=f"CloudPay {report_type} report — {label}",
            body=f"Your {report_type} payments & settlements report for {tenant.name} ({label}) is ready.",
            attachment_url=attachment_url,
            attachment=attach,
        )
        email_status = email_result.get("status", "skipped_no_provider")
    else:
        email_status = "disabled" if not ecfg.get("enabled") else "skipped_frequency"

    report = ScheduledReport(
        tenant_id=tenant.id, period_date=win_start, report_type=report_type,
        file_id=stored_file.id if stored_file else None,
        recipient_email=recipient, email_status=email_status,
        payments_count=len(payments), settlements_count=len(settlements), status="generated",
    )
    db.add(report)
    await record_audit(db, action="report.generate", resource_type="scheduled_report",
                       resource_id=report.id, tenant_id=tenant.id,
                       changes={"report_type": report_type, "payments": len(payments),
                                "settlements": len(settlements), "email_status": email_status})
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
