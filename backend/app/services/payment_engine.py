"""Payment engine: orchestrates provider charge, fee engine, ledger and audit.

All financial mutations are server-side validated, tenant-isolated, idempotent
(via idempotency_key) and audit-logged.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.models.payment import Payment, PaymentProvider, Refund
from app.providers.base import ChargeRequest, ProviderConfiguration
from app.providers.registry import get_provider, has_provider
from app.services import (
    fee_engine,
    ledger_service,
    payment_state,
    risk_service,
    routing_engine,
    webhook_service,
)
from app.services.secret_store import get_secret_store


async def _provider_account(db, tenant_id, provider_key, environment):
    res = await db.execute(select(PaymentProvider).where(
        PaymentProvider.tenant_id == tenant_id,
        PaymentProvider.provider_key == provider_key,
        PaymentProvider.mode == environment))
    return res.scalar_one_or_none()


async def _resolve_config(db, account, provider_key, environment) -> ProviderConfiguration:
    """Build the per-account config, resolving credentials from the secret store by reference.

    Raw secrets are fetched only in-memory for the dispatch and never persisted on the account
    or logged. Plugins read them from `config.options['credentials']`.
    """
    creds = None
    ref = getattr(account, "credentials_ref", None)
    if ref:
        creds = await get_secret_store().get(db, ref)
    return ProviderConfiguration(
        provider_key=provider_key, mode=environment, credential_ref=ref,
        options={"credentials": creds} if creds else {},
        enabled=account.enabled if account else True,
    )


async def _existing_payment(db, tenant_id, idempotency_key):
    res = await db.execute(select(Payment).where(
        Payment.tenant_id == tenant_id, Payment.idempotency_key == idempotency_key))
    return res.scalar_one_or_none()


async def _existing_refund(db, tenant_id, idempotency_key):
    res = await db.execute(select(Refund).where(
        Refund.tenant_id == tenant_id, Refund.idempotency_key == idempotency_key))
    return res.scalar_one_or_none()


async def create_payment(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    reference: str,
    amount_minor: int,
    currency: str,
    provider_key: str = "mock",
    environment: str = "sandbox",
    description: str | None = None,
    customer_email: str | None = None,
    idempotency_key: str | None = None,
    metadata: dict | None = None,
    country: str | None = None,
    payment_method: str = "card",
    flow: str = "direct",
) -> Payment:
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")

    # Resolve the payment's country/region: explicit request value, else the tenant's country.
    from app.models.tenant import Tenant
    if country is None:
        tenant = await db.get(Tenant, tenant_id)
        country = tenant.country if tenant else None

    # ---- Capability-aware, environment-aware provider selection (generic contract only) ----
    # provider_key == "auto" -> priority-ordered failover across all ELIGIBLE healthy accounts
    # that support the country, currency, payment method and flow of this payment.
    routing_trace: list[dict] = []
    if provider_key == "auto":
        candidates, routing_trace = await routing_engine.plan_route(
            db, tenant_id, environment=environment, currency=currency,
            payment_method=payment_method, flow=flow, country=country)
        if not candidates:
            raise ValueError(
                f"No eligible provider for {currency}/{payment_method}/{flow}"
                + (f"/{country}" if country else "") + f" in the '{environment}' environment")
        plan = [(a.provider_key, get_provider(a.provider_key), a) for a in candidates]
    else:
        if not has_provider(provider_key):
            raise ValueError(f"Unknown provider '{provider_key}'")
        provider = get_provider(provider_key)
        if not provider.supports_environment(environment):
            raise ValueError(f"Provider '{provider_key}' does not support the '{environment}' environment")
        account = await _provider_account(db, tenant_id, provider_key, environment)
        if account is not None and not account.enabled:
            raise ValueError(f"Provider '{provider_key}' is disabled for the '{environment}' environment")
        # LIVE must never run without an explicitly configured + enabled account (no real money
        # can move by default). SANDBOX stays permissive for the dev/reference provider.
        if environment == "live" and account is None:
            raise ValueError(f"No live provider account configured for '{provider_key}'")
        # Capability enforcement: never route a payment to a provider that does not support its
        # country, currency, payment method or flow — even when explicitly selected.
        if account is not None:
            ok, reason = routing_engine.match_capability(
                account, provider, environment=environment, currency=currency,
                payment_method=payment_method, flow=flow, country=country)
        else:
            ok, reason = routing_engine.match_plugin_capability(
                provider, environment=environment, currency=currency,
                payment_method=payment_method, flow=flow, country=country)
        if not ok:
            raise ValueError(f"Provider '{provider_key}' cannot process this payment ({reason})")
        routing_trace = [{"provider_key": provider_key, "selected": True, "reason": "explicit",
                          "environment": environment}]
        plan = [(provider_key, provider, account)]

    # Enrich request metadata with the resolved routing dimensions (no secrets).
    md = dict(metadata or {})
    md.setdefault("method", payment_method)
    md.setdefault("flow", flow)
    if country:
        md.setdefault("country", country)

    # Idempotency: return existing payment if key already used for this tenant.
    if idempotency_key:
        existing = await _existing_payment(db, tenant_id, idempotency_key)
        if existing:
            return existing

    risk = risk_service.score_payment(amount_minor=amount_minor, customer_email=customer_email)

    payment = Payment(
        tenant_id=tenant_id,
        reference=reference,
        idempotency_key=idempotency_key,
        provider_key=plan[0][0],
        environment=environment,
        amount_minor=amount_minor,
        currency=currency,
        status="pending",
        description=description,
        customer_email=customer_email,
        risk_score=risk,
        metadata_json=md,
        created_by=str(getattr(actor, "id", "")) or None,
    )
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError:
        # Concurrent request with the same idempotency key won the race; return that one.
        await db.rollback()
        existing = await _existing_payment(db, tenant_id, idempotency_key)
        if existing:
            return existing
        raise

    # Idempotency is claimed above by inserting the payment row (unique constraint on
    # tenant_id + idempotency_key) BEFORE dispatching. Then attempt providers in priority order,
    # failing over to the next healthy account until one succeeds (or all are exhausted).
    charge_req = ChargeRequest(
        amount_minor=amount_minor, currency=currency, reference=reference, description=description,
        customer_email=customer_email, idempotency_key=idempotency_key, metadata=md,
    )
    attempts: list[dict] = []
    result = None
    used_key = plan[0][0]
    for pk, plugin, account in plan:
        cfg = await _resolve_config(db, account, pk, environment)
        result = plugin.create_payment(charge_req, cfg)
        used_key = pk
        attempts.append({"provider_key": pk, "status": result.status,
                         "success": result.success, "error": result.error})
        if result.success:
            break

    payment.provider_key = used_key
    payment.provider_txn_id = result.provider_txn_id
    # Record the full routing decision + attempt trace (no secrets) for observability/failover.
    payment.metadata_json = {**(payment.metadata_json or {}),
                             "routing_trace": routing_trace, "routing_attempts": attempts}
    prev_status = payment.status
    payment_state.validate_transition(prev_status, result.status)
    payment.status = result.status

    if result.success:
        fee = await fee_engine.compute_fee(
            db, tenant_id=tenant_id, amount_minor=amount_minor, currency=currency, provider_key=used_key
        )
        payment.fee_minor = fee
        payment.net_minor = amount_minor - fee
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=currency, direction="credit",
            amount_minor=payment.net_minor, ref_type="payment", ref_id=payment.id,
            description=f"Payment {reference}",
        )

    await record_audit(
        db, action="payment.create", resource_type="payment", resource_id=payment.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"previous_state": prev_status, "new_state": payment.status,
                 "amount_minor": amount_minor, "currency": currency,
                 "correlation_id": str(payment.id)},
    )
    await webhook_service.dispatch(
        db, tenant_id=tenant_id,
        event="payment.succeeded" if result.success else "payment.failed",
        data={"payment_id": str(payment.id), "reference": reference, "amount_minor": amount_minor,
              "currency": currency, "status": payment.status, "provider_txn_id": payment.provider_txn_id},
    )
    await db.commit()
    await db.refresh(payment)
    return payment


async def create_refund(
    db: AsyncSession,
    *,
    tenant_id,
    actor,
    payment: Payment,
    amount_minor: int,
    reason: str | None = None,
    idempotency_key: str | None = None,
) -> Refund:
    # Serialize concurrent refunds for this payment with a row-level lock, then recompute the
    # cumulative refunded total from the database UNDER THE LOCK. This closes the race where two
    # concurrent refunds each read a stale total and both pass the cap (over-refund).
    locked = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update())
    payment = locked.scalar_one()

    if not payment_state.is_refundable(payment.status):
        raise ValueError("Payment is not in a refundable state")
    already = (await db.execute(
        select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(
            Refund.payment_id == payment.id, Refund.status == "succeeded"))).scalar_one()
    if amount_minor <= 0 or already + amount_minor > payment.amount_minor:
        raise ValueError("Refund exceeds refundable amount")

    if idempotency_key:
        existing = await _existing_refund(db, tenant_id, idempotency_key)
        if existing:
            return existing

    refund = Refund(
        tenant_id=tenant_id, payment_id=payment.id, amount_minor=amount_minor,
        currency=payment.currency, reason=reason, status="pending",
        idempotency_key=idempotency_key, created_by=str(getattr(actor, "id", "")) or None,
    )
    db.add(refund)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await _existing_refund(db, tenant_id, idempotency_key)
        if existing:
            return existing
        raise

    provider = get_provider(payment.provider_key)
    result = provider.refund(payment.provider_txn_id or "", amount_minor, payment.currency)
    refund.status = result.status if result.success else "failed"
    refund.provider_refund_id = result.provider_txn_id

    prev_status = payment.status
    if result.success:
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=payment.currency, direction="debit",
            amount_minor=amount_minor, ref_type="refund", ref_id=refund.id,
            description=f"Refund for {payment.reference}",
        )
        new_total = already + amount_minor
        target = "refunded" if new_total >= payment.amount_minor else "partially_refunded"
        payment_state.validate_transition(prev_status, target)
        payment.status = target

    await record_audit(
        db, action="refund.create", resource_type="refund", resource_id=refund.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"refund_status": refund.status, "previous_state": prev_status,
                 "new_state": payment.status, "amount_minor": amount_minor,
                 "correlation_id": str(refund.id)},
    )
    await webhook_service.dispatch(
        db, tenant_id=tenant_id,
        event="refund.succeeded" if result.success else "refund.failed",
        data={"refund_id": str(refund.id), "payment_id": str(payment.id),
              "amount_minor": amount_minor, "currency": payment.currency, "status": refund.status},
    )
    await db.commit()
    await db.refresh(refund)
    return refund


async def _existing_payment_credit(db, tenant_id, payment_id) -> int:
    from app.models.finance import LedgerEntry
    return (await db.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
            LedgerEntry.tenant_id == tenant_id, LedgerEntry.ref_type == "payment",
            LedgerEntry.ref_id == payment_id, LedgerEntry.direction == "credit"))).scalar_one()


async def capture_payment(
    db: AsyncSession, *, tenant_id, actor, payment: Payment,
    amount_minor: int | None = None, reason: str | None = None, idempotency_key: str | None = None,
) -> Payment:
    """Capture an eligible AUTHORIZED payment. Provider-agnostic, idempotent, no duplicate credit."""
    locked = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
        .execution_options(populate_existing=True))
    payment = locked.scalar_one()
    md = dict(payment.metadata_json or {})

    # Idempotency: a repeated capture with the same key is a safe no-op (no second provider call).
    if idempotency_key and md.get("capture_idempotency_key") == idempotency_key:
        return payment
    if payment.status != "authorized":
        raise ValueError("Only an authorized payment can be captured")
    cap_amount = payment.amount_minor if amount_minor is None else amount_minor
    if cap_amount <= 0 or cap_amount > payment.amount_minor:
        raise ValueError("Capture amount exceeds the authorized amount")

    provider = get_provider(payment.provider_key)
    if not provider.supports_capture():
        raise ValueError(f"Provider '{payment.provider_key}' does not support capture")
    account = await _provider_account(db, tenant_id, payment.provider_key, payment.environment)
    cfg = await _resolve_config(db, account, payment.provider_key, payment.environment)
    result = provider.capture(payment.provider_txn_id or "", cap_amount, payment.currency, cfg)
    if not result.success:
        raise ValueError(result.error or "Provider capture failed")

    # Never duplicate a ledger credit: post one only if the payment has none yet (an authorized
    # payment may already carry a credit from creation, depending on the provider).
    if await _existing_payment_credit(db, tenant_id, payment.id) == 0:
        fee = await fee_engine.compute_fee(db, tenant_id=tenant_id, amount_minor=cap_amount,
                                           currency=payment.currency, provider_key=payment.provider_key)
        payment.fee_minor = fee
        payment.net_minor = cap_amount - fee
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=payment.currency, direction="credit",
            amount_minor=payment.net_minor, ref_type="payment", ref_id=payment.id,
            description=f"Capture {payment.reference}")

    prev_status = payment.status
    payment_state.validate_transition(prev_status, "captured")
    payment.status = "captured"
    md["capture_idempotency_key"] = idempotency_key
    payment.metadata_json = md

    await record_audit(
        db, action="payment.capture", resource_type="payment", resource_id=payment.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"previous_state": prev_status, "new_state": payment.status,
                 "amount_minor": cap_amount, "currency": payment.currency, "reason": reason,
                 "provider_txn_id": payment.provider_txn_id, "correlation_id": str(payment.id)})
    await webhook_service.dispatch(
        db, tenant_id=tenant_id, event="payment.captured",
        data={"payment_id": str(payment.id), "reference": payment.reference,
              "amount_minor": cap_amount, "currency": payment.currency, "status": payment.status,
              "provider_txn_id": payment.provider_txn_id})
    await db.commit()
    await db.refresh(payment)
    return payment


async def void_payment(
    db: AsyncSession, *, tenant_id, actor, payment: Payment,
    reason: str | None = None, idempotency_key: str | None = None,
) -> Payment:
    """Void/cancel an eligible AUTHORIZED payment before capture. Idempotent; creates no money."""
    locked = await db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
        .execution_options(populate_existing=True))
    payment = locked.scalar_one()
    md = dict(payment.metadata_json or {})

    if idempotency_key and md.get("void_idempotency_key") == idempotency_key:
        return payment
    if payment.status != "authorized":
        raise ValueError("Only an authorized payment can be voided")

    provider = get_provider(payment.provider_key)
    if not provider.supports_void():
        raise ValueError(f"Provider '{payment.provider_key}' does not support void")
    account = await _provider_account(db, tenant_id, payment.provider_key, payment.environment)
    cfg = await _resolve_config(db, account, payment.provider_key, payment.environment)
    result = provider.void(payment.provider_txn_id or "", cfg)
    if not result.success:
        raise ValueError(result.error or "Provider void failed")

    # Void creates no money: it only unwinds a credit the authorization may already have posted.
    credited = await _existing_payment_credit(db, tenant_id, payment.id)
    if credited > 0:
        await ledger_service.post_entry(
            db, tenant_id=tenant_id, currency=payment.currency, direction="debit",
            amount_minor=int(credited), ref_type="void", ref_id=payment.id,
            description=f"Void of {payment.reference}")

    prev_status = payment.status
    payment_state.validate_transition(prev_status, "cancelled")
    payment.status = "cancelled"
    md["void_idempotency_key"] = idempotency_key
    payment.metadata_json = md

    await record_audit(
        db, action="payment.void", resource_type="payment", resource_id=payment.id,
        tenant_id=tenant_id, actor_id=str(getattr(actor, "id", "")) or None,
        actor_email=getattr(actor, "email", None),
        changes={"previous_state": prev_status, "new_state": payment.status, "reason": reason,
                 "provider_txn_id": payment.provider_txn_id, "correlation_id": str(payment.id)})
    await webhook_service.dispatch(
        db, tenant_id=tenant_id, event="payment.voided",
        data={"payment_id": str(payment.id), "reference": payment.reference,
              "currency": payment.currency, "status": payment.status,
              "provider_txn_id": payment.provider_txn_id})
    await db.commit()
    await db.refresh(payment)
    return payment
