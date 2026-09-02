"""Stripe adapter tests — SANDBOX only. Unit-tests the isolated plugin's real logic:
normalization, error mapping, idempotency, and REAL webhook signature/replay verification
(HMAC via Stripe's own construct_event). Network calls are simulated by monkeypatching the SDK;
webhook crypto is genuine. No core payment-engine changes. Run serially: `pytest tests/ -n0`.
"""
import hashlib
import hmac
import json
import time

import pytest
import stripe

from app.providers.contracts import ChargeRequest, ProviderConfiguration, ProviderError
from app.providers.registry import get_provider, has_provider, list_providers
from app.providers.stripe_provider import StripeProvider, _STATUS_MAP

WH_SECRET = "whsec_test_secret_abc123"


@pytest.fixture
def sp(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WH_SECRET)
    return StripeProvider()


def _charge(**kw):
    return ChargeRequest(amount_minor=kw.get("amount", 5000), currency=kw.get("currency", "USD"),
                         reference=kw.get("ref", "T1"), idempotency_key=kw.get("idem", "idem-1"),
                         metadata={"country": "US"})


def _sign(payload: bytes, secret=WH_SECRET, ts=None):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


# 1-3 discovery / capability / credentials
def test_provider_discovery(sp):
    assert has_provider("stripe")
    assert any(c["key"] == "stripe" for c in list_providers())
    assert get_provider("stripe").key == "stripe"


def test_sandbox_only_live_disabled(sp):
    caps = sp.capabilities()
    assert caps["supported_environments"] == ["sandbox"]
    assert caps["live_supported"] is False


def test_required_credentials(sp):
    keys = [c.key for c in sp.required_credentials()]
    assert "api_key" in keys and "webhook_secret" in keys


# 4-5 missing / invalid credentials
def test_missing_credential_fails(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        StripeProvider().create_payment(_charge(), None)
    assert e.value.code == "unconfigured"


def test_invalid_credentials(monkeypatch, sp):
    def boom(**kw):
        raise stripe.error.AuthenticationError("bad key")
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(boom))
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "invalid_credentials"


# 6-7 timeout / network failure
def test_timeout_network_failure(monkeypatch, sp):
    def boom(**kw):
        raise stripe.error.APIConnectionError("connection dropped")
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(boom))
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "network_error" and e.value.retryable is True


# 8-9 4xx / 5xx
def test_provider_4xx(monkeypatch, sp):
    def boom(**kw):
        raise stripe.error.InvalidRequestError("bad param", "amount")
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(boom))
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "invalid_request"


def test_provider_5xx(monkeypatch, sp):
    def boom(**kw):
        raise stripe.error.APIError("server error")
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(boom))
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "provider_error" and e.value.retryable is True


# 10 malformed
def test_malformed_response(monkeypatch, sp):
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(lambda **kw: {"id": "pi_1"}))  # no status
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "malformed_response"


# 11 success
def test_successful_sandbox_payment(monkeypatch, sp):
    monkeypatch.setattr(stripe.PaymentIntent, "create",
                        staticmethod(lambda **kw: {"id": "pi_ok", "status": "succeeded"}))
    r = sp.create_payment(_charge(), None)
    assert r.success and r.status == "succeeded" and r.provider_txn_id == "pi_ok"
    assert "api_key" not in json.dumps(r.raw) and "sk_" not in json.dumps(r.raw)


# 12 declined
def test_declined_payment(monkeypatch, sp):
    def boom(**kw):
        raise stripe.error.CardError("declined", "card", "card_declined")
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(boom))
    r = sp.create_payment(_charge(), None)
    assert r.success is False and r.status == "failed"


# 13 pending
def test_pending_payment(monkeypatch, sp):
    monkeypatch.setattr(stripe.PaymentIntent, "create",
                        staticmethod(lambda **kw: {"id": "pi_p", "status": "requires_action"}))
    r = sp.create_payment(_charge(), None)
    assert r.success and r.status == "pending"


# 14 idempotency + no blind retry
def test_idempotency_key_passed_and_no_retry(monkeypatch, sp):
    captured = {}
    def cap(**kw):
        captured.update(kw)
        return {"id": "pi_i", "status": "succeeded"}
    monkeypatch.setattr(stripe.PaymentIntent, "create", staticmethod(cap))
    sp.create_payment(_charge(idem="unique-key-xyz"), None)
    assert captured["idempotency_key"] == "unique-key-xyz"
    assert stripe.max_network_retries == 0  # never blind-retry a charge


# 15-18 webhooks (real signature crypto)
def _event(pid="pi_ok", status="succeeded", etype="payment_intent.succeeded", eid="evt_1"):
    return json.dumps({"id": eid, "type": etype,
                       "data": {"object": {"object": "payment_intent", "id": pid, "status": status}}}).encode()


def test_valid_webhook(sp):
    payload = _event()
    ev = sp.verify_callback(payload, {"Stripe-Signature": _sign(payload)}, "sandbox")
    assert ev.event_type == "payment_intent.succeeded"
    assert ev.provider_txn_id == "pi_ok" and ev.normalized_status == "succeeded"
    assert ev.raw["event_id"] == "evt_1"


def test_invalid_webhook_signature(sp):
    payload = _event()
    with pytest.raises(ProviderError) as e:
        sp.verify_callback(payload, {"Stripe-Signature": "t=1,v1=deadbeef"}, "sandbox")
    assert e.value.code == "invalid_signature"


def test_replayed_webhook_rejected(sp):
    payload = _event()
    stale = _sign(payload, ts=int(time.time()) - 4000)  # beyond Stripe's default tolerance
    with pytest.raises(ProviderError) as e:
        sp.verify_callback(payload, {"Stripe-Signature": stale}, "sandbox")
    assert e.value.code == "invalid_signature"


def test_duplicate_event_id_stable_for_dedupe(sp):
    payload = _event(eid="evt_dup")
    h = {"Stripe-Signature": _sign(payload)}
    a = sp.verify_callback(payload, h, "sandbox")
    b = sp.verify_callback(payload, dict(h), "sandbox")
    assert a.raw["event_id"] == b.raw["event_id"] == "evt_dup"  # platform dedupes on this id


# 19 unknown event
def test_unknown_webhook_event(sp):
    payload = json.dumps({"id": "evt_u", "type": "customer.created",
                          "data": {"object": {"object": "customer", "id": "cus_1"}}}).encode()
    ev = sp.verify_callback(payload, {"Stripe-Signature": _sign(payload)}, "sandbox")
    assert ev.event_type == "customer.created" and ev.normalized_status is None


# 20 status synchronization
def test_status_synchronization(monkeypatch, sp):
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve",
                        staticmethod(lambda pid, **kw: {"id": pid, "status": "processing"}))
    st = sp.get_payment_status("pi_x", None)
    assert st.normalized_status == "pending" and st.provider_txn_id == "pi_x"


# 21 normalization correctness (state mapping used by the existing state machine)
def test_status_mapping_normalization():
    assert _STATUS_MAP["succeeded"] == "succeeded"
    assert _STATUS_MAP["canceled"] == "cancelled"
    assert _STATUS_MAP["requires_action"] == "pending"


# 23 environment isolation — live key rejected
def test_live_key_rejected(monkeypatch, sp):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_should_not_be_used")
    with pytest.raises(ProviderError) as e:
        sp.create_payment(_charge(), None)
    assert e.value.code == "live_disabled"


# 24-25 no credential leakage in capabilities/health/results
def test_no_secret_leakage(monkeypatch, sp):
    blob = json.dumps(sp.capabilities()) + json.dumps(sp.health_check("sandbox"))
    for leak in ("sk_test", "sk_live", "whsec_", "api_key\":\"sk"):
        assert leak not in blob


def test_credentials_from_config_not_env(monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.setattr(stripe.PaymentIntent, "create",
                        staticmethod(lambda **kw: {"id": "pi_cfg", "status": "succeeded"}))
    cfg = ProviderConfiguration(provider_key="stripe", mode="sandbox",
                                options={"credentials": {"api_key": "sk_test_cfg"}})
    r = StripeProvider().create_payment(_charge(), cfg)
    assert r.success and r.provider_txn_id == "pi_cfg"
