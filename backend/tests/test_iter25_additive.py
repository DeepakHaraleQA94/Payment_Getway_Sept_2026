"""Additive tests: checkout acceptance object + Tenant Admin role permissions."""
import os, random, httpx

BASE = "http://localhost:8001"
ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CloudPay-DutqTuzcS1jL64hHJrCy")


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def _client():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = f"Bearer {_cookie(r, 'access_token')}"
    return c


def _tenant(c, slug):
    return next(t["id"] for t in c.get("/api/tenants").json() if t["slug"] == slug)


def test_checkout_public_returns_acceptance_object_when_eligible():
    c = _client()
    tid = _tenant(c, "acme")  # US tenant
    # Create INR acceptance account for acme
    vpa = f"chk{random.randint(1,10**7)}@yesbank"
    r = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid},
               json={"account_type": "upi", "display_name": "Checkout INR UPI",
                     "upi_vpa": vpa, "bank_name": "Yes Bank",
                     "account_holder_name": "Merchant Pvt Ltd", "country": "IN",
                     "currency": "INR", "environment": "sandbox", "priority": 1,
                     "enabled": True})
    assert r.status_code == 200, r.text

    # Create checkout session with INR currency
    ref = f"ref-{random.randint(1,10**7)}"
    s = c.post("/api/checkout/sessions", params={"tenant_id": tid},
               json={"reference": ref, "amount_minor": 5000, "currency": "INR"})
    assert s.status_code in (200, 201), s.text
    token = s.json().get("token") or s.json().get("session_token") or s.json().get("id")
    assert token, s.text

    # Public GET
    pub = httpx.get(f"{BASE}/api/public/checkout/{token}")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert "acceptance" in body, "response missing 'acceptance' key"
    acc = body["acceptance"]
    assert acc is not None
    for k in ("display_name", "upi_vpa", "bank_name", "account_type", "verification_status"):
        assert k in acc, f"missing {k}"
    assert acc["verification_status"] == "unverified"
    assert acc["account_type"] == "upi"
    c.close()


def test_checkout_public_acceptance_null_when_no_eligible():
    c = _client()
    tid = _tenant(c, "acme")
    # EUR session; no EUR acceptance account should exist for acme
    ref = f"ref-{random.randint(1,10**7)}"
    s = c.post("/api/checkout/sessions", params={"tenant_id": tid},
               json={"reference": ref, "amount_minor": 5000, "currency": "EUR"})
    assert s.status_code in (200, 201), s.text
    token = s.json().get("token") or s.json().get("session_token") or s.json().get("id")
    pub = httpx.get(f"{BASE}/api/public/checkout/{token}")
    assert pub.status_code == 200
    body = pub.json()
    assert "acceptance" in body
    assert body["acceptance"] is None
    c.close()


def test_tenant_admin_role_has_acceptance_perms():
    c = _client()
    tid = _tenant(c, "acme")
    r = c.get("/api/roles", params={"tenant_id": tid})
    assert r.status_code == 200, r.text
    roles = r.json()
    ta = None
    for role in roles:
        if role.get("name", "").lower() in ("tenant admin", "tenant-admin"):
            ta = role
            break
    assert ta is not None, f"Tenant Admin role not seeded: {[r.get('name') for r in roles]}"
    perms_raw = ta.get("permissions") or []
    perms = {p.get("code") if isinstance(p, dict) else p for p in perms_raw}
    assert "payment_acceptance_account.view" in perms, perms
    assert "payment_acceptance_account.manage" in perms, perms
    c.close()
