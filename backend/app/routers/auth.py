"""Authentication: JWT email/password + Emergent Google session auth.

Additive security features: password reset/change, email verification, TOTP MFA
for privileged users, session listing/revocation, login history and auth notifications.
Google authentication is preserved unchanged.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, permission_codes
from app.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
    generate_mfa_secret,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    mfa_provisioning_uri,
    verify_password,
    verify_totp,
)
from app.models.iam import AuthSession, LoginAttempt, LoginHistory, SecurityToken, User
from app.schemas import LoginRequest, RegisterRequest, UserOut
from app.services import notification_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
RESET_TOKEN_TTL_MIN = 60
VERIFY_TOKEN_TTL_HOURS = 24


# ---------- helpers ----------
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def is_privileged(user: User) -> bool:
    return bool(user.is_superadmin)


async def _start_session(db: AsyncSession, response: Response, user: User, request: Request) -> None:
    """Create a revocable JWT session and set signed cookies embedding sid + token_version."""
    sid = uuid.uuid4().hex
    sess = AuthSession(
        user_id=user.id, session_token=sid, kind="jwt",
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
        user_agent=request.headers.get("user-agent", "")[:300], ip_address=_client_ip(request),
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(sess)
    await db.flush()
    access = create_access_token(str(user.id), user.email,
                                 str(user.tenant_id) if user.tenant_id else None, sid=sid, tv=user.token_version)
    refresh = create_refresh_token(str(user.id), sid=sid, tv=user.token_version)
    response.set_cookie("access_token", access, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=settings.access_token_minutes * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=settings.refresh_token_days * 86400, path="/")


async def _log_login(db: AsyncSession, *, email: str, user: User | None, success: bool,
                     reason: str | None, request: Request) -> None:
    db.add(LoginHistory(user_id=user.id if user else None, email=email,
                        tenant_id=user.tenant_id if user else None, success=success, reason=reason,
                        ip_address=_client_ip(request), user_agent=request.headers.get("user-agent", "")[:300]))


def _notify(user: User, event: str, extra: dict | None = None) -> None:
    notification_service.notify(tenant_id=user.tenant_id, event=event,
                                payload={"user_id": str(user.id), "email": user.email, **(extra or {})})


# ---------- register / login ----------
@router.post("/register", response_model=UserOut)
async def register(body: RegisterRequest, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, name=body.name or email.split("@")[0],
                password_hash=hash_password(body.password), auth_provider="password", status="active")
    db.add(user)
    await db.flush()
    await record_audit(db, action="auth.register", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=email)
    await _start_session(db, response, user, request)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    identifier = f"{_client_ip(request)}:{email}"
    res = await db.execute(select(LoginAttempt).where(LoginAttempt.identifier == identifier))
    attempt = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if attempt and attempt.locked_until:
        locked = attempt.locked_until.replace(tzinfo=timezone.utc) if attempt.locked_until.tzinfo is None else attempt.locked_until
        if locked > now:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        if attempt is None:
            db.add(LoginAttempt(identifier=identifier, attempts=1))
        else:
            attempt.attempts += 1
            if attempt.attempts >= MAX_ATTEMPTS:
                attempt.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        await _log_login(db, email=email, user=user, success=False, reason="bad_credentials", request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.status == "suspended":
        await _log_login(db, email=email, user=user, success=False, reason="suspended", request=request)
        await db.commit()
        raise HTTPException(status_code=403, detail="Account suspended")

    # MFA challenge: never issue session cookies until the TOTP step is completed.
    if user.mfa_enabled:
        if attempt:
            await db.delete(attempt)
        await _log_login(db, email=email, user=user, success=False, reason="mfa_required", request=request)
        await db.commit()
        return {"mfa_required": True, "mfa_token": create_mfa_token(str(user.id))}

    if attempt:
        await db.delete(attempt)
    user.last_login_at = now
    await _start_session(db, response, user, request)
    await _log_login(db, email=email, user=user, success=True, reason="password", request=request)
    _notify(user, "auth.login")
    await db.commit()
    await db.refresh(user)
    out = UserOut.model_validate(user).model_dump(mode="json")
    out["mfa_enrollment_required"] = is_privileged(user) and not user.mfa_enabled
    return out


@router.post("/mfa/verify")
async def mfa_verify(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    mfa_token, code = body.get("mfa_token"), body.get("code")
    if not mfa_token or not code:
        raise HTTPException(status_code=400, detail="mfa_token and code are required")
    try:
        payload = decode_token(mfa_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token")
    if payload.get("type") != "mfa":
        raise HTTPException(status_code=401, detail="Invalid MFA token")
    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.mfa_enabled:
        raise HTTPException(status_code=401, detail="MFA not available")
    if not verify_totp(user.mfa_secret, code):
        await _log_login(db, email=user.email, user=user, success=False, reason="mfa_failed", request=request)
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    user.last_login_at = datetime.now(timezone.utc)
    await _start_session(db, response, user, request)
    await _log_login(db, email=user.email, user=user, success=True, reason="mfa", request=request)
    _notify(user, "auth.login")
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user).model_dump(mode="json")


# ---------- Google auth (preserved) ----------
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
                    auth_provider="google", status="active", email_verified=True)
        db.add(user)
        await db.flush()
    else:
        user.picture = data.get("picture") or user.picture
        user.email_verified = True
        if user.auth_provider == "password":
            user.auth_provider = "google"

    session_token = data["session_token"]
    sess = AuthSession(
        user_id=user.id, session_token=session_token, kind="google",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        user_agent=request.headers.get("user-agent", "")[:300], ip_address=_client_ip(request),
        last_seen_at=datetime.now(timezone.utc),
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
    user = await db.get(User, uuid.UUID(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if payload.get("tv") is not None and payload.get("tv") != user.token_version:
        raise HTTPException(status_code=401, detail="Session revoked")
    sid = payload.get("sid")
    if sid:
        res = await db.execute(select(AuthSession).where(AuthSession.session_token == sid))
        sess = res.scalar_one_or_none()
        if sess is None or sess.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Session revoked")
    access = create_access_token(str(user.id), user.email,
                                 str(user.tenant_id) if user.tenant_id else None, sid=sid, tv=user.token_version)
    response.set_cookie("access_token", access, httponly=True, secure=settings.cookie_secure,
                        samesite="none", max_age=settings.access_token_minutes * 60, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    out = UserOut.model_validate(user).model_dump(mode="json")
    out["permissions"] = sorted(permission_codes(user))
    out["role_name"] = user.role.name if user.role else ("superadmin" if user.is_superadmin else None)
    out["mfa_enabled"] = user.mfa_enabled
    out["email_verified"] = user.email_verified
    out["mfa_enrollment_required"] = is_privileged(user) and not user.mfa_enabled
    return out


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    for cookie_name in ("access_token", "refresh_token"):
        tok = request.cookies.get(cookie_name)
        if tok:
            try:
                sid = decode_token(tok).get("sid")
            except Exception:
                sid = None
            if sid:
                res = await db.execute(select(AuthSession).where(AuthSession.session_token == sid))
                sess = res.scalar_one_or_none()
                if sess and sess.revoked_at is None:
                    sess.revoked_at = datetime.now(timezone.utc)
            break
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


# ---------- password reset / change ----------
class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordBody, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    # Do not reveal whether the account exists.
    if user and user.password_hash:
        raw, token_hash = generate_reset_token()
        db.add(SecurityToken(user_id=user.id, purpose="password_reset", token_hash=token_hash,
                             expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_TTL_MIN)))
        await record_audit(db, action="auth.forgot_password", resource_type="user", resource_id=user.id,
                           tenant_id=user.tenant_id, actor_email=email)
        _notify(user, "auth.password_reset_requested",
                {"reset_link": f"{settings.frontend_url}/reset-password?token={raw}"})
        await db.commit()
    return {"message": "If an account exists, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordBody, db: AsyncSession = Depends(get_db)):
    token_hash = hash_reset_token(body.token)
    res = await db.execute(select(SecurityToken).where(
        SecurityToken.token_hash == token_hash, SecurityToken.purpose == "password_reset"))
    tok = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if tok is None or tok.used_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or already-used reset token")
    exp = tok.expires_at.replace(tzinfo=timezone.utc) if tok.expires_at.tzinfo is None else tok.expires_at
    if exp < now:
        raise HTTPException(status_code=400, detail="Reset token has expired")
    user = await db.get(User, tok.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid token")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1  # revoke all existing sessions
    tok.used_at = now
    await record_audit(db, action="auth.reset_password", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=user.email)
    _notify(user, "auth.password_changed", {"via": "reset"})
    await db.commit()
    return {"message": "Password has been reset. Please sign in."}


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, response: Response, request: Request,
                          user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.password_hash or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    db_user = await db.get(User, user.id)
    db_user.password_hash = hash_password(body.new_password)
    db_user.token_version += 1  # revoke other sessions
    # Revoke existing jwt sessions, then start a fresh session for this client.
    res = await db.execute(select(AuthSession).where(AuthSession.user_id == db_user.id,
                                                     AuthSession.kind == "jwt", AuthSession.revoked_at.is_(None)))
    for s in res.scalars().all():
        s.revoked_at = datetime.now(timezone.utc)
    await _start_session(db, response, db_user, request)
    await record_audit(db, action="auth.change_password", resource_type="user", resource_id=db_user.id,
                       tenant_id=db_user.tenant_id, actor_email=db_user.email)
    _notify(db_user, "auth.password_changed", {"via": "change"})
    await db.commit()
    return {"message": "Password changed successfully."}


# ---------- email verification ----------
@router.post("/verify-email/request")
async def request_email_verification(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.email_verified:
        return {"message": "Email already verified."}
    db_user = await db.get(User, user.id)
    raw, token_hash = generate_reset_token()
    db.add(SecurityToken(user_id=db_user.id, purpose="email_verify", token_hash=token_hash,
                         expires_at=datetime.now(timezone.utc) + timedelta(hours=VERIFY_TOKEN_TTL_HOURS)))
    _notify(db_user, "auth.email_verification_requested",
            {"verify_link": f"{settings.frontend_url}/verify-email?token={raw}"})
    await db.commit()
    return {"message": "Verification link sent."}


class VerifyEmailBody(BaseModel):
    token: str


@router.post("/verify-email")
async def verify_email(body: VerifyEmailBody, db: AsyncSession = Depends(get_db)):
    token_hash = hash_reset_token(body.token)
    res = await db.execute(select(SecurityToken).where(
        SecurityToken.token_hash == token_hash, SecurityToken.purpose == "email_verify"))
    tok = res.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if tok is None or tok.used_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or already-used verification token")
    exp = tok.expires_at.replace(tzinfo=timezone.utc) if tok.expires_at.tzinfo is None else tok.expires_at
    if exp < now:
        raise HTTPException(status_code=400, detail="Verification token has expired")
    user = await db.get(User, tok.user_id)
    user.email_verified = True
    tok.used_at = now
    await record_audit(db, action="auth.verify_email", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=user.email)
    await db.commit()
    return {"message": "Email verified."}


# ---------- MFA enrollment ----------
class MfaCodeBody(BaseModel):
    code: str


@router.post("/mfa/setup")
async def mfa_setup(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA already enabled")
    db_user = await db.get(User, user.id)
    secret = generate_mfa_secret()
    db_user.mfa_secret = secret  # provisional until confirmed
    await db.commit()
    return {"secret": secret, "otpauth_uri": mfa_provisioning_uri(secret, db_user.email)}


@router.post("/mfa/enable")
async def mfa_enable(body: MfaCodeBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    db_user = await db.get(User, user.id)
    if db_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA already enabled")
    if not db_user.mfa_secret or not verify_totp(db_user.mfa_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    db_user.mfa_enabled = True
    await record_audit(db, action="auth.mfa_enabled", resource_type="user", resource_id=db_user.id,
                       tenant_id=db_user.tenant_id, actor_email=db_user.email)
    _notify(db_user, "auth.mfa_enabled")
    await db.commit()
    return {"message": "MFA enabled."}


@router.post("/mfa/disable")
async def mfa_disable(body: MfaCodeBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    db_user = await db.get(User, user.id)
    if not db_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    if not verify_totp(db_user.mfa_secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code")
    db_user.mfa_enabled = False
    db_user.mfa_secret = None
    await record_audit(db, action="auth.mfa_disabled", resource_type="user", resource_id=db_user.id,
                       tenant_id=db_user.tenant_id, actor_email=db_user.email)
    _notify(db_user, "auth.mfa_disabled")
    await db.commit()
    return {"message": "MFA disabled."}


# ---------- sessions & login history ----------
@router.get("/sessions")
async def list_sessions(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    current_sid = None
    tok = request.cookies.get("access_token")
    if tok:
        try:
            current_sid = decode_token(tok).get("sid")
        except Exception:
            current_sid = None
    res = await db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                           .order_by(AuthSession.created_at.desc()))
    return [{"id": str(s.id), "kind": s.kind, "ip_address": s.ip_address, "user_agent": s.user_agent,
             "current": s.session_token == current_sid,
             "expires_at": s.expires_at.isoformat() if s.expires_at else None,
             "created_at": s.created_at.isoformat()} for s in res.scalars().all()]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sess = await db.get(AuthSession, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.revoked_at = datetime.now(timezone.utc)
    await record_audit(db, action="auth.session_revoked", resource_type="auth_session", resource_id=sess.id,
                       tenant_id=user.tenant_id, actor_email=user.email)
    await db.commit()
    return {"ok": True}


@router.post("/sessions/revoke-all")
async def revoke_all_sessions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    db_user = await db.get(User, user.id)
    db_user.token_version += 1  # invalidates all JWTs
    res = await db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)))
    for s in res.scalars().all():
        s.revoked_at = datetime.now(timezone.utc)
    await record_audit(db, action="auth.revoke_all_sessions", resource_type="user", resource_id=user.id,
                       tenant_id=user.tenant_id, actor_email=user.email)
    _notify(db_user, "auth.sessions_revoked")
    await db.commit()
    return {"message": "All sessions revoked. Please sign in again."}


@router.get("/login-history")
async def login_history(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(LoginHistory).where(LoginHistory.user_id == user.id)
                           .order_by(LoginHistory.created_at.desc()).limit(50))
    return [{"id": str(h.id), "success": h.success, "reason": h.reason, "ip_address": h.ip_address,
             "user_agent": h.user_agent, "created_at": h.created_at.isoformat()} for h in res.scalars().all()]
