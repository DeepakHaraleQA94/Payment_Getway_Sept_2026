"""Routing & Failover + example external provider plugin + simulated UPI flows.

Covers this SRD batch:
 * priority-based routing with failover across healthy provider accounts (provider-agnostic)
 * an ISOLATED example real-provider plugin (sandbox+live, credentials resolved from config)
 * realistic SIMULATED UPI Intent/QR flows on the generic contract (clearly marked simulated)

Run serially: `pytest tests/ -n0`.
"""
import os
import uuid

import httpx
import pytest

from app.providers.contracts import ChargeRequest, ProviderConfiguration
from app.providers.example_provider import ExampleExternalProvider
from app.providers.mock import MockProvider
from app.services.secret_store import EncryptedDbSecretStore, get_secret_store

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


# ----------------------------- secret store selector (unit) -----------------------------
def test_secret_store_backend_is_configurable_encrypted_db():
    assert isinstance(get_secret_store(), EncryptedDbSecretStore)


# ----------------------------- example provider (unit) -----------------------------
def test_example_provider_declares_sandbox_and_live():
    p = ExampleExternalProvider()
    caps = p.capabilities()
    assert caps["supported_environments"] == ["sandbox", "live"]
    assert caps["live_supported"] is True
    assert {c["key"] for c in caps["required_credentials"]} == {"api_key", "api_secret", "webhook_secret"}


def test_example_provider_sandbox_simulates_success():
    p = ExampleExternalProvider()
    cfg = ProviderConfiguration(provider_key="examplepsp", mode="sandbox")
    r = p.create_payment(ChargeRequest(amount_minor=5000, currency="USD", reference="R"), cfg)
    assert r.success and r.status == "succeeded" and r.raw.get("simulated") is True


def test_example_provider_live_requires_credentials():
    p = ExampleExternalProvider()
    no_creds = ProviderConfiguration(provider_key="examplepsp", mode="live")
    r = p.create_payment(ChargeRequest(amount_minor=5000, currency="USD", reference="R"), no_creds)
    assert r.success is False and r.error == "missing_live_credentials"
    with_creds = ProviderConfiguration(provider_key="examplepsp", mode="live",
                                       options={"credentials": {"api_key": "k", "api_secret": "s"}})
    r2 = p.create_payment(ChargeRequest(amount_minor=5000, currency="USD", reference="R"), with_creds)
    assert r2.success is True


# ----------------------------- UPI simulation (unit) -----------------------------
def test_mock_upi_intent_and_qr_are_simulated():
    p = MockProvider()
    req = ChargeRequest(amount_minor=15000, currency="INR", reference="UPI-1", metadata={"method": "upi"})
    intent = p.generate_intent(req)
    assert intent.client_token.startswith("upi://pay")
    assert intent.raw.get("simulated") is True and intent.raw.get("rail") == "upi"
    qr = p.generate_qr(req)
    assert qr.qr_payload.startswith("upi://pay")
    assert "card" not in qr.qr_payload.lower()


def test_mock_upi_status_lifecycle():
    p = MockProvider()

    def status_for(amount):
        i = p.generate_intent(ChargeRequest(amount_minor=amount, currency="INR", reference="U",
                                            metadata={"method": "upi"}))
        return p.get_payment_status(i.intent_id).normalized_status, \
            p.get_payment_status(i.intent_id).raw.get("upi_state")

    assert status_for(10011) == ("pending", "pending")
    assert status_for(10022) == ("failed", "failed")
    assert status_for(10033) == ("failed", "expired")
    assert status_for(10044) == ("cancelled", "cancelled")
    assert status_for(10000) == ("succeeded", "success")


# ----------------------------- HTTP fixtures -----------------------------
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
def routed_tenant(admin):
    slug = f"route-{uuid.uuid4().hex[:8]}"
    tid = admin.post("/api/tenants", json={"name": "Routing Co", "slug": slug, "default_currency": "USD"}).json()["id"]
    # Two sandbox accounts: mock (priority 10) then example PSP (priority 20).
    admin.post(f"/api/providers?tenant_id={tid}",
               json={"provider_key": "mock", "display_name": "Mock", "mode": "sandbox",
                     "enabled": True, "priority": 10, "supported_currencies": ["USD"]})
    admin.post(f"/api/providers?tenant_id={tid}",
               json={"provider_key": "examplepsp", "display_name": "Example PSP", "mode": "sandbox",
                     "enabled": True, "priority": 20, "supported_currencies": ["USD"]})
    return tid


# ----------------------------- routing & failover (HTTP) -----------------------------
def test_auto_routing_uses_highest_priority(admin, routed_tenant):
    r = admin.post(f"/api/payments?tenant_id={routed_tenant}",
                   json={"reference": "RT-1", "amount_minor": 5000, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"rt-{uuid.uuid4().hex}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"
    assert r.json()["provider_key"] == "mock"  # priority 10 wins


def test_auto_routing_fails_over_to_next_provider(admin, routed_tenant):
    # Amount ending in 13 makes the mock decline -> failover to the example PSP (priority 20).
    r = admin.post(f"/api/payments?tenant_id={routed_tenant}",
                   json={"reference": "RT-2", "amount_minor": 5013, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"rt-{uuid.uuid4().hex}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"
    assert r.json()["provider_key"] == "examplepsp"  # failed over
    attempts = r.json()["metadata"]["routing_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["provider_key"] == "mock" and attempts[0]["success"] is False
    assert attempts[1]["provider_key"] == "examplepsp" and attempts[1]["success"] is True


def test_auto_routing_no_accounts_errors(admin):
    slug = f"empty-{uuid.uuid4().hex[:8]}"
    tid = admin.post("/api/tenants", json={"name": "Empty Co", "slug": slug, "default_currency": "USD"}).json()["id"]
    r = admin.post(f"/api/payments?tenant_id={tid}",
                   json={"reference": "RT-3", "amount_minor": 5000, "currency": "USD",
                         "provider_key": "auto", "environment": "sandbox",
                         "idempotency_key": f"rt-{uuid.uuid4().hex}"})
    assert r.status_code == 400 and "no healthy provider" in r.text.lower()


# ----------------------------- example provider via routing (HTTP) -----------------------------
def test_example_provider_is_discoverable_with_live(admin):
    caps = {p["key"]: p for p in admin.get("/api/providers/available").json()}
    assert "examplepsp" in caps
    assert caps["examplepsp"]["live_supported"] is True
    assert caps["examplepsp"]["supported_environments"] == ["sandbox", "live"]


# ----------------------------- UPI flows via endpoints (HTTP) -----------------------------
def test_upi_intent_and_qr_endpoints(admin):
    intent = admin.post("/api/providers/mock/intent",
                        json={"amount_minor": 25000, "currency": "INR", "reference": "UPI-E", "method": "upi"})
    assert intent.status_code == 200
    assert intent.json()["client_token"].startswith("upi://pay")
    qr = admin.post("/api/providers/mock/qr",
                    json={"amount_minor": 25000, "currency": "INR", "reference": "UPI-E", "method": "upi"})
    assert qr.status_code == 200 and qr.json()["qr_payload"].startswith("upi://pay")


def test_upi_status_endpoint_reports_state(admin):
    intent = admin.post("/api/providers/mock/intent",
                        json={"amount_minor": 20011, "currency": "INR", "reference": "UPI-P", "method": "upi"})
    txn = intent.json()["intent_id"]
    s = admin.get(f"/api/providers/mock/status/{txn}")
    assert s.status_code == 200 and s.json()["status"] == "pending"
