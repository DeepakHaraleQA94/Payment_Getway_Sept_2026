"""Operator-facing Provider Health Board (read-only, provider-agnostic).

Aggregates, per tenant + environment, each configured provider account's enable/health state,
priority + routing eligibility (consistent with routing_engine), recent success/failure metrics,
recent provider errors, recent failover activity, and a live health-check timestamp. Never
exposes credentials, secret values, or credential references.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.payment import Payment, PaymentProvider
from app.providers.registry import get_provider, has_provider

_RECENT_PAYMENT_SCAN = 400  # most recent payments per tenant scanned for metrics/failovers


async def health_board(db, tenant_id) -> dict:
    now = datetime.now(timezone.utc).isoformat()

    accounts = (await db.execute(
        select(PaymentProvider).where(PaymentProvider.tenant_id == tenant_id)
        .order_by(PaymentProvider.mode.asc(), PaymentProvider.priority.asc())
    )).scalars().all()

    recent = (await db.execute(
        select(Payment).where(Payment.tenant_id == tenant_id)
        .order_by(Payment.created_at.desc()).limit(_RECENT_PAYMENT_SCAN)
    )).scalars().all()

    # Per (environment, provider_key) metrics + errors from recent payments.
    metrics: dict[tuple, dict] = {}
    failovers: dict[str, list] = {"sandbox": [], "live": []}
    for p in recent:
        env = p.environment or "sandbox"
        key = (env, p.provider_key)
        m = metrics.setdefault(key, {"total": 0, "succeeded": 0, "failed": 0,
                                     "last_payment_at": None, "errors": []})
        m["total"] += 1
        if p.status in ("succeeded", "captured"):
            m["succeeded"] += 1
        elif p.status == "failed":
            m["failed"] += 1
        if m["last_payment_at"] is None:
            m["last_payment_at"] = p.created_at.isoformat()

        attempts = (p.metadata_json or {}).get("routing_attempts") or []
        # Collect provider errors from routing attempts (attributed to each attempted provider).
        for a in attempts:
            if a.get("error"):
                em = metrics.setdefault((env, a.get("provider_key")),
                                        {"total": 0, "succeeded": 0, "failed": 0,
                                         "last_payment_at": None, "errors": []})
                if len(em["errors"]) < 5:
                    em["errors"].append({"reference": p.reference, "error": a["error"],
                                         "at": p.created_at.isoformat()})
        # A payment that tried more than one provider is a failover event.
        if len(attempts) > 1 and len(failovers.get(env, [])) < 10:
            failovers.setdefault(env, []).append({
                "reference": p.reference, "at": p.created_at.isoformat(),
                "final_provider": p.provider_key, "status": p.status,
                "attempts": [{"provider_key": a.get("provider_key"), "success": a.get("success"),
                              "status": a.get("status"), "error": a.get("error")} for a in attempts],
            })

    environments: dict[str, dict] = {"sandbox": {"accounts": [], "recent_failovers": []},
                                     "live": {"accounts": [], "recent_failovers": []}}
    for acc in accounts:
        env = acc.mode
        registered = has_provider(acc.provider_key)
        health = get_provider(acc.provider_key).health_check(env) if registered else \
            {"status": "unregistered", "environment": env}
        healthy = health.get("status") == "up"
        supports_env = registered and get_provider(acc.provider_key).supports_environment(env)
        m = metrics.get((env, acc.provider_key),
                        {"total": 0, "succeeded": 0, "failed": 0, "last_payment_at": None, "errors": []})
        success_rate = round(m["succeeded"] / m["total"], 3) if m["total"] else None
        environments.setdefault(env, {"accounts": [], "recent_failovers": []})["accounts"].append({
            "id": str(acc.id),
            "provider_key": acc.provider_key,
            "display_name": acc.display_name,
            "environment": env,
            "enabled": acc.enabled,
            "priority": acc.priority,
            "registered": registered,
            "supports_environment": supports_env,
            "health_status": health.get("status"),
            "routing_eligible": bool(acc.enabled and registered and supports_env and healthy),
            "has_credentials": acc.credentials_ref is not None,  # boolean only — never the ref/secret
            "metrics": {"total": m["total"], "succeeded": m["succeeded"], "failed": m["failed"],
                        "success_rate": success_rate, "last_payment_at": m["last_payment_at"]},
            "recent_errors": m["errors"],
            "checked_at": now,
        })

    for env in ("sandbox", "live"):
        environments[env]["recent_failovers"] = failovers.get(env, [])

    return {"tenant_id": str(tenant_id), "checked_at": now, "environments": environments}
