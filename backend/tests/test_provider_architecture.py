"""Generic provider plugin ARCHITECTURE tests (provider-agnostic).

CloudPay core must depend only on the standardized provider contract — never on a specific
provider. These tests verify the contract, registration/discovery, capability interface,
credential-reference interface, health-check interface, sandbox/live-mode abstraction,
provider isolation, and the generic inbound-webhook contract — using ONLY the built-in
Mock dev/test provider. No external provider (Stripe/Razorpay/etc.) is installed or used.

Run serially: `pytest tests/ -n0`.
"""
import os
import uuid

import httpx
import pytest

from app.providers.base import (
    ChargeRequest,
    PaymentProviderAdapter,
    ProviderCredentialField,
    ProviderWebhookEvent,
)
from app.providers.mock import MockProvider
from app.providers import registry

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


# ----------------------------- contract (unit) -----------------------------
def test_mock_implements_generic_contract():
    p = MockProvider()
    assert isinstance(p, PaymentProviderAdapter)
    # Contract methods present.
    for attr in ("create_payment", "charge", "refund", "capabilities", "health_check",
                 "required_credentials", "verify_callback", "verify_webhook",
                 "get_payment_status"):
        assert callable(getattr(p, attr))


def test_capability_interface_shape():
    caps = MockProvider().capabilities()
    for key in ("key", "display_name", "mode", "configured", "supported_currencies",
                "payment_methods", "supports_refund", "supports_webhooks", "test_mode",
                "required_credentials"):
        assert key in caps, f"capability missing '{key}'"
    assert caps["key"] == "mock"
    assert isinstance(caps["required_credentials"], list)


def test_credential_reference_interface_holds_no_secrets():
    creds = MockProvider().required_credentials()
    assert creds == []  # mock needs none
    # The field type carries names only, never values.
    f = ProviderCredentialField(key="api_key", label="API Key")
    assert f.secret is True and not hasattr(f, "value")


def test_sandbox_live_mode_abstraction():
    p = MockProvider()
    assert p.mode == "sandbox"
    assert p.is_live is False
    assert p.configured is True
    assert p.capabilities()["test_mode"] is True


def test_health_check_interface():
    h = MockProvider().health_check()
    assert h["status"] == "up"
    assert h["mode"] == "sandbox"


def test_charge_and_refund_contract():
    p = MockProvider()
    ok = p.charge(ChargeRequest(amount_minor=5000, currency="USD", reference="R1"))
    assert ok.success is True and ok.provider_txn_id and ok.status == "succeeded"
    declined = p.charge(ChargeRequest(amount_minor=113, currency="USD", reference="R2"))
    assert declined.success is False and declined.status == "failed"
    rf = p.refund("mock_txn", 1000, "USD")
    assert rf.success is True and rf.status == "succeeded"


def test_verify_webhook_returns_normalized_event():
    ev = MockProvider().verify_webhook(
        b'{"provider_txn_id":"mock_abc","status":"refunded","event_type":"payment.refunded"}', {})
    assert isinstance(ev, ProviderWebhookEvent)
    assert ev.provider_txn_id == "mock_abc"
    assert ev.normalized_status == "refunded"


def test_registry_isolation_and_discovery():
    # Only the built-in mock provider ships with the core.
    assert registry.has_provider("mock")
    assert not registry.has_provider("stripe")
    assert not registry.has_provider("razorpay")
    keys = {c["key"] for c in registry.list_providers()}
    assert keys == {"mock"}, f"core must ship only the mock provider, got {keys}"
    # Unknown provider safely falls back to mock (never a live/unknown provider).
    assert registry.get_provider("nonexistent").key == "mock"


def test_core_has_no_provider_specific_imports():
    # Guard against re-introducing hard-coded providers in the core.
    import app.services.payment_engine as pe
    import app.routers.webhooks as wh
    src = (open(pe.__file__).read() + open(wh.__file__).read()).lower()
    assert "stripe" not in src, "core must not reference a specific provider"
    assert "razorpay" not in src, "core must not reference a specific provider"


# ----------------------------- HTTP integration -----------------------------
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


def test_discovery_endpoint_lists_only_mock(admin):
    r = admin.get("/api/providers/available")
    assert r.status_code == 200, r.text
    keys = {p["key"] for p in r.json()}
    assert keys == {"mock"}, f"discovery must list only mock: {keys}"


def test_capabilities_and_health_endpoints(admin):
    c = admin.get("/api/providers/mock/capabilities")
    assert c.status_code == 200 and c.json()["key"] == "mock"
    h = admin.get("/api/providers/mock/health")
    assert h.status_code == 200 and h.json()["status"] == "up"
    # Unknown provider -> 404 (isolation).
    assert admin.get("/api/providers/stripe/capabilities").status_code == 404


def test_post_provider_rejects_live_mode(admin, acme):
    body = {"provider_key": "acme_live", "display_name": "Acme LIVE",
            "mode": "live", "priority": 10, "enabled": True, "config": {}}
    r = admin.post(f"/api/providers?tenant_id={acme}", json=body)
    assert r.status_code == 400 and "live" in r.text.lower()


def test_generic_inbound_webhook_reconciles_via_contract(admin, acme):
    # Create a succeeded mock payment, then drive the GENERIC provider webhook endpoint.
    body = {"reference": f"INV-WH-{uuid.uuid4().hex[:6]}", "amount_minor": 6000,
            "currency": "USD", "provider_key": "mock", "idempotency_key": f"wh-{uuid.uuid4().hex}"}
    p = admin.post(f"/api/payments?tenant_id={acme}", json=body).json()
    assert p["status"] == "succeeded"
    txn = p["provider_txn_id"]

    # Valid transition succeeded -> refunded, translated by the plugin contract.
    payload = {"provider_txn_id": txn, "status": "refunded", "event_type": "payment.refunded"}
    r = httpx.post(f"{BASE}/api/providers/mock/webhook", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("reconciled") is True
    assert r.json().get("status") == "refunded"

    got = admin.get(f"/api/payments/{p['id']}")
    assert got.status_code == 200 and got.json()["status"] == "refunded"


def test_generic_inbound_webhook_unmatched_and_ignored():
    # Unknown txn -> unmatched.
    r1 = httpx.post(f"{BASE}/api/providers/mock/webhook",
                    json={"provider_txn_id": f"mock_{uuid.uuid4().hex}", "status": "succeeded"},
                    timeout=15)
    assert r1.status_code == 200 and r1.json().get("unmatched") is True
    # No txn id -> ignored.
    r2 = httpx.post(f"{BASE}/api/providers/mock/webhook", json={"event_type": "noop"}, timeout=15)
    assert r2.status_code == 200 and r2.json().get("ignored") is True
    # Unknown provider -> 404.
    r3 = httpx.post(f"{BASE}/api/providers/stripe/webhook", json={}, timeout=15)
    assert r3.status_code == 404


# ----------------------------- expanded contract (unit) -----------------------------
def test_contract_exposes_all_srd_methods():
    p = MockProvider()
    for m in ("create_payment", "get_payment_status", "generate_intent", "generate_qr",
              "verify_callback", "reconcile", "refund"):
        assert callable(getattr(p, m)), f"contract missing {m}"


def test_plugin_is_composed_of_building_blocks():
    from app.providers.contracts import (
        CallbackHandler, ErrorHandler, HealthCheck, ProviderApiClient,
        ProviderAuthentication, RequestMapper, ResponseMapper, StatusMapper,
    )
    p = MockProvider()
    assert isinstance(p._auth, ProviderAuthentication)
    assert isinstance(p._client, ProviderApiClient)
    assert isinstance(p._req, RequestMapper)
    assert isinstance(p._resp, ResponseMapper)
    assert isinstance(p._status, StatusMapper)
    assert isinstance(p._callback, CallbackHandler)
    assert isinstance(p._errors, ErrorHandler)
    assert isinstance(p._health, HealthCheck)


def test_capabilities_include_flows_and_flags():
    caps = MockProvider().capabilities()
    assert set(caps["supported_flows"]) == {"direct", "intent", "qr"}
    assert caps["supports_intent"] is True
    assert caps["supports_qr"] is True
    assert caps["supports_webhooks"] is True


def test_create_payment_status_and_reconcile():
    p = MockProvider()
    ok = p.create_payment(ChargeRequest(amount_minor=5000, currency="USD", reference="R"))
    assert ok.success and ok.status == "succeeded"
    st = p.get_payment_status(ok.provider_txn_id)
    assert st.normalized_status == "succeeded"
    rec = p.reconcile(ok.provider_txn_id)
    assert rec.matched is True and rec.normalized_status == "succeeded"


def test_intent_and_qr_generation():
    p = MockProvider()
    intent = p.generate_intent(ChargeRequest(amount_minor=1000, currency="USD", reference="R"))
    assert intent.intent_id and intent.client_token
    qr = p.generate_qr(ChargeRequest(amount_minor=1000, currency="USD", reference="R"))
    assert qr.qr_id and qr.qr_payload.startswith("mockqr://")
    # QR payload never contains card data.
    assert "card" not in qr.qr_payload.lower()


def test_status_mapper_normalizes_provider_statuses():
    from app.providers.mock import _MockStatusMapper
    sm = _MockStatusMapper()
    assert sm.to_cloudpay_status("declined") == "failed"
    assert sm.to_cloudpay_status("succeeded") == "succeeded"
    assert sm.to_cloudpay_status("requires_confirmation") == "pending"


# ----------------------------- expanded contract (HTTP) -----------------------------
def test_intent_endpoint(admin):
    r = admin.post("/api/providers/mock/intent",
                   json={"amount_minor": 2500, "currency": "USD", "reference": "INT-1"})
    assert r.status_code == 200, r.text
    assert r.json()["intent_id"] and r.json()["client_token"]


def test_qr_endpoint(admin):
    r = admin.post("/api/providers/mock/qr",
                   json={"amount_minor": 2500, "currency": "USD", "reference": "QR-1"})
    assert r.status_code == 200, r.text
    assert r.json()["qr_id"] and r.json()["qr_payload"].startswith("mockqr://")


def test_status_and_reconcile_endpoints(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": f"INV-ST-{uuid.uuid4().hex[:5]}", "amount_minor": 3000,
                         "currency": "USD", "provider_key": "mock",
                         "idempotency_key": f"st-{uuid.uuid4().hex}"}).json()
    txn = p["provider_txn_id"]
    s = admin.get(f"/api/providers/mock/status/{txn}")
    assert s.status_code == 200 and s.json()["status"] == "succeeded"
    rec = admin.post(f"/api/providers/mock/reconcile/{txn}")
    assert rec.status_code == 200 and rec.json()["matched"] is True


def test_flow_endpoints_reject_unknown_provider(admin):
    assert admin.post("/api/providers/stripe/intent", json={"amount_minor": 100}).status_code == 404
    assert admin.post("/api/providers/stripe/qr", json={"amount_minor": 100}).status_code == 404

