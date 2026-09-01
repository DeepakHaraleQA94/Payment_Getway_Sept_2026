"""Tenant / client management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission
from app.models.tenant import Tenant
from app.schemas import TenantCreate, TenantOut, TenantUpdate

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


@router.get("", response_model=list[TenantOut])
async def list_tenants(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    q = select(Tenant).order_by(Tenant.created_at.desc())
    if not user.is_superadmin:
        q = q.where(Tenant.id == user.tenant_id)
    res = await db.execute(q)
    return res.scalars().all()


@router.post("", response_model=TenantOut)
async def create_tenant(body: TenantCreate, db: AsyncSession = Depends(get_db),
                        user=Depends(require_permission("tenant.manage"))):
    exists = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Slug already exists")
    tenant = Tenant(**body.model_dump(), created_by=str(user.id))
    db.add(tenant)
    await db.flush()
    await record_audit(db, action="tenant.create", resource_type="tenant", resource_id=tenant.id,
                       tenant_id=tenant.id, actor_id=str(user.id), actor_email=user.email,
                       changes=body.model_dump())
    await db.commit()
    await db.refresh(tenant)
    return tenant


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(tenant_id: uuid.UUID, body: TenantUpdate, db: AsyncSession = Depends(get_db),
                        user=Depends(require_permission("tenant.manage"))):
    tenant = await db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not user.is_superadmin and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(tenant, k, v)
    tenant.updated_by = str(user.id)
    await record_audit(db, action="tenant.update", resource_type="tenant", resource_id=tenant.id,
                       tenant_id=tenant.id, actor_id=str(user.id), actor_email=user.email, changes=changes)
    await db.commit()
    await db.refresh(tenant)
    return tenant
