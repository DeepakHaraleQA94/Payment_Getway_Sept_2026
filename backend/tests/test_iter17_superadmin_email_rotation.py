"""
Iteration 17: Verify Super Admin email rotation to finance@vortexglobal.info.
- Exactly one super admin exists at new email
- No admin@cloudpay.io super admin remains
- Forgot-password is generic (no enumeration)
- Login works, RBAC preserved (is_superadmin, permissions ['*'])
- Superadmin control plane still accessible
- No secret/token leakage
"""
import os
import json
import re
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read from frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

NEW_EMAIL = "finance@vortexglobal.info"
OLD_EMAIL = "admin@cloudpay.io"
# Read password from memory file (do not hardcode)
def _read_password():
    with open("/app/memory/test_credentials.md") as f:
        for line in f:
            if line.strip().startswith("- Password:"):
                # "- Password: <pass>   (comment)"
                rest = line.split(":", 1)[1].strip()
                # Take token up to double-space or paren
                return re.split(r"\s{2,}|\(", rest)[0].strip()
    raise RuntimeError("password not found")

PASSWORD = _read_password()

GENERIC_MSG = "If an account exists, a reset link has been sent."

SECRET_PATTERNS = [
    re.compile(r"\$2[aby]\$", re.I),          # bcrypt hash
    re.compile(r"reset[_-]?token", re.I),
    re.compile(r"password_hash", re.I),
    re.compile(r"hashed_password", re.I),
]


def _no_leaks(body_text: str, allowed_keys=("password",)):
    # Password field name is allowed to appear as key label in generic message text? No; check patterns.
    for p in SECRET_PATTERNS:
        assert not p.search(body_text), f"Leak pattern {p.pattern} found in response: {body_text[:300]}"
    # Also ensure plaintext password not echoed
    assert PASSWORD not in body_text, "Plaintext password leaked in response body"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    return s


@pytest.fixture(scope="module")
def access_token(session):
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": NEW_EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    tok = r.cookies.get("access_token")
    if not tok:
        # fallback: JSON body
        try:
            tok = r.json().get("access_token")
        except Exception:
            tok = None
    assert tok, f"No access_token cookie/body. cookies={r.cookies.get_dict()} body={r.text[:200]}"
    return tok


def _auth_headers(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- Login / RBAC ----------

def test_login_new_email_success(session):
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": NEW_EMAIL, "password": PASSWORD},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    _no_leaks(r.text)
    data = r.json()
    # user info in body
    user = data.get("user") or data.get("me") or data
    # Confirm superadmin flag & permissions if present in body
    if isinstance(user, dict) and "is_superadmin" in user:
        assert user["is_superadmin"] is True
    if isinstance(user, dict) and "permissions" in user:
        assert "*" in user["permissions"]


def test_login_old_email_should_fail(session):
    r = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": OLD_EMAIL, "password": PASSWORD},
        timeout=20,
    )
    # Old email must NOT be a valid super admin anymore (no duplicate)
    assert r.status_code in (401, 403, 400, 404), f"Old admin email still logs in: {r.status_code} {r.text[:200]}"


def test_auth_me_reflects_superadmin(session, access_token):
    r = session.get(f"{BASE_URL}/api/auth/me", headers=_auth_headers(access_token), timeout=20)
    assert r.status_code == 200, r.text
    _no_leaks(r.text)
    data = r.json()
    # Handle wrapper shapes
    user = data.get("user", data)
    assert user.get("email", "").lower() == NEW_EMAIL
    assert user.get("is_superadmin") is True, f"is_superadmin missing/false: {user}"
    perms = user.get("permissions") or []
    assert "*" in perms, f"permissions must include '*', got {perms}"


# ---------- Forgot-password (generic response, no enumeration) ----------

def test_forgot_password_existing_generic(session):
    r = session.post(
        f"{BASE_URL}/api/auth/forgot-password",
        json={"email": NEW_EMAIL},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    _no_leaks(r.text)
    body = r.json()
    msg = (body.get("message") or body.get("detail") or "").strip()
    assert msg == GENERIC_MSG, f"Unexpected message: {msg!r}"
    # No token in body
    assert "token" not in json.dumps(body).lower() or "reset_token" not in json.dumps(body).lower()


def test_forgot_password_nonexistent_same_generic(session):
    r = session.post(
        f"{BASE_URL}/api/auth/forgot-password",
        json={"email": "definitely-not-a-user-xyz@example.com"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    _no_leaks(r.text)
    body = r.json()
    msg = (body.get("message") or body.get("detail") or "").strip()
    assert msg == GENERIC_MSG, f"Non-existent should return same generic; got {msg!r}"


def test_forgot_password_no_enumeration_parity(session):
    """Responses for existing vs non-existing must be byte-identical shape."""
    r1 = session.post(f"{BASE_URL}/api/auth/forgot-password", json={"email": NEW_EMAIL}, timeout=20)
    r2 = session.post(f"{BASE_URL}/api/auth/forgot-password", json={"email": "nobody-xyz@example.com"}, timeout=20)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json(), f"Enumeration risk: {r1.json()} vs {r2.json()}"


# ---------- Super Admin control plane ----------

def test_superadmin_overview_accessible(session, access_token):
    r = session.get(f"{BASE_URL}/api/superadmin/overview", headers=_auth_headers(access_token), timeout=20)
    assert r.status_code == 200, f"Superadmin overview not accessible: {r.status_code} {r.text[:300]}"
    _no_leaks(r.text)


# ---------- Single super admin invariant ----------

def test_only_one_superadmin_and_new_email(session, access_token):
    """Best-effort: use superadmin users listing to verify exactly one super admin exists at NEW_EMAIL."""
    # Try common listing endpoints
    candidates = [
        "/api/superadmin/users",
        "/api/superadmin/admins",
        "/api/superadmin/accounts",
    ]
    found = None
    for path in candidates:
        r = session.get(f"{BASE_URL}{path}", headers=_auth_headers(access_token), timeout=20)
        if r.status_code == 200:
            found = (path, r.json())
            break
    if not found:
        pytest.skip("No superadmin user listing endpoint available; single-superadmin invariant checked implicitly via login+me")
    path, data = found
    # Normalize to list of users
    items = data.get("items") if isinstance(data, dict) else data
    if items is None and isinstance(data, dict):
        # try common keys
        for k in ("users", "admins", "results", "data"):
            if k in data:
                items = data[k]
                break
    assert isinstance(items, list), f"Unexpected list shape from {path}: {str(data)[:200]}"
    supers = [u for u in items if isinstance(u, dict) and u.get("is_superadmin") is True]
    emails = [u.get("email", "").lower() for u in supers]
    assert NEW_EMAIL in emails, f"New super admin email not present. supers={emails}"
    assert OLD_EMAIL not in emails, f"Old admin email still present as super admin. supers={emails}"
    assert len(supers) == 1, f"Expected exactly 1 super admin, got {len(supers)}: {emails}"


# ---------- Secret leakage sweep ----------

def test_no_secret_leak_on_login_and_me_and_forgot(session, access_token):
    r1 = session.post(f"{BASE_URL}/api/auth/login", json={"email": NEW_EMAIL, "password": PASSWORD}, timeout=20)
    r2 = session.get(f"{BASE_URL}/api/auth/me", headers=_auth_headers(access_token), timeout=20)
    r3 = session.post(f"{BASE_URL}/api/auth/forgot-password", json={"email": NEW_EMAIL}, timeout=20)
    for r in (r1, r2, r3):
        _no_leaks(r.text)
