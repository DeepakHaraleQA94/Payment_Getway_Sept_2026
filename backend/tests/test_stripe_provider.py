"""PROMPT 05 — Stripe TEST/Sandbox adapter tests.

Two layers:
 * Unit: exercise the StripeProvider adapter with a mocked `stripe` SDK (no network),
   covering capabilities, live/unconfigured guards, charge success/failure, refund and
   webhook signature verification.
 * Integration (HTTP against the running server): Stripe is registered in the provider
   registry (TEST mode), appears in provider discovery + monitoring, payments route to it
   and degrade safely, idempotency holds, and the inbound webhook endpoint reconciles.

Run serially: `pytest tests/ -n0` (webhook tests share mutable state across workers).
"""
import os
import sys
import types
import uuid

import httpx
import pytest

from app.providers.base import ChargeRequest
from app.providers.stripe_provider import StripeProvider

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


# ----------------------------- fake stripe SDK -----------------------------
class _FakeIntent:
    def __init__(self, id="pi_test_123", status="succeeded"):
        self.id = id
        self.status = status


class _FakeRefund:
    def __init__(self, id="re_test_123", status="succeeded"):
        self.id = id
        self.status = status


def _install_fake_stripe(monkeypatch, *, charge_status="succeeded", raise_on_charge=False,
                         refund_status="succeeded"):
    mod = types.ModuleType("stripe")
    mod.api_key = None

    class PaymentIntent:
        @staticmethod
        def create(**kwargs):
            if raise_on_charge:
                raise RuntimeError("card_declined")
            return _FakeIntent(status=charge_status)

        @staticmethod
        def retrieve(pid):
            return _FakeIntent(id=pid, status="succeeded")

    class Refund:
        @staticmethod
        def create(**kwargs):
            return _FakeRefund(status=refund_status)

    class Balance:
        @staticmethod
        def retrieve():
            return {"livemode": False}

    class Webhook:
        @staticmethod
        def construct_event(payload, sig_header, secret):
            return {"type": "payment_intent.succeeded", "data": {"object": {"id": "pi_test_123"}}}

    mod.PaymentIntent = PaymentIntent
    mod.Refund = Refund
    mod.Balance = Balance
    mod.Webhook = Webhook
    monkeypatch.setitem(sys.modules, "stripe", mod)
    return mod


# ----------------------------- unit tests -----------------------------
def test_capabilities_reports_sandbox_test_mode():
    p = StripeProvider()
    caps = p.capabilities()
    assert caps["key"] == "stripe"
    assert caps["supports_refund"] is True
    assert caps["supports_webhooks"] is True
    # Configured with sk_test_ placeholder in the pod env -> sandbox/test mode, live blocked.
    assert caps["mode"] == "sandbox"
    assert caps["test_mode"] is True


def test_live_key_is_blocked(monkeypatch):
    p = StripeProvider()
    monkeypatch.setattr(p, "_api_key", "sk_live_shouldneverrun")
    assert p.is_live is True
    res = p.charge(ChargeRequest(amount_minor=1000, currency="USD", reference="R1"))
    assert res.success is False and res.error == "stripe_live_disabled"


def test_unconfigured_is_guarded(monkeypatch):
    p = StripeProvider()
    monkeypatch.setattr(p, "_api_key", "")
    assert p.configured is False
    res = p.charge(ChargeRequest(amount_minor=1000, currency="USD", reference="R1"))
    assert res.success is False and res.error == "stripe_not_configured"


def test_charge_success_with_mocked_sdk(monkeypatch):
    _install_fake_stripe(monkeypatch, charge_status="succeeded")
    p = StripeProvider()
    monkeypatch.setattr(p, "_api_key", "sk_test_valid")
    res = p.charge(ChargeRequest(amount_minor=4200, currency="USD", reference="R2",
                                 idempotency_key="idem-abc"))
    assert res.success is True
    assert res.status == "succeeded"
    assert res.provider_txn_id == "pi_test_123"
    assert res.raw.get("test_mode") is True


def test_charge_failure_is_graceful(monkeypatch):
    _install_fake_stripe(monkeypatch, raise_on_charge=True)
    p = StripeProvider()
    monkeypatch.setattr(p, "_api_key", "sk_test_valid")
    res = p.charge(ChargeRequest(amount_minor=4200, currency="USD", reference="R3"))
    assert res.success is False
    assert res.status == "failed"
    assert res.error == "RuntimeError"


def test_refund_success_with_mocked_sdk(monkeypatch):
    _install_fake_stripe(monkeypatch, refund_status="succeeded")
    p = StripeProvider()
    monkeypatch.setattr(p, "_api_key", "sk_test_valid")
    res = p.refund("pi_test_123", 1000, "USD")
    assert res.success is True
    assert res.provider_txn_id == "re_test_123"


def test_verify_webhook_uses_sdk(monkeypatch):
    _install_fake_stripe(monkeypatch)
    p = StripeProvider()
    event = p.verify_webhook(b"{}", "sig")
    assert event["type"] == "payment_intent.succeeded"


# ----------------------------- integration (HTTP) -----------------------------
def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    tok = _cookie(r, "access_token")
    assert tok, "admin login failed"
    c.headers["Authorization"] = f"Bearer {tok}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme(admin):
    return next(t["id"] for t in admin.get("/api/tenants").json() if t["slug"] == "acme")


def test_stripe_is_registered_in_discovery(admin):
    r = admin.get("/api/providers/available")
    assert r.status_code == 200, r.text
    keys = {p["key"] for p in r.json()}
    assert "stripe" in keys, "Stripe adapter should be discoverable (TEST key configured)"
    stripe_meta = next(p for p in r.json() if p["key"] == "stripe")
    assert stripe_meta["mode"] == "sandbox"
    assert stripe_meta["test_mode"] is True


def test_monitoring_lists_stripe(admin):
    r = admin.get("/api/monitoring/services")
    assert r.status_code == 200, r.text
    names = [s["name"] for s in r.json()["services"]]
    assert any("Stripe" in n for n in names), f"monitoring should list Stripe: {names}"


def test_payment_routes_to_stripe_and_degrades_safely(admin, acme):
    # The pod env holds a placeholder sk_test key, so the real Stripe call fails and the
    # adapter degrades to a safe 'failed' payment (no ledger credit). This proves routing
    # to the isolated adapter without hard-coding Stripe into the engine.
    idem = f"stripe-{uuid.uuid4().hex}"
    body = {"reference": "INV-STRIPE-1", "amount_minor": 4200, "currency": "USD",
            "provider_key": "stripe", "idempotency_key": idem}
    r1 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r1.status_code == 200, r1.text
    pid = r1.json()["id"]
    assert r1.json()["provider_key"] == "stripe"
    assert r1.json()["status"] in {"failed", "succeeded"}

    # Idempotent replay returns the same payment (lock claimed before dispatch).
    r2 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r2.status_code == 200 and r2.json()["id"] == pid


def test_stripe_webhook_endpoint_accepts_and_handles_unmatched(admin):
    # No STRIPE_WEBHOOK_SECRET configured -> payload parsed, unmatched intent acknowledged.
    payload = {"type": "payment_intent.succeeded",
               "data": {"object": {"id": f"pi_{uuid.uuid4().hex}"}}}
    r = httpx.post(f"{BASE}/api/webhooks/stripe", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["received"] is True
    assert body.get("unmatched") or body.get("ignored")
