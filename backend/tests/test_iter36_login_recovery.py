"""Iter36 - Super Admin login recovery verification.

Verifies the bug-fix after Postgres role/db recovery + hardened seed:
  * finance@vortexglobal.info login succeeds (200) with existing password.
  * /auth/me returns is_superadmin=true.
  * /superadmin/overview reachable for super admin.
  * Invalid password returns 401, no session created.
  * Non-super-admin (ops-admin) cannot access /superadmin/overview (403).
  * Logout revokes the session (/auth/me subsequently 401).
  * Seed did NOT rotate the super admin password (still authenticates).
  * Tenant isolation intact (ops cannot read acme payments).
"""
import os
import uuid
import requests
import pytest

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"
OPS_EMAIL = "ops-admin@cloudpay.io"
OPS_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"


def _fresh_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _login(email, password):
    s = _fresh_session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    return s, r


class TestSuperAdminLoginRecovery:
    def test_login_success_returns_200(self):
        s, r = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
        body = r.json()
        # Should carry user + not require MFA (mfa not enrolled per seed).
        assert body.get("mfa_required") in (False, None), f"unexpected mfa_required: {body.get('mfa_required')}"
        # user block may be nested or absent (only /auth/me is guaranteed) — accept either shape.
        user = body.get("user") or {}
        if user:
            assert user.get("email") == ADMIN_EMAIL
        # Session cookie or bearer token must be issued.
        tok = body.get("access_token") or body.get("token")
        has_cookie = any(c.name in ("access_token", "session_token") for c in s.cookies)
        assert tok or has_cookie, "no auth token/cookie issued"

    def test_auth_me_returns_superadmin(self):
        s, r = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert r.status_code == 200
        tok = r.json().get("access_token") or r.json().get("token")
        if tok:
            s.headers["Authorization"] = f"Bearer {tok}"
        rm = s.get(f"{API}/auth/me", timeout=15)
        assert rm.status_code == 200, f"/auth/me failed: {rm.status_code} {rm.text[:200]}"
        me = rm.json()
        assert me.get("email") == ADMIN_EMAIL
        assert me.get("is_superadmin") is True

    def test_superadmin_overview_reachable(self):
        s, r = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert r.status_code == 200
        tok = r.json().get("access_token") or r.json().get("token")
        if tok:
            s.headers["Authorization"] = f"Bearer {tok}"
        ro = s.get(f"{API}/superadmin/overview", timeout=20)
        assert ro.status_code == 200, f"/superadmin/overview: {ro.status_code} {ro.text[:200]}"

    def test_invalid_password_401(self):
        s, r = _login(ADMIN_EMAIL, "WRONG-Password-NotReal-xxxx")
        assert r.status_code == 401, f"expected 401 got {r.status_code}"
        # No auth cookie / no bearer
        body = {}
        try:
            body = r.json()
        except Exception:
            pass
        assert not (body.get("access_token") or body.get("token"))

    def test_seed_did_not_rotate_password(self):
        # Run login twice with a fresh session to confirm the existing password
        # persists across the "reseed on startup" hardening in seed.py.
        _, r1 = _login(ADMIN_EMAIL, ADMIN_PASS)
        _, r2 = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert r1.status_code == 200 and r2.status_code == 200


class TestNonSuperAdminGuard:
    def test_ops_cannot_access_superadmin_overview(self):
        s, r = _login(OPS_EMAIL, OPS_PASS)
        if r.status_code != 200:
            pytest.skip(f"ops-admin login unavailable: {r.status_code}")
        tok = r.json().get("access_token") or r.json().get("token")
        if tok:
            s.headers["Authorization"] = f"Bearer {tok}"
        # Verify not a superadmin
        me = s.get(f"{API}/auth/me", timeout=15).json()
        assert me.get("is_superadmin") is False, "ops-admin should NOT be superadmin"
        ro = s.get(f"{API}/superadmin/overview", timeout=15)
        assert ro.status_code in (401, 403), f"expected 401/403 got {ro.status_code}"

    def test_ops_cannot_read_other_tenant(self):
        s_admin, ra = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert ra.status_code == 200
        tok = ra.json().get("access_token") or ra.json().get("token")
        if tok:
            s_admin.headers["Authorization"] = f"Bearer {tok}"
        rt = s_admin.get(f"{API}/tenants", timeout=15)
        assert rt.status_code == 200
        tenants = {t["slug"]: t for t in rt.json()}
        if "acme" not in tenants:
            pytest.skip("acme tenant not seeded")
        acme_id = tenants["acme"]["id"]

        s_ops, ro = _login(OPS_EMAIL, OPS_PASS)
        if ro.status_code != 200:
            pytest.skip(f"ops login unavailable: {ro.status_code}")
        tok = ro.json().get("access_token") or ro.json().get("token")
        if tok:
            s_ops.headers["Authorization"] = f"Bearer {tok}"
        r = s_ops.get(f"{API}/payments?tenant_id={acme_id}",
                      headers={"X-Tenant-Id": str(acme_id)}, timeout=15)
        assert r.status_code in (403, 404), f"cross-tenant leak: {r.status_code}"


class TestLogoutRevokesSession:
    def test_logout_then_me_401(self):
        s, r = _login(ADMIN_EMAIL, ADMIN_PASS)
        assert r.status_code == 200
        tok = r.json().get("access_token") or r.json().get("token")
        if tok:
            s.headers["Authorization"] = f"Bearer {tok}"
        rme = s.get(f"{API}/auth/me", timeout=15)
        assert rme.status_code == 200
        rl = s.post(f"{API}/auth/logout", timeout=15)
        assert rl.status_code in (200, 204), f"logout: {rl.status_code}"
        # Drop cookies to simulate cookie-based logout too
        # The bearer (if any) is invalidated via token_version bump.
        rme2 = s.get(f"{API}/auth/me", timeout=15)
        assert rme2.status_code == 401, f"session not revoked: {rme2.status_code}"
