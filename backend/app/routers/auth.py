"""Authentication: JWT email/password + Emergent Google session auth."""
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, permission_codes
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.iam import AuthSession, LoginAttempt, User
from app.schemas import LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _set_jwt_cookies(response: Response, user: User) -> None:
    access = create_access_token(str(user.id), user.email, str(user.tenant_id) if user.tenant_id else None)
    refresh = create_refresh_token(str(user.id))
    response.set_cookie("access_token", access, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=settings.access_token_minutes * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=settings.refresh_token_days * 86400, path="/")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


async def _permissions_payload(user: User) -> list[str]:
    return sorted(permission_codes(user))


@router.post("/register", response_model=UserOut)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=email, name=body.name or email.split("@")[0], password_hash=hash_password(body.password),
        auth_provider="password", status="active",
    )
    db.add(user)
    await db.flush()
    await record_audit(db, action="auth.register", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=email)
    await db.commit()
    await db.refresh(user)
    _set_jwt_cookies(response, user)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    identifier = f"{_client_ip(request)}:{email}"
    res = await db.execute(select(LoginAttempt).where(LoginAttempt.identifier == identifier))
    attempt = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if attempt and attempt.locked_until:
        locked = attempt.locked_until
        if locked.tzinfo is None:
            locked = locked.replace(tzinfo=timezone.utc)
        if locked > now:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        if attempt is None:
            attempt = LoginAttempt(identifier=identifier, attempts=1)
            db.add(attempt)
        else:
            attempt.attempts += 1
            if attempt.attempts >= MAX_ATTEMPTS:
                attempt.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if attempt:
        await db.delete(attempt)
    user.last_login_at = now
    await db.commit()
    await db.refresh(user)
    _set_jwt_cookies(response, user)
    return user


@router.post("/session")
async def google_session(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Exchange an Emergent OAuth session_id for a persistent session_token."""
    session_id = request.headers.get("X-Session-ID")
    if not session_id:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(settings.emergent_auth_session_url, headers={"X-Session-ID": session_id})
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session")
    data = resp.json()

    email = data["email"].lower()
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None:
        user = User(email=email, name=data.get("name", ""), picture=data.get("picture"),
                    auth_provider="google", status="active")
        db.add(user)
        await db.flush()
    else:
        user.picture = data.get("picture") or user.picture
        if user.auth_provider == "password":
            user.auth_provider = "google"

    session_token = data["session_token"]
    sess = AuthSession(
        user_id=user.id, session_token=session_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        user_agent=request.headers.get("user-agent", "")[:300],
    )
    db.add(sess)
    await record_audit(db, action="auth.google_login", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=email)
    await db.commit()
    await db.refresh(user)

    response.set_cookie("session_token", session_token, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=7 * 86400, path="/")
    return {"user": UserOut.model_validate(user).model_dump(mode="json")}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    import uuid as _uuid
    user = await db.get(User, _uuid.UUID(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    _set_jwt_cookies(response, user)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    out = UserOut.model_validate(user).model_dump(mode="json")
    out["permissions"] = sorted(permission_codes(user))
    out["role_name"] = user.role.name if user.role else ("superadmin" if user.is_superadmin else None)
    return out


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if session_token:
        res = await db.execute(select(AuthSession).where(AuthSession.session_token == session_token))
        sess = res.scalar_one_or_none()
        if sess:
            await db.delete(sess)
            await db.commit()
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    response.delete_cookie("session_token", path="/")
    return {"ok": True}
