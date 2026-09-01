"""Per-tenant API key management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_feature, require_permission, resolve_tenant_id
from app.core.security import generate_api_key
from app.models.commerce import ApiKey
from app.schemas import ApiKeyCreate, ApiKeyOut

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                    user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    res = await db.execute(select(ApiKey).where(ApiKey.tenant_id == tid).order_by(ApiKey.created_at.desc()))
    return res.scalars().all()


@router.post("")
async def create_key(body: ApiKeyCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("apikey.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    if tid is None:
        raise HTTPException(status_code=400, detail="tenant_id required")
    await require_feature(db, tid, "api_keys", bypass=user.is_superadmin)
    plaintext, key_hash, last4 = generate_api_key("sk_test")
    key = ApiKey(tenant_id=tid, label=body.label, key_prefix="sk_test", key_hash=key_hash,
                 last4=last4, created_by=str(user.id))
    db.add(key)
    await db.flush()
    await record_audit(db, action="apikey.create", resource_type="api_key", resource_id=key.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes={"label": body.label})
    await db.commit()
    await db.refresh(key)
    # Secret returned exactly once.
    return {"id": str(key.id), "label": key.label, "secret": plaintext, "last4": key.last4}


@router.delete("/{key_id}")
async def revoke_key(key_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                     user=Depends(require_permission("apikey.manage"))):
    key = await db.get(ApiKey, key_id)
    if not key or (not user.is_superadmin and key.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="API key not found")
    key.active = False
    await record_audit(db, action="apikey.revoke", resource_type="api_key", resource_id=key.id,
                       tenant_id=key.tenant_id, actor_id=str(user.id), actor_email=user.email)
    await db.commit()
    return {"ok": True}
