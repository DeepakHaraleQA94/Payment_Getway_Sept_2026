"""Focused tests for the NEW Payment Capture & Void capability (additive).

Runs against the live server like the rest of the suite. Uses the Mock provider's additive
manual-capture flow (metadata.capture_mode='manual') to obtain AUTHORIZED payments deterministically
without changing any existing create behavior.
"""
import concurrent.futures
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "finance@vortexglobal.info")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def _login(email, password):
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return _cookie(r, "access_token")


def _client(token):
    return httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(scope="module")
def admin_client():
    c = _client(_login(ADMIN_EMAIL, ADMIN_PASSWORD))
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme_id(admin_client):
    r = admin_client.get("/api/tenants")
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


def _auth_payment(admin_client, acme_id, amount=10000):
    """Create an AUTHORIZED payment via the Mock manual-capture flow."""
    r = admin_client.post(f"/api/payments?tenant_id={acme_id}", json={
        "reference": f"AUTH-{uuid.uuid4().hex[:8]}", "amount_minor": amount, "currency": "USD",
        "provider_key": "mock", "idempotency_key": f"ik-{uuid.uuid4().hex[:10]}",
        "metadata": {"capture_mode": "manual"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "authorized", body
    return body


def _ledger_credits(admin_client, acme_id, payment_id):
    le = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
    return [e for e in le if e["ref_type"] == "payment" and e["ref_id"] == payment_id
            and e["direction"] == "credit"]


class TestCapture:
    def test_capture_authorized(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        r = admin_client.post(f"/api/payments/{p['id']}/capture", json={})
        assert r.status_code == 200 and r.json()["status"] == "captured"

    def test_duplicate_capture_rejected(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        assert admin_client.post(f"/api/payments/{p['id']}/capture", json={}).status_code == 200
        r2 = admin_client.post(f"/api/payments/{p['id']}/capture", json={})
        assert r2.status_code == 400 and "authorized" in r2.text.lower()

    def test_capture_idempotent_by_key(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        key = f"cap-{uuid.uuid4().hex[:8]}"
        r1 = admin_client.post(f"/api/payments/{p['id']}/capture", json={"idempotency_key": key})
        r2 = admin_client.post(f"/api/payments/{p['id']}/capture", json={"idempotency_key": key})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["status"] == "captured" and r2.json()["status"] == "captured"

    def test_capture_beyond_authorized_rejected(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id, amount=10000)
        r = admin_client.post(f"/api/payments/{p['id']}/capture", json={"amount_minor": 20000})
        assert r.status_code == 400 and "exceeds" in r.text.lower()

    def test_capture_non_authorized_rejected(self, admin_client, acme_id):
        # A normal (immediately succeeded) payment is not authorized -> capture rejected
        r = admin_client.post(f"/api/payments?tenant_id={acme_id}", json={
            "reference": f"OK-{uuid.uuid4().hex[:8]}", "amount_minor": 5000, "currency": "USD",
            "provider_key": "mock", "idempotency_key": f"ik-{uuid.uuid4().hex[:10]}"})
        pid = r.json()["id"]
        assert r.json()["status"] == "succeeded"
        cr = admin_client.post(f"/api/payments/{pid}/capture", json={})
        assert cr.status_code == 400 and "authorized" in cr.text.lower()

    def test_capture_no_duplicate_ledger_credit(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        # authorized create already posted exactly one credit
        assert len(_ledger_credits(admin_client, acme_id, p["id"])) == 1
        admin_client.post(f"/api/payments/{p['id']}/capture", json={})
        # capture must NOT add a second credit
        assert len(_ledger_credits(admin_client, acme_id, p["id"])) == 1

    def test_capture_after_void_rejected(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        assert admin_client.post(f"/api/payments/{p['id']}/void", json={}).status_code == 200
        r = admin_client.post(f"/api/payments/{p['id']}/capture", json={})
        assert r.status_code == 400 and "authorized" in r.text.lower()


class TestVoid:
    def test_void_authorized(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        r = admin_client.post(f"/api/payments/{p['id']}/void", json={"reason": "test"})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"

    def test_duplicate_void_rejected(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        assert admin_client.post(f"/api/payments/{p['id']}/void", json={}).status_code == 200
        r2 = admin_client.post(f"/api/payments/{p['id']}/void", json={})
        assert r2.status_code == 400 and "authorized" in r2.text.lower()

    def test_void_idempotent_by_key(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        key = f"void-{uuid.uuid4().hex[:8]}"
        r1 = admin_client.post(f"/api/payments/{p['id']}/void", json={"idempotency_key": key})
        r2 = admin_client.post(f"/api/payments/{p['id']}/void", json={"idempotency_key": key})
        assert r1.status_code == 200 and r2.status_code == 200 and r2.json()["status"] == "cancelled"

    def test_void_after_capture_rejected(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        assert admin_client.post(f"/api/payments/{p['id']}/capture", json={}).status_code == 200
        r = admin_client.post(f"/api/payments/{p['id']}/void", json={})
        assert r.status_code == 400 and "authorized" in r.text.lower()

    def test_void_non_authorized_rejected(self, admin_client, acme_id):
        r = admin_client.post(f"/api/payments?tenant_id={acme_id}", json={
            "reference": f"OK-{uuid.uuid4().hex[:8]}", "amount_minor": 5000, "currency": "USD",
            "provider_key": "mock", "idempotency_key": f"ik-{uuid.uuid4().hex[:10]}"})
        pid = r.json()["id"]
        vr = admin_client.post(f"/api/payments/{pid}/void", json={})
        assert vr.status_code == 400 and "authorized" in vr.text.lower()

    def test_void_unwinds_credit_no_money_created(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id, amount=10000)
        credit = _ledger_credits(admin_client, acme_id, p["id"])[0]["amount_minor"]
        admin_client.post(f"/api/payments/{p['id']}/void", json={})
        le = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        debit = next((e for e in le if e["ref_type"] == "void" and e["ref_id"] == p["id"]), None)
        assert debit is not None and debit["direction"] == "debit" and debit["amount_minor"] == credit


class TestConcurrencyAndRbac:
    def test_concurrent_capture_void_safety(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        pid = p["id"]
        token = admin_client.headers["Authorization"].split(" ", 1)[1]

        def _op(kind):
            with _client(token) as c:
                return c.post(f"/api/payments/{pid}/{kind}", json={}).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            codes = list(ex.map(_op, ["capture", "void"]))
        # Exactly one wins; the other is rejected. Payment ends in one terminal-ish state.
        assert codes.count(200) == 1, codes
        final = admin_client.get(f"/api/payments/{pid}").json()["status"]
        assert final in ("captured", "cancelled")

    def test_capture_requires_permission(self, admin_client, acme_id):
        p = _auth_payment(admin_client, acme_id)
        ops = _client(_login("ops-admin@cloudpay.io", ADMIN_PASSWORD))
        try:
            assert ops.post(f"/api/payments/{p['id']}/capture", json={}).status_code == 403
            assert ops.post(f"/api/payments/{p['id']}/void", json={}).status_code == 403
        finally:
            ops.close()

    def test_capture_cross_tenant_rejected(self, admin_client, acme_id):
        # Create a second tenant + a user holding payment.capture/void, then attempt on acme payment.
        suffix = uuid.uuid4().hex[:8]
        tid = admin_client.post("/api/tenants", json={
            "name": f"CapTest {suffix}", "slug": f"captest-{suffix}", "country": "US",
            "default_currency": "USD", "contact_email": f"cap{suffix}@t.io"}).json()["id"]
        role_id = admin_client.post(f"/api/roles?tenant_id={tid}", json={
            "name": "Cap Ops", "description": "x",
            "permission_codes": ["payment.create", "payment.capture", "payment.void"]}).json()["id"]
        email = f"capops-{suffix}@t.io"
        admin_client.post(f"/api/users?tenant_id={tid}", json={
            "email": email, "name": "Cap Ops", "password": "CapOps-Passw0rd!", "role_id": role_id})
        other = _client(_login(email, "CapOps-Passw0rd!"))
        try:
            p = _auth_payment(admin_client, acme_id)
            assert other.post(f"/api/payments/{p['id']}/capture", json={}).status_code == 404
            assert other.post(f"/api/payments/{p['id']}/void", json={}).status_code == 404
        finally:
            other.close()


class TestUnsupportedCapability:
    def test_provider_without_capture_returns_normalized_error(self):
        # A minimal in-process provider that does NOT support capture/void surfaces the normalized
        # unsupported-capability error from the generic contract (no provider-specific logic in core).
        from app.providers.base import PaymentProviderAdapter
        from app.providers.contracts import ProviderError, ProviderResult

        class _NoCap(PaymentProviderAdapter):
            key = "nocap"

            def create_payment(self, req, config=None):
                return ProviderResult(success=True, provider_txn_id="x", status="authorized")

            def refund(self, provider_txn_id, amount_minor, currency, config=None):
                return ProviderResult(success=True, provider_txn_id="r", status="refunded")

        prov = _NoCap()
        assert prov.supports_capture() is False and prov.supports_void() is False
        with pytest.raises(ProviderError) as e1:
            prov.capture("x", 100, "USD")
        assert e1.value.code == "unsupported_capability"
        with pytest.raises(ProviderError) as e2:
            prov.void("x")
        assert e2.value.code == "unsupported_capability"
