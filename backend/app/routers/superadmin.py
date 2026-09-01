"""Super Admin control plane (/api/superadmin/*) — Level-1 platform administration.

Every endpoint is guarded by require_superadmin. Platform Admins (is_superadmin=False) and tenant
users can never reach these APIs. Guardrails prevent privilege escalation: this plane never sets
is_superadmin, never lets an admin be granted the wildcard, and refuses to modify a Super Admin.
Extends the existing IAM (User/Role/Permission/FeatureFlag) — no parallel auth system.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import permission_codes, require_superadmin
from app.core.security import hash_password
from app.models.feature import FeatureFlag
from app.models.iam import Permission, Role, User
from app.models.payment import Payment, PaymentProvider
from app.models.tenant import Tenant
from app.schemas import (
    PlatformAdminCreate,
    PlatformAdminOut,
    PlatformAdminUpdate,
    SetPasswordBody,
    TenantFeatureSet,
)

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])


async def _platform_tenant(db: AsyncSession) -> Tenant:
    res = await db.execute(select(Tenant).where(Tenant.is_platform.is_(True)))
    platform = res.scalars().first()
    if platform is None:
        raise HTTPException(status_code=500, detail="Platform tenant missing")
    return platform


def _admin_out(u: User) -> PlatformAdminOut:
    return PlatformAdminOut(
        id=u.id, email=u.email, name=u.name, status=u.status, is_superadmin=u.is_superadmin,
        role_id=u.role_id, role_name=(u.role.name if u.role else None),
        permissions=sorted(permission_codes(u)) if not u.is_superadmin else ["*"],
        last_login_at=u.last_login_at, created_at=u.created_at)


async def _valid_permission_codes(db: AsyncSession, codes: list[str]) -> list[Permission]:
    if not codes:
        return []
    res = await db.execute(select(Permission).where(Permission.code.in_(codes)))
    return list(res.scalars().all())


async def _apply_permissions(db: AsyncSession, target: User, codes: list[str]) -> None:
    """Grant EXACTLY the given permissions via a dedicated platform role (no wildcard)."""
    role_name = f"admin::{target.email}"
    res = await db.execute(select(Role).where(Role.name == role_name, Role.tenant_id.is_(None)))
    role = res.scalar_one_or_none()
    perms = await _valid_permission_codes(db, codes)
    if role is None:
        role = Role(name=role_name, description=f"Granted permissions for {target.email}",
                    is_system=False, tenant_id=None, permissions=perms)
        db.add(role)
        await db.flush()
    else:
        role.permissions = perms
    target.role_id = role.id


# ----------------------------- overview -----------------------------
@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db), _sa: User = Depends(require_superadmin)):
    tenants = (await db.execute(select(func.count()).select_from(Tenant)
                                .where(Tenant.is_platform.is_(False)))).scalar() or 0
    platform = await _platform_tenant(db)
    admins = (await db.execute(select(func.count()).select_from(User)
                               .where(User.tenant_id == platform.id, User.is_superadmin.is_(False)))).scalar() or 0
    superadmins = (await db.execute(select(func.count()).select_from(User)
                                    .where(User.is_superadmin.is_(True)))).scalar() or 0
    providers = (await db.execute(select(func.count()).select_from(PaymentProvider))).scalar() or 0
    payments = (await db.execute(select(func.count()).select_from(Payment))).scalar() or 0
    return {"tenants": tenants, "platform_admins": admins, "super_admins": superadmins,
            "provider_accounts": providers, "payments": payments}


# ----------------------------- platform admins -----------------------------
@router.get("/admins", response_model=list[PlatformAdminOut])
async def list_admins(db: AsyncSession = Depends(get_db), _sa: User = Depends(require_superadmin)):
    platform = await _platform_tenant(db)
    res = await db.execute(select(User).where(User.tenant_id == platform.id)
                           .order_by(User.is_superadmin.desc(), User.created_at.desc()))
    return [_admin_out(u) for u in res.scalars().all()]


@router.post("/admins", response_model=PlatformAdminOut)
async def create_admin(body: PlatformAdminCreate, db: AsyncSession = Depends(get_db),
                       sa: User = Depends(require_superadmin)):
    platform = await _platform_tenant(db)
    email = body.email.lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")
    # Platform Admin: on the platform tenant, NEVER a super admin, only granted permissions.
    admin = User(email=email, name=body.name or email.split("@")[0],
                 password_hash=hash_password(body.password), tenant_id=platform.id,
                 is_superadmin=False, auth_provider="password", status="active",
                 email_verified=True, created_by=str(sa.id))
    if body.role_id is not None:
        admin.role_id = body.role_id
    db.add(admin)
    await db.flush()
    if body.permission_codes is not None:
        await _apply_permissions(db, admin, body.permission_codes)
    await record_audit(db, action="superadmin.admin_create", resource_type="user", resource_id=admin.id,
                       tenant_id=platform.id, actor_id=str(sa.id), actor_email=sa.email,
                       changes={"email": email, "permissions": body.permission_codes})
    await db.commit()
    await db.refresh(admin)
    return _admin_out(admin)


async def _get_managed_admin(db: AsyncSession, admin_id: uuid.UUID) -> User:
    target = await db.get(User, admin_id)
    platform = await _platform_tenant(db)
    if target is None or target.tenant_id != platform.id:
        raise HTTPException(status_code=404, detail="Platform admin not found")
    if target.is_superadmin:
        raise HTTPException(status_code=403, detail="Super Admin accounts cannot be modified here")
    return target


@router.patch("/admins/{admin_id}", response_model=PlatformAdminOut)
async def update_admin(admin_id: uuid.UUID, body: PlatformAdminUpdate, db: AsyncSession = Depends(get_db),
                       sa: User = Depends(require_superadmin)):
    target = await _get_managed_admin(db, admin_id)
    if body.name is not None:
        target.name = body.name
    if body.status is not None:
        if body.status not in ("active", "suspended"):
            raise HTTPException(status_code=400, detail="status must be active or suspended")
        target.status = body.status
        if body.status == "suspended":
            target.token_version += 1  # revoke existing sessions immediately
    if body.role_id is not None:
        target.role_id = body.role_id
    if body.permission_codes is not None:
        await _apply_permissions(db, target, body.permission_codes)
    await record_audit(db, action="superadmin.admin_update", resource_type="user", resource_id=target.id,
                       actor_id=str(sa.id), actor_email=sa.email,
                       changes=body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(target)
    return _admin_out(target)


@router.post("/admins/{admin_id}/set-password")
async def set_admin_password(admin_id: uuid.UUID, body: SetPasswordBody, db: AsyncSession = Depends(get_db),
                             sa: User = Depends(require_superadmin)):
    target = await _get_managed_admin(db, admin_id)
    target.password_hash = hash_password(body.password)
    target.token_version += 1  # force re-login everywhere with the new password
    await record_audit(db, action="superadmin.admin_set_password", resource_type="user",
                       resource_id=target.id, actor_id=str(sa.id), actor_email=sa.email,
                       changes={"password": "updated"})
    await db.commit()
    return {"status": "password_updated", "admin_id": str(target.id)}


# ----------------------------- tenant feature control -----------------------------
@router.get("/features")
async def tenant_features(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          _sa: User = Depends(require_superadmin)):
    res = await db.execute(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id)
                           .order_by(FeatureFlag.key))
    flags = {f.key: f for f in res.scalars().all()}
    # Surface the core customer-facing features even if no flag row exists yet (default enabled).
    core = {"refunds": "Refunds", "checkout": "Checkout", "reports": "Reports",
            "webhooks": "Webhooks", "api_keys": "API Keys", "providers": "Providers"}
    out = []
    for key, name in core.items():
        f = flags.get(key)
        out.append({"key": key, "name": f.name if f else name,
                    "enabled": f.enabled if f else True,
                    "description": f.description if f else None,
                    "configured": f is not None})
    # Include any extra tenant-specific flags too.
    for key, f in flags.items():
        if key not in core:
            out.append({"key": key, "name": f.name, "enabled": f.enabled,
                        "description": f.description, "configured": True})
    return out


@router.put("/features")
async def set_tenant_feature(body: TenantFeatureSet, db: AsyncSession = Depends(get_db),
                             sa: User = Depends(require_superadmin)):
    res = await db.execute(select(FeatureFlag).where(FeatureFlag.tenant_id == body.tenant_id,
                                                     FeatureFlag.key == body.key))
    flag = res.scalar_one_or_none()
    if flag is None:
        flag = FeatureFlag(tenant_id=body.tenant_id, key=body.key,
                           name=body.name or body.key.replace("_", " ").title(),
                           enabled=body.enabled, description=body.description, created_by=str(sa.id))
        db.add(flag)
    else:
        flag.enabled = body.enabled
        if body.name is not None:
            flag.name = body.name
        if body.description is not None:
            flag.description = body.description
        flag.updated_by = str(sa.id)
    await db.flush()
    await record_audit(db, action="superadmin.feature_set", resource_type="feature_flag",
                       resource_id=flag.id, tenant_id=body.tenant_id, actor_id=str(sa.id),
                       actor_email=sa.email, changes={"key": body.key, "enabled": body.enabled})
    await db.commit()
    await db.refresh(flag)
    return {"key": flag.key, "name": flag.name, "enabled": flag.enabled,
            "description": flag.description, "configured": True}
