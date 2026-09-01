"""Focused negative/security tests for the authentication gap completion.

Covers: expired tokens, revoked sessions, token_version revoke-all, MFA enforcement
(no bypass), failed-login lockout, cross-tenant access, and password-reset token
expiry + reuse prevention. Reset-token cases seed hashed tokens directly in the DB
(the raw token is never exposed by the API).
"""
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import psycopg2
import pyotp
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
JWT_SECRET = os.environ["JWT_SECRET"]
DB_SYNC = os.environ["DATABASE_URL_SYNC"].replace("postgresql+psycopg2://", "postgresql://")


def _client():
    return httpx.Client(base_url=BASE, timeout=30)


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def _register(c, email=None):
    email = email or f"sec_{uuid.uuid4().hex[:10]}@test.com"
    r = c.post("/api/auth/register", json={"email": email, "password": "Password123", "name": "Sec"})
    assert r.status_code == 200, r.text
    return email, _cookie(r, "access_token")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_expired_access_token_rejected():
    with _client() as c:
        _, token = _register(c)
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        expired = jwt.encode({**payload, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                             JWT_SECRET, algorithm="HS256")
        r = c.get("/api/auth/me", headers=_bearer(expired))
        assert r.status_code == 401


def test_revoked_session_rejected():
    with _client() as c:
        _, token = _register(c)
        sessions = c.get("/api/auth/sessions", headers=_bearer(token)).json()
        assert len(sessions) >= 1
        sid = sessions[0]["id"]
        assert c.delete(f"/api/auth/sessions/{sid}", headers=_bearer(token)).status_code == 200
        # Token whose session was revoked must no longer authenticate.
        assert c.get("/api/auth/me", headers=_bearer(token)).status_code == 401


def test_revoke_all_invalidates_token():
    with _client() as c:
        _, token = _register(c)
        assert c.post("/api/auth/sessions/revoke-all", headers=_bearer(token)).status_code == 200
        assert c.get("/api/auth/me", headers=_bearer(token)).status_code == 401


def test_failed_login_lockout():
    with _client() as c:
        email, _ = _register(c)
        for _ in range(5):
            c.post("/api/auth/login", json={"email": email, "password": "wrong-pass"})
        r = c.post("/api/auth/login", json={"email": email, "password": "wrong-pass"})
        assert r.status_code == 429


def test_cross_tenant_access_denied():
    with _client() as c:
        _, token = _register(c)
        # Fetch a real tenant id as this non-privileged user is not allowed to touch it.
        admin = _client()
        ar = admin.post("/api/auth/login", json={"email": os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io"),
                                                 "password": os.environ["ADMIN_PASSWORD"]})
        atoken = _cookie(ar, "access_token")
        tenants = admin.get("/api/tenants", headers=_bearer(atoken)).json()
        acme = next(t["id"] for t in tenants if t["slug"] == "acme")
        admin.close()
        r = c.get(f"/api/payments?tenant_id={acme}", headers=_bearer(token))
        assert r.status_code == 403


def test_mfa_enforced_no_bypass():
    with _client() as c:
        email, token = _register(c)
        setup = c.post("/api/auth/mfa/setup", headers=_bearer(token)).json()
        secret = setup["secret"]
        code = pyotp.TOTP(secret).now()
        assert c.post("/api/auth/mfa/enable", headers=_bearer(token), json={"code": code}).status_code == 200
        # Login now must NOT issue a session; it returns an mfa challenge instead.
        r = c.post("/api/auth/login", json={"email": email, "password": "Password123"})
        assert r.status_code == 200
        assert r.json().get("mfa_required") is True
        assert _cookie(r, "access_token") is None
        mfa_token = r.json()["mfa_token"]
        # The mfa_token cannot be used as an access token (bypass attempt).
        assert c.get("/api/auth/me", headers=_bearer(mfa_token)).status_code == 401
        # Wrong code is rejected.
        assert c.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"}).status_code == 401
        # Correct code completes login.
        good = c.post("/api/auth/mfa/verify", json={"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})
        assert good.status_code == 200
        assert _cookie(good, "access_token") is not None


def test_privileged_user_flagged_for_mfa():
    """Admin (privileged) is flagged as requiring MFA enrollment."""
    with _client() as c:
        r = c.post("/api/auth/login", json={"email": os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io"),
                                            "password": os.environ["ADMIN_PASSWORD"]})
        assert r.status_code == 200
        assert r.json().get("mfa_enrollment_required") is True


def _insert_reset_token(user_id, raw, expires_at):
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    conn = psycopg2.connect(DB_SYNC)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO security_tokens (id, user_id, purpose, token_hash, expires_at, created_at, updated_at) "
                "VALUES (%s,%s,'password_reset',%s,%s,now(),now())",
                (str(uuid.uuid4()), user_id, token_hash, expires_at),
            )
        conn.commit()
    finally:
        conn.close()


def _user_id_by_email(email):
    conn = psycopg2.connect(DB_SYNC)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
            return str(cur.fetchone()[0])
    finally:
        conn.close()


def test_reset_token_reuse_prevented():
    with _client() as c:
        email, _ = _register(c)
        uid = _user_id_by_email(email)
        raw = f"raw-{uuid.uuid4().hex}"
        _insert_reset_token(uid, raw, datetime.now(timezone.utc) + timedelta(minutes=30))
        r1 = c.post("/api/auth/reset-password", json={"token": raw, "new_password": "NewPassw0rd1"})
        assert r1.status_code == 200, r1.text
        r2 = c.post("/api/auth/reset-password", json={"token": raw, "new_password": "AnotherPass9"})
        assert r2.status_code == 400  # already used


def test_reset_token_expiry_enforced():
    with _client() as c:
        email, _ = _register(c)
        uid = _user_id_by_email(email)
        raw = f"raw-{uuid.uuid4().hex}"
        _insert_reset_token(uid, raw, datetime.now(timezone.utc) - timedelta(minutes=1))
        r = c.post("/api/auth/reset-password", json={"token": raw, "new_password": "NewPassw0rd1"})
        assert r.status_code == 400  # expired
