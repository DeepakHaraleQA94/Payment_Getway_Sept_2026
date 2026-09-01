"""Feature flags, providers, fee rules management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.models.feature import FeatureFlag
from app.models.finance import FeeRule
from app.models.payment import Payment, PaymentProvider
from app.providers.base import ChargeRequest, ProviderError
from app.providers.registry import get_provider, has_provider, list_providers
from app.schemas import (
    FeatureFlagCreate,
    FeatureFlagOut,
    FeatureFlagUpdate,
    FeeRuleCreate,
    FeeRuleOut,
    ProviderCreate,
    ProviderOut,
)
from app.services import payment_state

router = APIRouter(prefix="/api", tags=["config"])


class ProviderFlowRequest(BaseModel):
    amount_minor: int
    currency: str = "USD"
    reference: str = "REF"
    description: str | None = None
    customer_email: str | None = None
    metadata: dict = {}

    def to_charge_request(self) -> ChargeRequest:
        return ChargeRequest(amount_minor=self.amount_minor, currency=self.currency,
                             reference=self.reference, description=self.description,
                             customer_email=self.customer_email, metadata=self.metadata or {})


# ---- Feature flags ----
@router.get("/features", response_model=list[FeatureFlagOut])
async def list_features(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(FeatureFlag).where(FeatureFlag.tenant_id == tid).order_by(FeatureFlag.key))
    return res.scalars().all()


@router.post("/features", response_model=FeatureFlagOut)
async def create_feature(body: FeatureFlagCreate, tenant_id: str | None = None,
                         db: AsyncSession = Depends(get_db), user=Depends(require_permission("feature.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    ff = FeatureFlag(tenant_id=tid, created_by=str(user.id), **body.model_dump())
    db.add(ff)
    await db.flush()
    await record_audit(db, action="feature.create", resource_type="feature_flag", resource_id=ff.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes=body.model_dump())
    await db.commit()
    await db.refresh(ff)
    return ff


@router.patch("/features/{feature_id}", response_model=FeatureFlagOut)
async def update_feature(feature_id: uuid.UUID, body: FeatureFlagUpdate, db: AsyncSession = Depends(get_db),
                         user=Depends(require_permission("feature.manage"))):
    ff = await db.get(FeatureFlag, feature_id)
    if not ff:
        raise HTTPException(status_code=404, detail="Feature not found")
    if not user.is_superadmin and ff.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(ff, k, v)
    ff.updated_by = str(user.id)
    await record_audit(db, action="feature.update", resource_type="feature_flag", resource_id=ff.id,
                       tenant_id=ff.tenant_id, actor_id=str(user.id), actor_email=user.email, changes=changes)
    await db.commit()
    await db.refresh(ff)
    return ff


# ---- Providers ----
@router.get("/providers/available")
async def available_providers(user=Depends(get_current_user)):
    return list_providers()


@router.get("/providers/{provider_key}/capabilities")
async def provider_capabilities(provider_key: str, user=Depends(get_current_user)):
    if not has_provider(provider_key):
        raise HTTPException(status_code=404, detail="Unknown provider")
    return get_provider(provider_key).capabilities()


@router.get("/providers/{provider_key}/health")
async def provider_health(provider_key: str, user=Depends(get_current_user)):
    if not has_provider(provider_key):
        raise HTTPException(status_code=404, detail="Unknown provider")
    return {"provider": provider_key, **get_provider(provider_key).health_check()}


def _require_plugin(provider_key: str):
    if not has_provider(provider_key):
        raise HTTPException(status_code=404, detail="Unknown provider")
    return get_provider(provider_key)


@router.post("/providers/{provider_key}/intent")
async def provider_generate_intent(provider_key: str, body: ProviderFlowRequest,
                                   user=Depends(require_permission("provider.manage"))):
    """Generic INTENT-flow initiation via the plugin contract (provider-agnostic)."""
    plugin = _require_plugin(provider_key)
    if not plugin.supports_intent():
        raise HTTPException(status_code=400, detail="Provider does not support the intent flow")
    try:
        intent = plugin.generate_intent(body.to_charge_request())
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"intent_id": intent.intent_id, "client_token": intent.client_token,
            "redirect_url": intent.redirect_url, "expires_at": intent.expires_at}


@router.post("/providers/{provider_key}/qr")
async def provider_generate_qr(provider_key: str, body: ProviderFlowRequest,
                               user=Depends(require_permission("provider.manage"))):
    """Generic QR-flow initiation via the plugin contract. Payload is never card data."""
    plugin = _require_plugin(provider_key)
    if not plugin.supports_qr():
        raise HTTPException(status_code=400, detail="Provider does not support the QR flow")
    try:
        qr = plugin.generate_qr(body.to_charge_request())
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"qr_id": qr.qr_id, "qr_payload": qr.qr_payload,
            "image_data_url": qr.image_data_url, "expires_at": qr.expires_at}


@router.get("/providers/{provider_key}/status/{provider_txn_id}")
async def provider_get_status(provider_key: str, provider_txn_id: str,
                              user=Depends(get_current_user)):
    """Generic status lookup via the plugin contract."""
    plugin = _require_plugin(provider_key)
    st = plugin.get_payment_status(provider_txn_id)
    return {"provider_txn_id": st.provider_txn_id, "status": st.normalized_status}


@router.post("/providers/{provider_key}/reconcile/{provider_txn_id}")
async def provider_reconcile(provider_key: str, provider_txn_id: str,
                             user=Depends(require_permission("provider.manage"))):
    """Generic reconciliation via the plugin contract (fetch provider source-of-truth)."""
    plugin = _require_plugin(provider_key)
    rec = plugin.reconcile(provider_txn_id)
    return {"provider_txn_id": rec.provider_txn_id, "status": rec.normalized_status,
            "matched": rec.matched}


@router.post("/providers/{provider_key}/webhook")
async def provider_inbound_webhook(provider_key: str, request: Request,
                                   db: AsyncSession = Depends(get_db)):
    """Generic inbound provider webhook (provider -> CloudPay). Public, no session auth.

    The core contains NO provider-specific logic: it delegates verification + translation
    to the plugin's `verify_webhook`, which returns a normalized event, then reconciles the
    matching payment's status via the state machine. Never posts ledger entries — the
    synchronous charge flow owns financial mutations.
    """
    if not has_provider(provider_key):
        raise HTTPException(status_code=404, detail="Unknown provider")
    plugin = get_provider(provider_key)
    if not plugin.supports_webhooks():
        raise HTTPException(status_code=400, detail="Provider does not support webhooks")

    payload = await request.body()
    try:
        event = plugin.verify_callback(payload, dict(request.headers))
    except NotImplementedError:
        raise HTTPException(status_code=400, detail="Provider does not implement callbacks")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook payload or signature")

    if not event.provider_txn_id or not event.normalized_status:
        return {"received": True, "ignored": True}

    res = await db.execute(select(Payment).where(
        Payment.provider_txn_id == event.provider_txn_id, Payment.provider_key == provider_key))
    payment = res.scalar_one_or_none()
    if not payment:
        return {"received": True, "unmatched": True}

    prev = payment.status
    if prev == event.normalized_status:
        return {"received": True, "already": prev}
    if not payment_state.can_transition(prev, event.normalized_status):
        return {"received": True, "skipped": True}

    payment_state.validate_transition(prev, event.normalized_status)
    payment.status = event.normalized_status
    await record_audit(db, action="payment.webhook_reconcile", resource_type="payment",
                       resource_id=payment.id, tenant_id=payment.tenant_id, actor_id=None,
                       actor_email=f"{provider_key}:webhook",
                       changes={"previous_state": prev, "new_state": event.normalized_status,
                                "event_type": event.event_type, "correlation_id": str(payment.id)})
    await db.commit()
    return {"received": True, "reconciled": True, "status": event.normalized_status}


@router.get("/providers", response_model=list[ProviderOut])
async def list_tenant_providers(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                                user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(PaymentProvider).where(PaymentProvider.tenant_id == tid)
                           .order_by(PaymentProvider.priority))
    return res.scalars().all()


@router.post("/providers", response_model=ProviderOut)
async def add_provider(body: ProviderCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                       user=Depends(require_permission("provider.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    exists = await db.execute(select(PaymentProvider).where(
        PaymentProvider.tenant_id == tid, PaymentProvider.provider_key == body.provider_key))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Provider already configured")
    if body.mode == "live":
        raise HTTPException(status_code=400,
                            detail="Live mode disabled until an authorized production provider is configured")
    prov = PaymentProvider(tenant_id=tid, created_by=str(user.id), **body.model_dump())
    db.add(prov)
    await db.flush()
    await record_audit(db, action="provider.create", resource_type="payment_provider", resource_id=prov.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes=body.model_dump())
    await db.commit()
    await db.refresh(prov)
    return prov


# ---- Fee rules ----
@router.get("/fees", response_model=list[FeeRuleOut])
async def list_fees(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                    user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(FeeRule).where(FeeRule.tenant_id == tid).order_by(FeeRule.priority))
    return res.scalars().all()


@router.post("/fees", response_model=FeeRuleOut)
async def create_fee(body: FeeRuleCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("fee.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    rule = FeeRule(tenant_id=tid, created_by=str(user.id), **body.model_dump())
    db.add(rule)
    await db.flush()
    await record_audit(db, action="fee.create", resource_type="fee_rule", resource_id=rule.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes=body.model_dump())
    await db.commit()
    await db.refresh(rule)
    return rule
