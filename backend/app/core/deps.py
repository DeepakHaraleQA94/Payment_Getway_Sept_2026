"""Auth, tenant-context and RBAC dependencies."""
import uuid
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models.iam import AuthSession, User


async def _user_from_jwt(token: str, db: AsyncSession) -> User | None:
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        uid = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        return None
    user = await db.get(User, uid)
    if user is None:
        return None
    # Global revocation via token_version.
    if payload.get("tv") is not None and payload.get("tv") != user.token_version:
        return None
    # Per-session revocation: sid must map to a live, non-revoked AuthSession.
    sid = payload.get("sid")
    if sid:
        res = await db.execute(select(AuthSession).where(AuthSession.session_token == sid))
        sess = res.scalar_one_or_none()
        if sess is None or sess.revoked_at is not None:
            return None
        exp = sess.expires_at.replace(tzinfo=timezone.utc) if sess.expires_at.tzinfo is None else sess.expires_at
        if exp < datetime.now(timezone.utc):
            return None
    return user


async def _user_from_session(token: str, db: AsyncSession) -> User | None:
    res = await db.execute(select(AuthSession).where(AuthSession.session_token == token))
    session = res.scalar_one_or_none()
    if not session:
        return None
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    return await db.get(User, session.user_id)


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    # 1) JWT access token cookie, 2) Emergent session_token cookie, 3) Bearer header.
    access = request.cookies.get("access_token")
    session_token = request.cookies.get("session_token")

    user: User | None = None
    if access:
        user = await _user_from_jwt(access, db)
    if user is None and session_token:
        user = await _user_from_session(session_token, db)
    if user is None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:]
            user = await _user_from_jwt(bearer, db)
            if user is None:
                user = await _user_from_session(bearer, db)

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if user.status == "suspended":
        raise HTTPException(status_code=403, detail="Account suspended")
    return user


def permission_codes(user: User) -> set[str]:
    if user.is_superadmin:
        return {"*"}
    if user.role is None:
        return set()
    return {p.code for p in user.role.permissions}


def require_permission(code: str):
    async def _dep(user: User = Depends(get_current_user)) -> User:
        codes = permission_codes(user)
        if "*" in codes or code in codes:
            return user
        raise HTTPException(status_code=403, detail=f"Missing permission: {code}")

    return _dep


async def feature_enabled(db, tenant_id, key: str) -> bool:
    """Feature entitlement check. Explicitly-disabled flags block the operation;
    absence of a flag defaults to allowed (so tenants without the flag are unaffected)."""
    from app.models.feature import FeatureFlag
    res = await db.execute(
        select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id, FeatureFlag.key == key)
    )
    flag = res.scalar_one_or_none()
    if flag is None:
        return True
    return bool(flag.enabled)


async def require_feature(db, tenant_id, key: str, *, bypass: bool = False) -> None:
    # Super Admin (bypass=True) is never blocked by a tenant's feature restriction.
    if bypass:
        return
    if not await feature_enabled(db, tenant_id, key):
        raise HTTPException(status_code=403, detail=f"Feature '{key}' is disabled for this tenant")


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """Level-1 guard: only platform Super Admins may access the /superadmin control plane."""
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return user


def resolve_tenant_id(user: User, requested_tenant_id: str | None) -> uuid.UUID | None:
    """Tenant isolation: non-superadmins are locked to their own tenant."""
    if user.is_superadmin:
        if requested_tenant_id:
            try:
                return uuid.UUID(requested_tenant_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid tenant_id")
        return user.tenant_id
    if requested_tenant_id and str(user.tenant_id) != requested_tenant_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied")
    return user.tenant_id



async def get_tenant_from_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate a public API request via the X-API-Key header. Returns the Tenant."""
    from datetime import datetime, timezone

    from app.core.security import hash_api_key
    from app.models.commerce import ApiKey
    from app.models.tenant import Tenant

    key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key")
    res = await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(key), ApiKey.active.is_(True)))
    api_key = res.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    api_key.last_used_at = datetime.now(timezone.utc)
    tenant = await db.get(Tenant, api_key.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=403, detail="Tenant inactive")
    await db.commit()
    return tenant
