"""Users, roles and permissions management."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.database import get_db
from app.core.deps import get_current_user, require_permission, resolve_tenant_id
from app.core.security import hash_password
from app.models.iam import Permission, Role, User
from app.schemas import PermissionOut, RoleCreate, RoleOut, UserCreate, UserOut

router = APIRouter(prefix="/api", tags=["iam"])


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    res = await db.execute(select(Permission).order_by(Permission.module, Permission.code))
    return res.scalars().all()


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    q = select(Role).order_by(Role.created_at.desc())
    if not user.is_superadmin:
        q = q.where((Role.tenant_id == user.tenant_id) | (Role.is_system.is_(True)))
    res = await db.execute(q)
    return res.scalars().all()


@router.post("/roles", response_model=RoleOut)
async def create_role(body: RoleCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                      user=Depends(require_permission("role.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    role = Role(name=body.name, description=body.description, tenant_id=tid, created_by=str(user.id))
    if body.permission_codes:
        res = await db.execute(select(Permission).where(Permission.code.in_(body.permission_codes)))
        role.permissions = list(res.scalars().all())
    db.add(role)
    await db.flush()
    await record_audit(db, action="role.create", resource_type="role", resource_id=role.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email,
                       changes={"name": body.name, "permissions": body.permission_codes})
    await db.commit()
    await db.refresh(role)
    return role


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    q = select(User).order_by(User.created_at.desc())
    if not user.is_superadmin:
        q = q.where(User.tenant_id == user.tenant_id)
    res = await db.execute(q)
    return res.scalars().all()


@router.post("/users", response_model=UserOut)
async def create_user(body: UserCreate, tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                      user=Depends(require_permission("user.manage"))):
    tid = resolve_tenant_id(user, tenant_id)
    email = body.email.lower()
    exists = await db.execute(select(User).where(User.email == email))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")
    new_user = User(email=email, name=body.name or email.split("@")[0],
                    password_hash=hash_password(body.password), tenant_id=tid,
                    role_id=body.role_id, auth_provider="password", created_by=str(user.id))
    db.add(new_user)
    await db.flush()
    await record_audit(db, action="user.create", resource_type="user", resource_id=new_user.id,
                       tenant_id=tid, actor_id=str(user.id), actor_email=user.email, changes={"email": email})
    await db.commit()
    await db.refresh(new_user)
    return new_user
