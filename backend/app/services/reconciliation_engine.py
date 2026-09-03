"""Line-level payment reconciliation & matching engine (REPORT-ONLY).

Deterministic, tenant-isolated, idempotent matching of internal CloudPay payments against provider
records (uploaded transaction lines and/or provider-pulled status). It ONLY records findings in the
reconciliation_runs / reconciliation_items tables — it never posts ledger entries, never changes
balances, and never modifies payments or settlements. Corrections remain manual via reversal/refund.

Outcome categories (fixed): matched, amount_mismatch, currency_mismatch, status_mismatch,
missing_in_cloudpay, missing_at_provider, duplicate.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import ReconciliationItem, ReconciliationRun
from app.models.payment import Payment
from app.providers.registry import get_provider

logger = logging.getLogger("cloudpay.reconciliation")

OUTCOMES = ["matched", "amount_mismatch", "currency_mismatch", "status_mismatch",
            "missing_in_cloudpay", "missing_at_provider", "duplicate"]

# Canonical status buckets used to compare provider vs internal statuses provider-agnostically.
_BUCKETS = {
    "success": {"succeeded", "captured", "paid", "settled", "complete", "completed", "success", "captured_settled"},
    "pending": {"pending", "authorized", "requires_capture", "processing", "created", "in_progress"},
    "failed": {"failed", "declined", "cancelled", "canceled", "voided", "void", "expired", "error"},
    "refunded": {"refunded", "partially_refunded", "reversed", "chargeback"},
}


def _bucket(status: str | None) -> str:
    s = (status or "").strip().lower()
    for name, members in _BUCKETS.items():
        if s in members:
            return name
    return "unknown"


async def run_reconciliation(
    db: AsyncSession, *, tenant_id, actor, source: str,
    provider_lines: list[dict] | None = None, currency: str | None = None,
    filename: str | None = None, run_ref: str | None = None,
) -> ReconciliationRun:
    """Execute a reconciliation run and persist its line-level results. Read-only on financial data."""
    if source not in ("upload", "provider_pull", "both"):
        raise ValueError("source must be 'upload', 'provider_pull' or 'both'")
    provider_lines = provider_lines or []
    if source in ("upload", "both") and not provider_lines and source != "both":
        raise ValueError("upload source requires provider transaction lines")

    # Idempotency: a run with the same (tenant, run_ref) is returned as-is (safe to re-request).
    if run_ref:
        existing = (await db.execute(select(ReconciliationRun).where(
            ReconciliationRun.tenant_id == tenant_id,
            ReconciliationRun.run_ref == run_ref))).scalar_one_or_none()
        if existing is not None:
            return existing

    # Load internal payments (tenant-isolated; optional currency filter).
    q = select(Payment).where(Payment.tenant_id == tenant_id)
    if currency:
        q = q.where(Payment.currency == currency.upper())
    payments = (await db.execute(q)).scalars().all()
    by_txn = {p.provider_txn_id: p for p in payments if p.provider_txn_id}
    by_ref = {}
    for p in payments:
        by_ref.setdefault(p.reference, p)

    items: list[ReconciliationItem] = []
    consumed_payment_ids: set = set()
    seen_txn: set = set()

    # --- Pass 1: match each provider (uploaded) line against internal payments ---
    for line in provider_lines:
        ptxn = str(line.get("provider_txn_id") or "").strip() or None
        pref = str(line.get("reference") or "").strip() or None
        pcur = (str(line.get("currency") or "").strip().upper() or None)
        pstatus = str(line.get("status") or "").strip() or None
        try:
            pamt = int(line["amount_minor"]) if line.get("amount_minor") not in (None, "") else None
        except (ValueError, TypeError):
            pamt = None

        # Duplicate provider reference within the same file.
        dedup_key = ptxn or pref
        if dedup_key and dedup_key in seen_txn:
            items.append(ReconciliationItem(
                tenant_id=tenant_id, outcome="duplicate", provider_txn_id=ptxn, reference=pref,
                provider_amount_minor=pamt, currency=pcur, provider_status=pstatus,
                detail="duplicate provider reference in source file"))
            continue
        if dedup_key:
            seen_txn.add(dedup_key)

        match = (by_txn.get(ptxn) if ptxn else None) or (by_ref.get(pref) if pref else None)
        if match is None:
            items.append(ReconciliationItem(
                tenant_id=tenant_id, outcome="missing_in_cloudpay", provider_txn_id=ptxn,
                reference=pref, provider_amount_minor=pamt, currency=pcur, provider_status=pstatus,
                detail="provider record has no matching CloudPay payment"))
            continue

        if match.id in consumed_payment_ids:
            items.append(ReconciliationItem(
                tenant_id=tenant_id, outcome="duplicate", provider_txn_id=ptxn, reference=pref,
                payment_id=match.id, provider_amount_minor=pamt, internal_amount_minor=match.amount_minor,
                currency=pcur, provider_status=pstatus, internal_status=match.status,
                detail="multiple provider lines match the same CloudPay payment"))
            continue
        consumed_payment_ids.add(match.id)

        base = dict(tenant_id=tenant_id, provider_txn_id=ptxn or match.provider_txn_id,
                    reference=pref or match.reference, payment_id=match.id,
                    provider_amount_minor=pamt, internal_amount_minor=match.amount_minor,
                    currency=pcur or match.currency, provider_status=pstatus,
                    internal_status=match.status)
        if pcur and pcur != match.currency:
            items.append(ReconciliationItem(outcome="currency_mismatch",
                detail=f"provider {pcur} vs internal {match.currency}", **base))
        elif pamt is not None and pamt != match.amount_minor:
            items.append(ReconciliationItem(outcome="amount_mismatch",
                detail=f"provider {pamt} vs internal {match.amount_minor}", **base))
        elif pstatus and _bucket(pstatus) != _bucket(match.status) and _bucket(pstatus) != "unknown":
            items.append(ReconciliationItem(outcome="status_mismatch",
                detail=f"provider '{pstatus}' vs internal '{match.status}'", **base))
        else:
            items.append(ReconciliationItem(outcome="matched", detail="exact match", **base))

    # --- Pass 2: internal payments not matched by any uploaded line ---
    for p in payments:
        if p.id in consumed_payment_ids:
            continue
        provider_status = None
        detail = "no matching provider record"
        if source in ("provider_pull", "both") and p.provider_txn_id:
            # Best-effort provider-pull. Read-only; failures never break the run.
            try:
                res = get_provider(p.provider_key).get_payment_status(p.provider_txn_id, None)
                provider_status = getattr(res, "normalized_status", None)
            except Exception as exc:  # noqa: BLE001
                logger.info("provider-pull unavailable for %s: %s", p.provider_txn_id, exc)
                provider_status = None
        if provider_status and _bucket(provider_status) != "unknown":
            if _bucket(provider_status) == _bucket(p.status):
                outcome = "matched"
                detail = "matched via provider status pull"
            else:
                outcome = "status_mismatch"
                detail = f"provider '{provider_status}' vs internal '{p.status}'"
        else:
            outcome = "missing_at_provider"
            if source == "provider_pull":
                detail = "provider has no record / status unavailable for this payment"
        items.append(ReconciliationItem(
            tenant_id=tenant_id, outcome=outcome, provider_txn_id=p.provider_txn_id,
            reference=p.reference, payment_id=p.id, internal_amount_minor=p.amount_minor,
            currency=p.currency, provider_status=provider_status, internal_status=p.status,
            detail=detail))

    summary = {o: 0 for o in OUTCOMES}
    for it in items:
        summary[it.outcome] = summary.get(it.outcome, 0) + 1
    matched = summary["matched"]
    total = len(items)

    run = ReconciliationRun(
        tenant_id=tenant_id, source=source, filename=filename,
        currency=currency.upper() if currency else None, total_lines=total, matched_count=matched,
        discrepancy_count=total - matched, summary=summary, run_ref=run_ref,
        created_by=str(getattr(actor, "id", "")) or None)
    db.add(run)
    await db.flush()
    for it in items:
        it.run_id = run.id
        db.add(it)
    await db.flush()
    return run
