"""Provider health alerting (provider-agnostic).

Evaluates each provider account against health + success-rate thresholds and notifies operators
via the existing email + outbound-webhook abstractions on state transitions (dedupe: alert once
per healthy->unhealthy transition; send a recovery notice on unhealthy->healthy). Reuses the
Provider Health Board computation so thresholds stay consistent with routing eligibility.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.payment import PaymentProvider, ProviderAlert, ProviderAlertEvent
from app.models.tenant import Tenant
from app.services import email_service, provider_health, webhook_service

logger = logging.getLogger("cloudpay.alerts")


async def get_thresholds(db, tenant_id) -> dict:
    """Per-tenant alert thresholds (stored in tenant.settings), falling back to env defaults."""
    tenant = await db.get(Tenant, tenant_id)
    cfg = (tenant.settings or {}).get("alerts", {}) if tenant else {}
    return {
        "success_rate_threshold": float(cfg.get("success_rate_threshold", settings.alert_success_rate_threshold)),
        "min_sample": int(cfg.get("min_sample", settings.alert_min_sample)),
    }


async def set_thresholds(db, tenant_id, *, success_rate_threshold=None, min_sample=None) -> dict:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("tenant not found")
    current = dict(tenant.settings or {})
    alerts = dict(current.get("alerts", {}))
    if success_rate_threshold is not None:
        srt = float(success_rate_threshold)
        if not 0 <= srt <= 1:
            raise ValueError("success_rate_threshold must be between 0 and 1")
        alerts["success_rate_threshold"] = srt
    if min_sample is not None:
        ms = int(min_sample)
        if ms < 1:
            raise ValueError("min_sample must be >= 1")
        alerts["min_sample"] = ms
    current["alerts"] = alerts
    tenant.settings = current
    await db.commit()
    return await get_thresholds(db, tenant_id)


def _evaluate_account(acc: dict, thresholds: dict) -> tuple[bool, str | None, str | None]:
    """Return (is_bad, severity, reason) for a health-board account entry."""
    if not acc["enabled"]:
        return False, None, None  # operator-disabled accounts are not alerted
    if acc["health_status"] != "up":
        return True, "critical", f"health status is '{acc['health_status']}'"
    m = acc["metrics"]
    rate = m.get("success_rate")
    if m.get("total", 0) >= thresholds["min_sample"] and rate is not None \
            and rate < thresholds["success_rate_threshold"]:
        pct = round(rate * 100)
        thr = round(thresholds["success_rate_threshold"] * 100)
        return True, "warning", f"success rate {pct}% below threshold {thr}% ({m['succeeded']}/{m['total']})"
    return False, None, None


async def _notify(db, tenant_id, acc, event: str, severity, reason):
    subject = f"[CloudPay] Provider {'RECOVERED' if event == 'provider.recovered' else 'ALERT'}: " \
              f"{acc['display_name']} ({acc['environment']})"
    body = (f"Provider account: {acc['provider_key']} ({acc['display_name']})\n"
            f"Environment: {acc['environment']}\nSeverity: {severity or 'info'}\n"
            f"Reason: {reason or 'recovered to healthy'}\n"
            f"Success rate: {acc['metrics'].get('success_rate')}\n")
    email_service.send_email(to=settings.alert_email_to or None, subject=subject, body=body)
    # Outbound webhook to any subscribed tenant endpoints (no-op if none configured). No secrets.
    await webhook_service.dispatch(db, tenant_id=tenant_id, event=event, data={
        "provider_account_id": acc["id"], "provider_key": acc["provider_key"],
        "environment": acc["environment"], "severity": severity, "reason": reason,
        "health_status": acc["health_status"], "success_rate": acc["metrics"].get("success_rate"),
    })


async def evaluate_tenant(db, tenant_id) -> dict:
    board = await provider_health.health_board(db, tenant_id)
    thresholds = await get_thresholds(db, tenant_id)
    now = datetime.now(timezone.utc)
    existing = {str(r.provider_account_id): r for r in (await db.execute(
        select(ProviderAlert).where(ProviderAlert.tenant_id == tenant_id))).scalars().all()}

    changes: list[dict] = []
    active: list[dict] = []
    for env in ("sandbox", "live"):
        for acc in board["environments"][env]["accounts"]:
            bad, severity, reason = _evaluate_account(acc, thresholds)
            row = existing.get(acc["id"])
            if row is None:
                row = ProviderAlert(tenant_id=tenant_id, provider_account_id=acc["id"],
                                    provider_key=acc["provider_key"], environment=env, status="ok")
                db.add(row)
            row.last_evaluated_at = now
            row.success_rate = acc["metrics"].get("success_rate")
            if bad and row.status != "alerting":
                row.status, row.severity, row.reason, row.last_alert_at = "alerting", severity, reason, now
                await _notify(db, tenant_id, acc, "provider.health_alert", severity, reason)
                db.add(ProviderAlertEvent(
                    tenant_id=tenant_id, provider_account_id=acc["id"], provider_key=acc["provider_key"],
                    environment=env, transition="alerting", severity=severity, reason=reason,
                    success_rate=acc["metrics"].get("success_rate")))
                changes.append({"provider_key": acc["provider_key"], "environment": env,
                                "transition": "alerting", "severity": severity, "reason": reason})
            elif bad and row.status == "alerting":
                row.severity, row.reason = severity, reason  # keep current, no re-notify
            elif not bad and row.status == "alerting":
                row.status, row.severity, row.reason = "ok", None, "recovered"
                await _notify(db, tenant_id, acc, "provider.recovered", None, None)
                db.add(ProviderAlertEvent(
                    tenant_id=tenant_id, provider_account_id=acc["id"], provider_key=acc["provider_key"],
                    environment=env, transition="recovered", severity=None, reason="recovered to healthy",
                    success_rate=acc["metrics"].get("success_rate")))
                changes.append({"provider_key": acc["provider_key"], "environment": env,
                                "transition": "recovered"})
            if row.status == "alerting":
                active.append({"provider_account_id": acc["id"], "provider_key": acc["provider_key"],
                               "environment": env, "severity": row.severity, "reason": row.reason,
                               "since": row.last_alert_at.isoformat() if row.last_alert_at else None})
    await db.commit()
    return {"tenant_id": str(tenant_id), "active_alerts": active, "changes": changes,
            "evaluated_at": now.isoformat()}


async def list_active(db, tenant_id) -> list[dict]:
    rows = (await db.execute(select(ProviderAlert).where(
        ProviderAlert.tenant_id == tenant_id, ProviderAlert.status == "alerting"))).scalars().all()
    return [{"provider_account_id": str(r.provider_account_id), "provider_key": r.provider_key,
             "environment": r.environment, "severity": r.severity, "reason": r.reason,
             "since": r.last_alert_at.isoformat() if r.last_alert_at else None} for r in rows]


async def list_history(db, tenant_id, limit: int = 50) -> list[dict]:
    """Recent provider alert transitions (fired/recovered) for the tenant, newest first."""
    rows = (await db.execute(
        select(ProviderAlertEvent).where(ProviderAlertEvent.tenant_id == tenant_id)
        .order_by(ProviderAlertEvent.created_at.desc()).limit(limit))).scalars().all()
    return [{"id": str(r.id), "provider_account_id": str(r.provider_account_id),
             "provider_key": r.provider_key, "environment": r.environment,
             "transition": r.transition, "severity": r.severity, "reason": r.reason,
             "success_rate": float(r.success_rate) if r.success_rate is not None else None,
             "at": r.created_at.isoformat()} for r in rows]


async def evaluate_all(db) -> int:
    tenant_ids = (await db.execute(select(PaymentProvider.tenant_id).distinct())).scalars().all()
    for tid in tenant_ids:
        try:
            await evaluate_tenant(db, tid)
        except Exception as exc:  # pragma: no cover
            logger.error("alert evaluation failed for tenant %s: %s", tid, exc)
    return len(tenant_ids)
