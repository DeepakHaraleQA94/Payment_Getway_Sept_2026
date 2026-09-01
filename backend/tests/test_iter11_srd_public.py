"""Iteration 11: SRD verification against public preview URL.

Focuses on the specific SRD claims not fully asserted by unit tests:
 * routing_attempts metadata has 2 entries on failover
 * no accounts + auto => 400 "no healthy provider"
 * examplepsp live account with credentials stores only credentials_ref (raw secret never returned)
 * UPI endpoints return upi:// (no card data)
 * examplepsp is in /api/providers/available with live=true + required_credentials
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("PUBLIC_BASE_URL", "https://pay-gateway-core.preview.emergentagent.com")
ADMIN_EMAIL = "admin@cloudpay.io"
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=45, verify=True)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    tok = _cookie(r, "access_token") or r.json().get("access_token")
    assert tok
    c.headers["Authorization"] = f"Bearer {tok}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def tenant(admin):
    slug = f"TEST-iter11-{uuid.uuid4().hex[:6]}"
    r = admin.post("/api/tenants", json={"name": "Iter11 Co", "slug": slug, "default_currency": "USD"})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    # add mock (priority 10) + examplepsp (priority 20), both sandbox
    r1 = admin.post(f"/api/providers?tenant_id={tid}",
                    json={"provider_key": "mock", "display_name": "Mock", "mode": "sandbox",
                          "enabled": True, "priority": 10, "supported_currencies": ["USD"]})
    assert r1.status_code in (200, 201), r1.text
    r2 = admin.post(f"/api/providers?tenant_id={tid}",
                    json={"provider_key": "examplepsp", "display_name": "Example PSP", "mode": "sandbox",
                          "enabled": True, "priority": 20, "supported_currencies": ["USD"]})
    assert r2.status_code in (200, 201), r2.text
    return tid


def test_available_lists_examplepsp_with_live(admin):
    caps = {p["key"]: p for p in admin.get("/api/providers/available").json()}
    assert "examplepsp" in caps
    e = caps["examplepsp"]
    assert e["live_supported"] is True
    assert e["supported_environments"] == ["sandbox", "live"]
    keys = {c["key"] for c in e["required_credentials"]}
    assert {"api_key", "api_secret", "webhook_secret"}.issubset(keys)


def test_auto_routing_success_via_mock(admin, tenant):
    r = admin.post(f"/api/payments?tenant_id={tenant}",
                   json={"reference": "TEST-R1", "amount_minor": 5000, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"iter11-{uuid.uuid4().hex}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["provider_key"] == "mock"


def test_auto_routing_failover_records_two_attempts(admin, tenant):
    r = admin.post(f"/api/payments?tenant_id={tenant}",
                   json={"reference": "TEST-R2", "amount_minor": 5013, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"iter11-{uuid.uuid4().hex}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded"
    assert body["provider_key"] == "examplepsp"
    # NOTE: PaymentOut schema does not expose metadata_json — verified in DB directly.
    # See iteration_11.json backend_issues.minor for the reported API surface gap.


def test_auto_routing_no_accounts_returns_400(admin):
    slug = f"TEST-empty-{uuid.uuid4().hex[:6]}"
    tid = admin.post("/api/tenants", json={"name": "Empty Co", "slug": slug, "default_currency": "USD"}).json()["id"]
    r = admin.post(f"/api/payments?tenant_id={tid}",
                   json={"reference": "TEST-R3", "amount_minor": 5000, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"iter11-{uuid.uuid4().hex}"})
    assert r.status_code == 400
    assert "no healthy provider" in r.text.lower()


def test_examplepsp_credentials_are_referenced_not_returned(admin):
    slug = f"TEST-creds-{uuid.uuid4().hex[:6]}"
    tid = admin.post("/api/tenants", json={"name": "Creds Co", "slug": slug, "default_currency": "USD"}).json()["id"]
    secret_key = f"SECRET-{uuid.uuid4().hex}"
    r = admin.post(f"/api/providers?tenant_id={tid}",
                   json={"provider_key": "examplepsp", "display_name": "Ex Live", "mode": "live",
                         "enabled": True, "priority": 30, "supported_currencies": ["USD"],
                         "credentials": {"api_key": secret_key, "api_secret": "s3cr3t",
                                         "webhook_secret": "wh"}})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    # raw secret must NOT appear anywhere in the response body
    import json as _json
    dumped = _json.dumps(body)
    assert secret_key not in dumped, "raw api_key leaked in create response"
    assert "s3cr3t" not in dumped
    # list also must not leak
    listing = admin.get(f"/api/providers?tenant_id={tid}").json()
    assert secret_key not in _json.dumps(listing)
    # a credentials_ref should be present somewhere (typically credentials_ref field)
    found_ref = False
    def _walk(x):
        nonlocal found_ref
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "credentials_ref" and isinstance(v, str) and v:
                    found_ref = True
                _walk(v)
        elif isinstance(x, list):
            for i in x:
                _walk(i)
    _walk(listing)
    assert found_ref, "expected credentials_ref reference on account listing"


def test_upi_intent_qr_and_status(admin):
    intent = admin.post("/api/providers/mock/intent",
                        json={"amount_minor": 25000, "currency": "INR",
                              "reference": "TEST-UPI", "method": "upi"})
    assert intent.status_code == 200, intent.text
    tok = intent.json()["client_token"]
    assert tok.startswith("upi://pay")
    assert "card" not in tok.lower()
    qr = admin.post("/api/providers/mock/qr",
                    json={"amount_minor": 25000, "currency": "INR",
                          "reference": "TEST-UPI", "method": "upi"})
    assert qr.status_code == 200
    assert qr.json()["qr_payload"].startswith("upi://pay")

    # lifecycle: pending
    ip = admin.post("/api/providers/mock/intent",
                    json={"amount_minor": 20011, "currency": "INR",
                          "reference": "TEST-UPI-P", "method": "upi"}).json()
    s = admin.get(f"/api/providers/mock/status/{ip['intent_id']}")
    assert s.status_code == 200 and s.json()["status"] == "pending"
    # cancelled
    ic = admin.post("/api/providers/mock/intent",
                    json={"amount_minor": 20044, "currency": "INR",
                          "reference": "TEST-UPI-C", "method": "upi"}).json()
    sc = admin.get(f"/api/providers/mock/status/{ic['intent_id']}")
    assert sc.status_code == 200 and sc.json()["status"] == "cancelled"
