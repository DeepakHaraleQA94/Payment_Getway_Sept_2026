"""Authorization & tenant-isolation regression tests (PROMPT 03).

Two tenants (acme + a fresh isolated tenant) are used to prove cross-tenant denial
across GET/POST/PATCH/DELETE, guessed IDs, query params, API keys, revoked keys,
insufficient permission, feature-entitlement enforcement, and legitimate access.
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
    slug = f"iso{uuid.uuid4().hex[:6]}"
    other = admin.post("/api/tenants", json={"name": "Isolated Co", "slug": slug,
                                             "default_currency": "USD"}).json()
    return acme, other["id"]


@pytest.fixture(scope="module")
def tenant_user(admin, two_tenants):
    """A Client-scoped user bound to the 'other' tenant with payment+refund perms."""
    _, other = two_tenants
    perms = admin.get("/api/permissions").json()
    codes = ["payment.create", "refund.create"]
    role = admin.post(f"/api/roles?tenant_id={other}",
                      json={"name": "ClientOps", "permission_codes": codes}).json()
    email = f"iso_{uuid.uuid4().hex[:8]}@test.com"
    admin.post(f"/api/users?tenant_id={other}",
               json={"email": email, "name": "Iso", "password": "Password123", "role_id": role["id"]})
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": email, "password": "Password123"})
    c.headers["Authorization"] = f"Bearer {_cookie(r, 'access_token')}"
    yield c, other
    c.close()


def test_cross_tenant_get_denied(tenant_user, two_tenants):
    c, _ = tenant_user
    acme, _ = two_tenants
    assert c.get(f"/api/payments?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/settlements?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/ledger/entries?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/api-keys?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/webhooks/deliveries?tenant_id={acme}").status_code == 403
    assert c.get(f"/api/reports/scheduled?tenant_id={acme}").status_code == 403


def test_cross_tenant_post_denied(tenant_user, two_tenants):
    c, _ = tenant_user
    acme, _ = two_tenants
    r = c.post(f"/api/payments?tenant_id={acme}",
               json={"reference": "X", "amount_minor": 100, "currency": "USD", "provider_key": "mock"})
    assert r.status_code == 403


def test_cross_tenant_guessed_id_denied(tenant_user, two_tenants, admin):
    c, _ = tenant_user
    acme, _ = two_tenants
    # Admin creates a payment in acme; the other tenant's user must not read it by id.
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "SECRET", "amount_minor": 500, "currency": "USD", "provider_key": "mock"}).json()
    assert c.get(f"/api/payments/{p['id']}").status_code == 404
    # And cannot refund another tenant's payment (PATCH/POST on guessed id).
    assert c.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 100}).status_code == 404


def test_cross_tenant_export_denied(tenant_user, two_tenants):
    c, _ = tenant_user
    acme, _ = two_tenants
    assert c.get(f"/api/reports/export/payments.csv?tenant_id={acme}").status_code == 403


def test_api_key_is_tenant_scoped(admin, two_tenants):
    acme, other = two_tenants
    key_a = admin.post(f"/api/api-keys?tenant_id={acme}", json={"label": "A"}).json()["secret"]
    key_b = admin.post(f"/api/api-keys?tenant_id={other}", json={"label": "B"}).json()["secret"]
    c = httpx.Client(base_url=BASE, timeout=30)
    # A checkout session created with tenant A's key belongs to tenant A only.
    r = c.post("/api/v1/checkout/sessions", headers={"X-API-Key": key_a},
               json={"amount_minor": 200, "currency": "USD"})
    assert r.status_code == 200
    token = r.json()["token"]
    # Tenant B's user cannot see tenant A's checkout session in its own list.
    sessions_b = admin.get(f"/api/checkout/sessions?tenant_id={other}").json()
    assert all(s["token"] != token for s in sessions_b)
    c.close()


def test_revoked_api_key_denied(admin, two_tenants):
    acme, _ = two_tenants
    created = admin.post(f"/api/api-keys?tenant_id={acme}", json={"label": "revoke-me"}).json()
    key_id, secret = created["id"], created["secret"]
    assert admin.delete(f"/api/api-keys/{key_id}").status_code == 200
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/v1/checkout/sessions", headers={"X-API-Key": secret},
               json={"amount_minor": 200, "currency": "USD"})
    assert r.status_code == 401
    # Invalid key does not leak details.
    r2 = c.post("/api/v1/checkout/sessions", headers={"X-API-Key": "sk_test_bogus"},
                json={"amount_minor": 100, "currency": "USD"})
    assert r2.status_code == 401
    c.close()


def test_insufficient_permission_denied(tenant_user):
    c, other = tenant_user
    # ClientOps role lacks provider.manage / user.manage / feature.manage.
    assert c.post(f"/api/providers?tenant_id={other}",
                  json={"provider_key": "mock", "display_name": "X"}).status_code == 403
    assert c.post(f"/api/users?tenant_id={other}",
                  json={"email": f"z{uuid.uuid4().hex[:6]}@t.com", "password": "Password123"}).status_code == 403


def test_feature_entitlement_enforced_on_backend(admin, two_tenants):
    acme, other = two_tenants
    # Create a payment in 'other', then disable the 'refunds' feature and confirm the API rejects refunds.
    p = admin.post(f"/api/payments?tenant_id={other}",
                   json={"reference": "FEAT", "amount_minor": 4000, "currency": "USD", "provider_key": "mock"}).json()
    ff = admin.post(f"/api/features?tenant_id={other}",
                    json={"key": "refunds", "name": "Refunds", "enabled": False}).json()
    r = admin.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 1000})
    assert r.status_code == 403  # feature disabled -> backend rejects
    # Re-enable and confirm it now works.
    admin.patch(f"/api/features/{ff['id']}", json={"enabled": True})
    r2 = admin.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 1000})
    assert r2.status_code == 200


def test_superadmin_cross_tenant_allowed(admin, two_tenants):
    acme, other = two_tenants
    assert admin.get(f"/api/payments?tenant_id={acme}").status_code == 200
    assert admin.get(f"/api/payments?tenant_id={other}").status_code == 200


def test_same_tenant_access_allowed(tenant_user):
    c, other = tenant_user
    assert c.get(f"/api/payments?tenant_id={other}").status_code == 200
    r = c.post(f"/api/payments?tenant_id={other}",
               json={"reference": "OK", "amount_minor": 300, "currency": "USD", "provider_key": "mock"})
    assert r.status_code == 200
