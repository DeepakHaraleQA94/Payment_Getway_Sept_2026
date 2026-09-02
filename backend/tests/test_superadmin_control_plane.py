"""Super Admin control-plane tests: guard, platform-admin permission exactness, guardrails,
feature control. Extends existing IAM. Run serially: `pytest tests/ -n0`.
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


def _client(email, password):
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    tok = _cookie(r, "access_token")
    if tok:
        c.headers["Authorization"] = f"Bearer {tok}"
    return c, r


@pytest.fixture(scope="module")
def admin():
    c, _ = _client(ADMIN_EMAIL, ADMIN_PASSWORD)
    yield c
    c.close()


def test_overview_superadmin_only(admin):
    r = admin.get("/api/superadmin/overview")
    assert r.status_code == 200
    assert {"tenants", "platform_admins", "super_admins"} <= set(r.json())


def test_create_platform_admin_has_exact_permissions_no_wildcard(admin):
    email = f"padmin_{uuid.uuid4().hex[:8]}@cloudpay.io"
    r = admin.post("/api/superadmin/admins", json={
        "email": email, "name": "P Admin", "password": "Password123",
        "permission_codes": ["tenant.manage", "audit.view"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_superadmin"] is False
    assert set(body["permissions"]) == {"tenant.manage", "audit.view"}
    assert "*" not in body["permissions"]


def test_platform_admin_blocked_from_superadmin_plane(admin):
    email = f"blocked_{uuid.uuid4().hex[:8]}@cloudpay.io"
    admin.post("/api/superadmin/admins", json={
        "email": email, "name": "Blocked", "password": "Password123",
        "permission_codes": ["tenant.manage"]})
    c, _ = _client(email, "Password123")
    try:
        assert c.get("/api/superadmin/overview").status_code == 403
        assert c.get("/api/superadmin/admins").status_code == 403
        assert c.post("/api/superadmin/admins", json={"email": "x@y.z", "password": "Password123"}).status_code == 403
        # Cannot toggle tenant features via the super-admin plane.
        assert c.put("/api/superadmin/features",
                     json={"tenant_id": str(uuid.uuid4()), "key": "refunds", "enabled": False}).status_code == 403
    finally:
        c.close()


def test_cannot_modify_super_admin_via_plane(admin):
    admins = admin.get("/api/superadmin/admins").json()
    sa = next(a for a in admins if a["is_superadmin"])
    # Attempting to change a super admin through the admin-management endpoints is refused.
    assert admin.patch(f"/api/superadmin/admins/{sa['id']}", json={"status": "suspended"}).status_code == 403
    assert admin.post(f"/api/superadmin/admins/{sa['id']}/set-password",
                      json={"password": "Password123"}).status_code == 403


def test_set_password_and_suspend(admin):
    email = f"pw_{uuid.uuid4().hex[:8]}@cloudpay.io"
    admin_id = admin.post("/api/superadmin/admins", json={
        "email": email, "name": "PW", "password": "Password123",
        "permission_codes": ["audit.view"]}).json()["id"]
    # Rotate password.
    assert admin.post(f"/api/superadmin/admins/{admin_id}/set-password",
                      json={"password": "NewPassword456"}).status_code == 200
    c, r = _client(email, "NewPassword456")
    c.close()
    assert r.status_code == 200
    # Suspend blocks authentication.
    assert admin.patch(f"/api/superadmin/admins/{admin_id}", json={"status": "suspended"}).status_code == 200
    _, r2 = _client(email, "NewPassword456")
    assert r2.status_code in (401, 403)


def test_tenant_feature_control_roundtrip(admin):
    tid = admin.post("/api/tenants", json={"name": "Feat Co", "slug": f"feat-{uuid.uuid4().hex[:8]}",
                                           "default_currency": "USD"}).json()["id"]
    feats = admin.get(f"/api/superadmin/features?tenant_id={tid}").json()
    assert any(f["key"] == "checkout" for f in feats)
    # Disable checkout, verify it is reflected in the tenant's feature list.
    admin.put("/api/superadmin/features", json={"tenant_id": tid, "key": "checkout", "enabled": False})
    listing = admin.get(f"/api/features?tenant_id={tid}").json()
    assert any(f["key"] == "checkout" and f["enabled"] is False for f in listing)
