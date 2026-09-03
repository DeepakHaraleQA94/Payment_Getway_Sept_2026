"""Focused tests for the NEW customer payment receipt email (additive).

Exercises `payment_receipt_service.send_payment_receipt` directly with a stubbed email
provider + fake DB (no live server, no real email send). Verifies: sends once on final
success, idempotent against replays/retries, skipped without a customer email or non-final
status, never aborts on send failure, and never leaks secrets.
"""
import asyncio
import types
from datetime import datetime, timezone

import pytest

from app.services import email_service, payment_receipt_service


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


class _FakeDB:
    def __init__(self, tenant):
        self._tenant = tenant

    async def execute(self, _query):
        return _FakeResult(self._tenant)


def _tenant(name="Acme Corp", contact_email="support@acme.test"):
    return types.SimpleNamespace(name=name, contact_email=contact_email)


def _payment(**kw):
    p = types.SimpleNamespace(
        id="pid-123", status="succeeded", customer_email="cust@example.com",
        metadata_json={}, reference="ORD-77", amount_minor=125000, currency="USD",
        provider_txn_id="ptxn_abc", created_at=datetime.now(timezone.utc), tenant_id="tid-1")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


@pytest.fixture
def capture_email(monkeypatch):
    sent = []

    def _fake_send(*, to, subject, body, attachment_url=None, attachment=None, html=None):
        sent.append({"to": to, "subject": subject, "body": body, "html": html})
        return {"provider": "test", "delivered": True, "status": "sent", "id": "mid-1"}

    monkeypatch.setattr(email_service, "send_email", _fake_send)
    return sent


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_sends_receipt_on_success(capture_email):
    p = _payment()
    result = _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
    assert result and result["status"] == "sent"
    assert len(capture_email) == 1
    msg = capture_email[0]
    assert msg["to"] == "cust@example.com"
    assert "ORD-77" in msg["subject"]
    assert "CloudPay" in msg["html"] and "1,250.00 USD" in msg["html"]
    assert "support@acme.test" in msg["html"]  # tenant contact surfaced
    # idempotency marker persisted on the payment metadata
    assert p.metadata_json.get("receipt_sent_at")
    assert p.metadata_json.get("receipt_status") == "sent"


def test_idempotent_no_duplicate_on_replay(capture_email):
    p = _payment()
    db = _FakeDB(_tenant())
    _run(payment_receipt_service.send_payment_receipt(db, payment=p))
    # replay / retry (e.g. webhook redelivery) must not send again
    second = _run(payment_receipt_service.send_payment_receipt(db, payment=p))
    assert second is None
    assert len(capture_email) == 1


def test_captured_status_also_sends(capture_email):
    p = _payment(status="captured", reference="CAP-9")
    _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
    assert len(capture_email) == 1
    assert "captured".capitalize() in capture_email[0]["html"]


def test_skipped_when_no_customer_email(capture_email):
    p = _payment(customer_email=None)
    result = _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
    assert result is None
    assert len(capture_email) == 0


def test_skipped_when_status_not_final(capture_email):
    for status in ("pending", "authorized", "failed", "cancelled", "reversed"):
        p = _payment(status=status)
        result = _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
        assert result is None
    assert len(capture_email) == 0


def test_no_support_block_without_contact(capture_email):
    p = _payment()
    _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant(contact_email=None)), payment=p))
    assert len(capture_email) == 1
    assert "Questions" not in capture_email[0]["html"]


def test_send_failure_does_not_mark_and_never_raises(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "send_email", _boom)
    p = _payment()
    # must not raise, and must NOT mark receipt_sent_at (so a later retry can re-attempt)
    result = _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
    assert result is None
    assert "receipt_sent_at" not in p.metadata_json


def test_transient_send_failed_status_allows_retry(monkeypatch):
    calls = {"n": 0}

    def _flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"provider": "resend", "delivered": False, "status": "send_failed"}
        return {"provider": "resend", "delivered": True, "status": "sent", "id": "mid"}

    monkeypatch.setattr(email_service, "send_email", _flaky)
    p = _payment()
    db = _FakeDB(_tenant())
    _run(payment_receipt_service.send_payment_receipt(db, payment=p))
    assert "receipt_sent_at" not in p.metadata_json  # transient failure not marked
    _run(payment_receipt_service.send_payment_receipt(db, payment=p))  # retry succeeds
    assert p.metadata_json.get("receipt_status") == "sent"
    assert calls["n"] == 2


def test_receipt_html_has_no_secrets(capture_email):
    p = _payment()
    _run(payment_receipt_service.send_payment_receipt(_FakeDB(_tenant()), payment=p))
    html = capture_email[0]["html"].lower()
    for banned in ("api_key", "resend", "secret", "password", "bearer", "sk_test", "credential"):
        assert banned not in html
