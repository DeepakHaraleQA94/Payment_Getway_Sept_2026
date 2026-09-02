"""
Iteration 18 — verify Super Admin login bug fix (backend/DB restored, seeded admin).
Focus: reproduce reported "Something went wrong" symptom is gone at backend layer,
plus security invariants (401 generic, no enumeration, no secret leaks, RBAC).
"""
import os
import re
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else "https://pay-gateway-core.preview.emergentagent.com"
SUPER_EMAIL = "finance@vortexglobal.info"
SUPER_PASSWORD = "CloudPay-DutqTuzcS1jL64hHJrCy"

BCRYPT_RE = re.compile(r"\$2[aby]\$")
SECRET_KEYS = ("password_hash", "hashed_password", "password", "reset_token", "access_token", "refresh_token", "jwt")


@pytest.fixture(scope="module")
def session():
    return requests.Session()


@pytest.fixture(scope="module")
def logged_in(session):
    r = session.post(f"{BASE_URL}/api/auth/login",
                     json={"email": SUPER_EMAIL, "password": SUPER_PASSWORD})
    assert r.status_code == 200, r.text
    return r


def _bearer_from_cookies(resp):
    tok = resp.cookies.get("access_token")
    assert tok, "access_token cookie missing"
    return {"Authorization": f"Bearer {tok}"}


# --- Login success -----------------------------------------------------------
def test_login_success_super_admin(logged_in):
    r = logged_in
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == SUPER_EMAIL
    assert data["is_superadmin"] is True
    assert r.cookies.get("access_token"), "access_token cookie should be set"
    assert r.cookies.get("refresh_token"), "refresh_token cookie should be set"


def test_login_response_body_has_no_secrets(logged_in):
    body = logged_in.text
    assert not BCRYPT_RE.search(body), "bcrypt hash leaked in login body"
    j = logged_in.json()
    for k in SECRET_KEYS:
        assert k not in j, f"key {k} should NOT be in login JSON body"
    assert SUPER_PASSWORD not in body


# --- /auth/me ----------------------------------------------------------------
def test_auth_me_returns_superadmin(logged_in):
    headers = _bearer_from_cookies(logged_in)
    r = requests.get(f"{BASE_URL}/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["email"] == SUPER_EMAIL
    assert me.get("is_superadmin") is True
    perms = me.get("permissions") or []
    assert "*" in perms, f"expected '*' permission, got {perms}"
    # No secret leakage
    for k in ("password_hash", "hashed_password", "password"):
        assert k not in me


# --- Wrong password -> 401 generic ------------------------------------------
def test_wrong_password_returns_401_generic():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": SUPER_EMAIL, "password": "definitely-wrong-Password!123"})
    assert r.status_code == 401, r.text
    body = r.text.lower()
    # generic (no reveal that email exists)
    assert "invalid" in body or "incorrect" in body or "unauthor" in body
    assert not r.cookies.get("access_token")


# --- Unknown email -> 401 generic (no enumeration) ---------------------------
def test_unknown_email_returns_401_generic():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": "nobody-xyz@vortexglobal.info", "password": "definitely-wrong-Password!123"})
    assert r.status_code == 401, r.text
    assert not r.cookies.get("access_token")


def test_login_error_bodies_are_indistinguishable():
    r1 = requests.post(f"{BASE_URL}/api/auth/login",
                       json={"email": SUPER_EMAIL, "password": "definitely-wrong-Password!aaa"})
    r2 = requests.post(f"{BASE_URL}/api/auth/login",
                       json={"email": "nobody-xyz-2@vortexglobal.info", "password": "definitely-wrong-Password!aaa"})
    assert r1.status_code == r2.status_code == 401
    # Bodies should be equivalent (no account enumeration signal)
    assert r1.json() == r2.json(), f"enumeration risk: {r1.json()} vs {r2.json()}"


# --- Superadmin overview accessible -----------------------------------------
def test_superadmin_overview_accessible(logged_in):
    headers = _bearer_from_cookies(logged_in)
    r = requests.get(f"{BASE_URL}/api/superadmin/overview", headers=headers)
    assert r.status_code == 200, r.text
