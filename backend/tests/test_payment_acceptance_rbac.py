"""RBAC permission-denial tests for the Payment Acceptance Accounts endpoints.

Confirms that a Level-2 platform user WITHOUT payment_acceptance_account.view/manage
receives 403 on both read and write, while the super admin can operate cross-tenant.
"""
import os
import random
import httpx

BASE = "http://localhost:8001"
ADMIN_EMAIL = "finance@vortexglobal.info"
OPS_EMAIL = "ops-admin@cloudpay.io"
PASSWORD = os.environ.get("ADMIN_PASSWORD", "CloudPay-DutqTuzcS1jL64hHJrCy")


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def _login(email, password=PASSWORD):
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    tok = _cookie(r, "access_token")
    assert tok
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


def _tenant(c, slug):
    tenants = c.get("/api/tenants").json()
    return next((t["id"] for t in tenants if t["slug"] == slug), None)


def _mk(vpa=None):
    return {"account_type": "upi", "display_name": "RBAC Test UPI",
            "upi_vpa": vpa or f"rbac{random.randint(1,10**7)}@yesbank",
            "bank_name": "Yes Bank", "account_holder_name": "T",
            "country": "IN", "currency": "INR", "environment": "sandbox",
            "priority": 1, "enabled": True}


def test_ops_admin_denied_read_and_write():
    """Level-2 user without payment_acceptance_account.* perms → 403 on GET and POST."""
    c = _login(OPS_EMAIL)
    # ops-admin has no tenant scope of its own here; endpoint should return 403 before any tenant check.
    r_get = c.get("/api/payment-acceptance/accounts")
    assert r_get.status_code == 403, f"GET expected 403, got {r_get.status_code} {r_get.text}"
    r_post = c.post("/api/payment-acceptance/accounts", json=_mk())
    assert r_post.status_code == 403, f"POST expected 403, got {r_post.status_code} {r_post.text}"
    r_elig = c.get("/api/payment-acceptance/accounts/eligible",
                   params={"country": "IN", "currency": "INR", "environment": "sandbox"})
    assert r_elig.status_code == 403
    c.close()


def test_super_admin_cross_tenant_scoping():
    """Super admin can list/create accounts under a specific tenant via ?tenant_id=; results are tenant-scoped."""
    c = _login(ADMIN_EMAIL)
    acme = _tenant(c, "acme")
    bp = _tenant(c, "bharat-pay")
    assert acme and bp, "expected both acme and bharat-pay tenants seeded"

    # create one under acme, one under bharat-pay
    vpa_a = f"scope{random.randint(1,10**7)}@yesbank"
    vpa_b = f"scope{random.randint(1,10**7)}@hdfcbank"
    ra = c.post("/api/payment-acceptance/accounts", params={"tenant_id": acme}, json=_mk(vpa=vpa_a))
    assert ra.status_code == 200, ra.text
    rb = c.post("/api/payment-acceptance/accounts", params={"tenant_id": bp}, json=_mk(vpa=vpa_b))
    assert rb.status_code == 200, rb.text

    # list scoped by tenant — no cross-tenant leakage
    acme_list = c.get("/api/payment-acceptance/accounts", params={"tenant_id": acme}).json()
    bp_list = c.get("/api/payment-acceptance/accounts", params={"tenant_id": bp}).json()
    acme_vpas = {x["upi_vpa"] for x in acme_list}
    bp_vpas = {x["upi_vpa"] for x in bp_list}
    assert vpa_a in acme_vpas and vpa_a not in bp_vpas
    assert vpa_b in bp_vpas and vpa_b not in acme_vpas

    # Verify no provider api_key / secret style fields ever exposed in responses.
    forbidden_keys = {"api_key", "secret", "api_secret", "webhook_secret", "provider_api_key"}
    for row in acme_list + bp_list:
        assert not (forbidden_keys & set(row.keys())), f"unexpected secret key in response: {row.keys()}"
        assert row["verification_status"] == "unverified"
    c.close()


def test_super_admin_can_full_crud():
    c = _login(ADMIN_EMAIL)
    acme = _tenant(c, "acme")
    a = c.post("/api/payment-acceptance/accounts", params={"tenant_id": acme}, json=_mk()).json()
    aid = a["id"]
    assert c.post(f"/api/payment-acceptance/accounts/{aid}/disable").json()["enabled"] is False
    assert c.post(f"/api/payment-acceptance/accounts/{aid}/enable").json()["enabled"] is True
    assert c.post(f"/api/payment-acceptance/accounts/{aid}/priority", json={"priority": 9}).json()["priority"] == 9
    upd = c.patch(f"/api/payment-acceptance/accounts/{aid}", json={"display_name": "Renamed"}).json()
    assert upd["display_name"] == "Renamed"
    d = c.delete(f"/api/payment-acceptance/accounts/{aid}")
    assert d.status_code == 200 and d.json().get("deleted") is True
    g = c.get(f"/api/payment-acceptance/accounts/{aid}")
    assert g.status_code == 404
    c.close()
