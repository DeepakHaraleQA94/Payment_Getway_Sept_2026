"""PROMPT 05 additional regression coverage.

Verifies:
 * Failed Stripe payment posts NO ledger credit.
 * Mock provider still succeeds and posts exactly one ledger credit (regression).
 * POST /api/providers with mode='live' is rejected 400.
 * Provider discovery never shows stripe with test_mode=false.
 * Protected endpoints require auth (401 without token).
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    tok = _cookie(r, "access_token")
    assert tok, "admin login failed"
    c.headers["Authorization"] = f"Bearer {tok}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme(admin):
    return next(t["id"] for t in admin.get("/api/tenants").json() if t["slug"] == "acme")


def test_protected_endpoints_require_auth():
    r = httpx.get(f"{BASE}/api/providers/available", timeout=10)
    assert r.status_code in (401, 403), r.text
    r2 = httpx.get(f"{BASE}/api/tenants", timeout=10)
    assert r2.status_code in (401, 403), r2.text


def test_admin_login_returns_cookie():
    r = httpx.post(f"{BASE}/api/auth/login",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    assert _cookie(r, "access_token"), "access_token cookie missing"


def test_stripe_discovery_never_shows_live(admin):
    r = admin.get("/api/providers/available")
    assert r.status_code == 200
    for p in r.json():
        if p["key"] == "stripe":
            assert p["mode"] == "sandbox"
            assert p["test_mode"] is True


def test_post_provider_rejects_live_mode(admin, acme):
    body = {"provider_key": "stripe", "display_name": "Stripe LIVE",
            "mode": "live", "priority": 10, "enabled": True, "config": {}}
    r = admin.post(f"/api/providers?tenant_id={acme}", json=body)
    assert r.status_code == 400, r.text
    assert "live" in r.text.lower()


def test_failed_stripe_payment_posts_no_ledger_credit(admin, acme):
    """Placeholder key causes real Stripe call to fail. Payment is 'failed', net/fee=0,
    and no ledger credit is posted for this payment."""
    idem = f"stripe-noledger-{uuid.uuid4().hex}"
    body = {"reference": f"INV-NOLEDGER-{uuid.uuid4().hex[:6]}", "amount_minor": 1500,
            "currency": "USD", "provider_key": "stripe", "idempotency_key": idem}
    r = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["provider_key"] == "stripe"
    assert p["status"] == "failed", f"expected graceful failed, got {p}"
    assert p.get("net_minor", 0) == 0
    assert p.get("fee_minor", 0) == 0
    pid = p["id"]

    # Confirm ledger has no entries tied to this payment.
    lr = admin.get(f"/api/ledger/entries?tenant_id={acme}")
    assert lr.status_code == 200, lr.text
    entries = lr.json() if isinstance(lr.json(), list) else lr.json().get("items", lr.json())
    matched = [e for e in entries if str(e.get("ref_id")) == str(pid)]
    assert matched == [], f"failed stripe payment should not post ledger entries: {matched}"


def test_mock_provider_succeeds_and_posts_single_credit(admin, acme):
    idem = f"mock-{uuid.uuid4().hex}"
    body = {"reference": f"INV-MOCK-{uuid.uuid4().hex[:6]}", "amount_minor": 2500,
            "currency": "USD", "provider_key": "mock", "idempotency_key": idem}
    r = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "succeeded", p
    assert p["provider_key"] == "mock"
    pid = p["id"]

    lr = admin.get(f"/api/ledger/entries?tenant_id={acme}")
    assert lr.status_code == 200
    entries = lr.json() if isinstance(lr.json(), list) else lr.json().get("items", lr.json())
    credits = [e for e in entries
               if str(e.get("ref_id")) == str(pid) and e.get("direction") == "credit"]
    assert len(credits) == 1, f"mock success should post exactly 1 ledger credit, got {credits}"


def test_stripe_idempotency_lock_before_dispatch(admin, acme):
    """Same idempotency_key returns identical payment id even though the external call
    fails — proving the DB row (and lock) is claimed before provider.charge is dispatched.
    """
    idem = f"stripe-idem-{uuid.uuid4().hex}"
    body = {"reference": f"INV-IDEM-{uuid.uuid4().hex[:6]}", "amount_minor": 999,
            "currency": "USD", "provider_key": "stripe", "idempotency_key": idem}
    r1 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    r2 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


def test_stripe_webhook_public_no_auth_unmatched():
    payload = {"type": "payment_intent.succeeded",
               "data": {"object": {"id": f"pi_{uuid.uuid4().hex}"}}}
    r = httpx.post(f"{BASE}/api/webhooks/stripe", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("received") is True
    assert j.get("unmatched") or j.get("ignored")


def test_stripe_webhook_unsupported_event_ignored():
    payload = {"type": "customer.created", "data": {"object": {"id": "cus_x"}}}
    r = httpx.post(f"{BASE}/api/webhooks/stripe", json=payload, timeout=15)
    assert r.status_code == 200
    assert r.json().get("ignored") is True
