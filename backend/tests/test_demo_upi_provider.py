"""Tests for the NEW Demo UPI sandbox-only provider plugin (additive).

Verifies: registered via the existing registry, sandbox-only (live rejected), UPI intent/QR
simulation via the inherited generic contract, and demo app-choice metadata — all WITHOUT any
CloudPay core payment-engine change.
"""
from app.providers.registry import get_provider, has_provider, list_providers
from app.providers.contracts import ChargeRequest, PaymentFlow


def _req(amount_minor=25000, method="upi"):
    return ChargeRequest(amount_minor=amount_minor, currency="INR", reference="DEMO-1",
                         idempotency_key="k1", metadata={"method": method})


def test_registered_and_capabilities():
    assert has_provider("demo_upi")
    p = get_provider("demo_upi")
    caps = p.capabilities()
    assert caps["key"] == "demo_upi"
    assert caps.get("demo") is True and caps.get("sandbox_only") is True
    assert "INR" in caps["supported_currencies"]
    labels = {a["key"] for a in caps["upi_apps"]}
    assert {"phonepe", "gpay", "paytm", "bhim", "qr"}.issubset(labels)
    assert caps["supported_environments"] == ["sandbox"]
    # discoverable in the standard registry listing
    assert any(c["key"] == "demo_upi" for c in list_providers())


def test_live_mode_rejected_sandbox_ok():
    p = get_provider("demo_upi")
    assert p.supports_environment("sandbox") is True
    assert p.supports_environment("live") is False
    assert p.health_check("live")["status"] == "unsupported_environment"
    assert p.health_check("sandbox")["status"] == "up"


def test_upi_intent_and_qr_simulation():
    p = get_provider("demo_upi")
    intent = p.generate_intent(_req())
    assert intent.intent_id.startswith("mock_upi_")  # inherited simulated UPI lifecycle
    assert intent.client_token.startswith("upi://pay")
    assert intent.raw.get("simulated") is True and intent.raw.get("rail") == "upi"
    qr = p.generate_qr(_req())
    assert qr.qr_payload.startswith("upi://pay") and qr.raw.get("simulated") is True


def test_upi_outcome_scenarios():
    p = get_provider("demo_upi")
    # amount minor-unit last two digits drive the simulated outcome (inherited from mock)
    pending = p.generate_intent(_req(amount_minor=20011))  # ..11 -> pending
    assert p.get_payment_status(pending.intent_id).normalized_status == "pending"
    failed = p.generate_intent(_req(amount_minor=20022))   # ..22 -> failed
    assert p.get_payment_status(failed.intent_id).normalized_status == "failed"
    success = p.generate_intent(_req(amount_minor=25000))  # -> success
    assert p.get_payment_status(success.intent_id).normalized_status == "succeeded"


def test_flows_declared():
    p = get_provider("demo_upi")
    assert PaymentFlow.INTENT in p.supported_flows and PaymentFlow.QR in p.supported_flows
