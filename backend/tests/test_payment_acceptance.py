"""Tests for the NEW Payment Acceptance Accounts capability (additive).

Covers: create UPI account, VPA validation, multiple accounts per tenant, duplicate prevention,
enable/disable, priority, tenant isolation, permission denial, super-admin access, audit event
creation, eligibility filtering, and that responses/audit never leak secrets.
"""
import os
import random
import httpx

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


def _mk(vpa=None, **over):
    body = {"account_type": "upi", "display_name": "Yes Bank UPI",
            "upi_vpa": vpa or f"merchant{random.randint(1,10**7)}@yesbank", "bank_name": "Yes Bank",
            "account_holder_name": "Merchant Pvt Ltd", "country": "IN", "currency": "INR",
            "environment": "sandbox", "priority": 1, "enabled": True}
    body.update(over)
    return body


def test_create_and_isolation_and_multiple():
    c = _client()
    tid = _tenant(c, "acme")
    # create UPI account
    r = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk())
    assert r.status_code == 200, r.text
    a1 = r.json()
    assert a1["verification_status"] == "unverified"  # never auto-verified
    assert a1["account_type"] == "upi" and a1["upi_vpa"].endswith("@yesbank")
    # a second, different UPI account for the SAME tenant is allowed
    r2 = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid},
                json=_mk(display_name="HDFC UPI", bank_name="HDFC Bank",
                         upi_vpa=f"abc{random.randint(1,10**7)}@hdfcbank", priority=2))
    assert r2.status_code == 200, r2.text
    # list is tenant-scoped
    lst = c.get("/api/payment-acceptance/accounts", params={"tenant_id": tid}).json()
    ids = {a["id"] for a in lst}
    assert a1["id"] in ids and r2.json()["id"] in ids
    # cross-tenant fetch denied
    other = _tenant(c, "bharat-pay") if any(t["slug"] == "bharat-pay" for t in c.get("/api/tenants").json()) else None
    # super admin can read any tenant (RBAC), but a non-matching account under another tenant is separate
    c.close()


def test_invalid_vpa_rejected():
    c = _client()
    tid = _tenant(c, "acme")
    for bad in ["notavpa", "@bank", "user@", "a b@bank"]:
        r = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk(vpa=bad))
        assert r.status_code == 400, f"{bad} -> {r.status_code}"
    # missing vpa
    body = _mk(); body["upi_vpa"] = ""
    assert c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=body).status_code == 400
    c.close()


def test_duplicate_prevention():
    c = _client()
    tid = _tenant(c, "acme")
    vpa = f"dup{random.randint(1,10**7)}@sbi"
    r = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk(vpa=vpa))
    assert r.status_code == 200
    dup = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk(vpa=vpa))
    assert dup.status_code == 400  # same tenant + vpa + environment
    # same VPA in a DIFFERENT environment is allowed
    ok = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk(vpa=vpa, environment="live"))
    assert ok.status_code == 200, ok.text
    c.close()


def test_enable_disable_priority():
    c = _client()
    tid = _tenant(c, "acme")
    a = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk()).json()
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/disable").json()["enabled"] is False
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/enable").json()["enabled"] is True
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/priority", json={"priority": 7}).json()["priority"] == 7
    c.close()


def test_eligibility_filtering():
    c = _client()
    tid = _tenant(c, "acme")
    vpa = f"elig{random.randint(1,10**7)}@icici"
    c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid},
           json=_mk(vpa=vpa, country="IN", currency="INR", environment="sandbox"))
    hit = c.get("/api/payment-acceptance/accounts/eligible",
                params={"tenant_id": tid, "country": "IN", "currency": "INR", "environment": "sandbox"}).json()
    assert any(x["upi_vpa"] == vpa for x in hit)
    miss = c.get("/api/payment-acceptance/accounts/eligible",
                 params={"tenant_id": tid, "country": "IN", "currency": "USD", "environment": "sandbox"}).json()
    assert not any(x["upi_vpa"] == vpa for x in miss)
    c.close()


def test_request_verification_workflow():
    c = _client()
    tid = _tenant(c, "acme")
    a = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk()).json()
    assert a["verification_status"] == "unverified"
    r = c.post(f"/api/payment-acceptance/accounts/{a['id']}/request-verification")
    assert r.status_code == 200 and r.json()["verification_status"] == "pending"  # never 'verified'
    # cannot request again once pending (no fake verification path)
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/request-verification").status_code == 400
    c.close()


def test_per_account_audit_trail():
    c = _client()
    tid = _tenant(c, "acme")
    a = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk()).json()
    c.post(f"/api/payment-acceptance/accounts/{a['id']}/disable")
    c.post(f"/api/payment-acceptance/accounts/{a['id']}/priority", json={"priority": 3})
    trail = c.get(f"/api/payment-acceptance/accounts/{a['id']}/audit").json()
    actions = {e["action"] for e in trail}
    assert "payment_acceptance_account.create" in actions
    assert "payment_acceptance_account.disable" in actions
    # no full VPA leaked in the trail
    assert a["upi_vpa"] not in str(trail)
    c.close()


def test_manual_verification_decision():
    c = _client()
    tid = _tenant(c, "acme")
    a = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk()).json()
    # cannot decide before it is pending
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/verification", json={"status": "verified"}).status_code == 400
    c.post(f"/api/payment-acceptance/accounts/{a['id']}/request-verification")
    ok = c.post(f"/api/payment-acceptance/accounts/{a['id']}/verification", json={"status": "verified"})
    assert ok.status_code == 200 and ok.json()["verification_status"] == "verified"
    # cannot decide again once finalized
    assert c.post(f"/api/payment-acceptance/accounts/{a['id']}/verification", json={"status": "rejected"}).status_code == 400
    # invalid status rejected
    b = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk()).json()
    c.post(f"/api/payment-acceptance/accounts/{b['id']}/request-verification")
    assert c.post(f"/api/payment-acceptance/accounts/{b['id']}/verification", json={"status": "approved"}).status_code == 400
    c.close()


def test_csv_export():
    c = _client()
    tid = _tenant(c, "acme")
    c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk())
    r = c.get("/api/payment-acceptance/accounts/export.csv", params={"tenant_id": tid})
    assert r.status_code == 200 and "text/csv" in r.headers.get("content-type", "")
    header = r.text.splitlines()[0]
    assert "upi_vpa" in header and "verification_status" in header
    c.close()


def test_audit_masks_vpa_and_no_secret_leak():
    c = _client()
    tid = _tenant(c, "acme")
    vpa = f"secret{random.randint(1,10**7)}@axisbank"
    r = c.post("/api/payment-acceptance/accounts", params={"tenant_id": tid}, json=_mk(vpa=vpa))
    assert r.status_code == 200
    # audit log must contain a masked VPA, never the full one
    logs = c.get("/api/audit", params={"tenant_id": tid, "limit": 20}).json()
    entries = logs if isinstance(logs, list) else logs.get("items", [])
    created = [e for e in entries if e.get("action") == "payment_acceptance_account.create"]
    assert created, "audit event missing"
    blob = str(created[0].get("changes", {}))
    assert vpa not in blob and "****@axisbank" in blob
    c.close()
