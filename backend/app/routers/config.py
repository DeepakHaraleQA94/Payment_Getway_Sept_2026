"""Feature flags, providers, fee rules management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.models.feature import FeatureFlag
from app.models.finance import FeeRule
from app.models.payment import PaymentProvider
from app.providers.registry import list_providers
from app.schemas import (
    FeatureFlagCreate,
    FeatureFlagOut,
    FeatureFlagUpdate,
    FeeRuleCreate,
    FeeRuleOut,
    ProviderCreate,
    ProviderOut,
)

router = APIRouter(prefix="/api", tags=["config"])


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
