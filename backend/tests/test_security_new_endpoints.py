"""Security regression for the newly-added endpoints (scope-freeze verification):
provider stability, alert history, report email-settings, custom report run, report download.

Proves: cross-tenant denial, RBAC (report.manage), unauthenticated 401, and no secret leakage.
Run serially: `pytest tests/ -n0`.
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    c.headers["Authorization"] = f"Bearer {_cookie(r, 'access_token')}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def two_tenants(admin):
    tenants = admin.get("/api/tenants").json()
    acme = next(t["id"] for t in tenants if t["slug"] == "acme")
    slug = f"sec{uuid.uuid4().hex[:6]}"
    other = admin.post("/api/tenants", json={"name": "Sec Co", "slug": slug,
                                             "default_currency": "USD"}).json()
    return acme, other["id"]


@pytest.fixture(scope="module")
def limited_user(admin, two_tenants):
    """User bound to 'other' tenant WITHOUT report.manage/provider.manage."""
    _, other = two_tenants
    role = admin.post(f"/api/roles?tenant_id={other}",
                      json={"name": "SecOps", "permission_codes": ["payment.create"]}).json()
    email = f"sec_{uuid.uuid4().hex[:8]}@test.com"
    admin.post(f"/api/users?tenant_id={other}",
               json={"email": email, "name": "Sec", "password": "Password123", "role_id": role["id"]})
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": email, "password": "Password123"})
    c.headers["Authorization"] = f"Bearer {_cookie(r, 'access_token')}"
    yield c, other
    c.close()


# ----------------------------- cross-tenant denial -----------------------------
def test_new_endpoints_cross_tenant_get_denied(limited_user, two_tenants):
    c, _ = limited_user
    acme, _ = two_tenants
    assert c.get(f"/api/providers/stability?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/providers/alerts/history?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/reports/scheduled/email-settings?tenant_id={acme}").status_code == 403


def test_report_download_cross_tenant_denied(admin, limited_user, two_tenants):
    c, _ = limited_user
    acme, _ = two_tenants
    rep = admin.post(f"/api/reports/scheduled/run?tenant_id={acme}&report_type=daily").json()
    assert rep["file_id"]
    assert c.get(f"/api/reports/scheduled/download/{rep['file_id']}").status_code == 403


# ----------------------------- RBAC (report.manage) -----------------------------
def test_report_run_requires_permission(limited_user):
    c, other = limited_user
    assert c.post(f"/api/reports/scheduled/run?tenant_id={other}&report_type=daily").status_code == 403


def test_email_settings_update_requires_permission(limited_user):
    c, other = limited_user
    r = c.put(f"/api/reports/scheduled/email-settings?tenant_id={other}", json={"enabled": True})
    assert r.status_code == 403


def test_limited_user_can_read_own_tenant(limited_user):
    c, other = limited_user
    # Read-only endpoints only need authentication + own-tenant scope.
    assert c.get(f"/api/providers/stability?tenant_id={other}").status_code == 200
    assert c.get(f"/api/providers/alerts/history?tenant_id={other}").status_code == 200
    assert c.get(f"/api/reports/scheduled/email-settings?tenant_id={other}").status_code == 200


# ----------------------------- unauthenticated -----------------------------
def test_unauthenticated_denied(two_tenants):
    acme, _ = two_tenants
    anon = httpx.Client(base_url=BASE, timeout=30)
    for path in ("/api/providers/stability", "/api/providers/alerts/history",
                 "/api/reports/scheduled/email-settings"):
        assert anon.get(f"{path}?tenant_id={acme}").status_code == 401
    assert anon.post(f"/api/reports/scheduled/run?tenant_id={acme}").status_code == 401
    anon.close()


# ----------------------------- no secret leakage -----------------------------
def test_new_endpoints_never_leak_secrets(admin, two_tenants):
    acme, _ = two_tenants
    for path in (f"/api/providers/stability?tenant_id={acme}",
                 f"/api/providers/alerts/history?tenant_id={acme}",
                 f"/api/reports/scheduled/email-settings?tenant_id={acme}"):
        text = admin.get(path).text.lower()
        for leak in ("ciphertext", "credential_ref", "secret", "fernet", "private_key"):
            assert leak not in text, f"{leak} leaked in {path}"


# ----------------------------- hardening: headers, rate limit, config -----------------------------
def test_security_headers_present():
    anon = httpx.Client(base_url=BASE, timeout=30)
    r = anon.get("/api/")
    anon.close()
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "referrer-policy" in r.headers
    assert "permissions-policy" in r.headers
    assert r.headers.get("cross-origin-opener-policy") == "same-origin"


def test_rate_limit_engages_on_forgot_password():
    """Repeated forgot-password from one IP is throttled (429). Robust to residual bucket state."""
    anon = httpx.Client(base_url=BASE, timeout=30)
    codes = [anon.post("/api/auth/forgot-password",
                       json={"email": "ratelimit-probe@test.com"}).status_code for _ in range(8)]
    anon.close()
    assert 429 in codes
    assert all(c in (200, 429) for c in codes)


def test_production_config_fails_fast_on_insecure_settings():
    """Settings.validate() must raise in production for wildcard CORS / missing secrets."""
    from app.core.config import Settings
    saved = dict(os.environ)
    try:
        os.environ["APP_ENV"] = "production"
        os.environ["CORS_ORIGINS"] = "*"
        with pytest.raises(RuntimeError):
            Settings().validate()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_dev_config_returns_warnings_without_raising():
    from app.core.config import settings
    warnings = settings.validate()
    assert isinstance(warnings, list)
