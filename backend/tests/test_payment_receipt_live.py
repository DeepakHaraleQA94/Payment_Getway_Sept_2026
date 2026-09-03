"""Live-API verification of the customer payment receipt feature.

Verifies that the metadata.receipt_sent_at + receipt_status markers are set (or absent)
across the flows the review requests: direct-success, no-customer_email skip, manual
capture, idempotent capture retry, and no secret leakage in payment responses.

Uses customer_email='finance@vortexglobal.info' (a verified/controlled mailbox on the
Resend-configured domain) per the review note.
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "finance@vortexglobal.info")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
CUST_EMAIL = "finance@vortexglobal.info"  # controlled mailbox on verified domain

_BANNED_SECRETS = ("api_key", "secret", "password", "bearer ", "sk_test", "sk_live",
                   "credential", "resend_api")


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


def _login():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        return _cookie(r, "access_token")


@pytest.fixture(scope="module")
def admin_client():
    tok = _login()
    c = httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {tok}"})
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme_id(admin_client):
    r = admin_client.get("/api/tenants")
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


def _assert_no_secrets(body_text: str):
    lower = body_text.lower()
    for banned in _BANNED_SECRETS:
        assert banned not in lower, f"secret-like token '{banned}' leaked in response"


def _create_payment(client, acme_id, *, customer_email=None, metadata=None, amount=7500):
    payload = {
        "reference": f"RCPT-{uuid.uuid4().hex[:8]}",
        "amount_minor": amount, "currency": "USD",
        "provider_key": "mock", "idempotency_key": f"ik-{uuid.uuid4().hex[:10]}",
    }
    if customer_email:
        payload["customer_email"] = customer_email
    if metadata:
        payload["metadata"] = metadata
    r = client.post(f"/api/payments?tenant_id={acme_id}", json=payload)
    assert r.status_code == 200, r.text
    return r.json(), r.text


# ---- Success-path (direct succeed) --------------------------------------------------

def test_direct_success_with_customer_email_sets_receipt_marker(admin_client, acme_id):
    body, raw = _create_payment(admin_client, acme_id, customer_email=CUST_EMAIL)
    assert body["status"] == "succeeded"
    md = body.get("metadata") or {}
    assert md.get("receipt_sent_at"), f"receipt_sent_at missing: {md}"
    assert md.get("receipt_status") in ("sent", "noop"), md
    _assert_no_secrets(raw)


def test_direct_success_without_customer_email_no_receipt(admin_client, acme_id):
    body, _ = _create_payment(admin_client, acme_id, customer_email=None)
    assert body["status"] == "succeeded"
    md = body.get("metadata") or {}
    assert "receipt_sent_at" not in md, md
    assert "receipt_status" not in md, md


# ---- Manual-capture flow + idempotency -----------------------------------------------

def test_manual_capture_sends_receipt_and_is_idempotent(admin_client, acme_id):
    body, _ = _create_payment(
        admin_client, acme_id,
        customer_email=CUST_EMAIL,
        metadata={"capture_mode": "manual"},
    )
    assert body["status"] == "authorized"
    # authorized should NOT have a receipt yet
    assert "receipt_sent_at" not in (body.get("metadata") or {})

    pid = body["id"]
    cap_key = f"cap-{uuid.uuid4().hex[:8]}"
    r1 = admin_client.post(f"/api/payments/{pid}/capture", json={"idempotency_key": cap_key})
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["status"] == "captured"
    first_marker = (j1.get("metadata") or {}).get("receipt_sent_at")
    assert first_marker, f"receipt_sent_at missing after capture: {j1.get('metadata')}"

    # Repeat capture with SAME idempotency_key: no duplicate receipt, marker unchanged
    r2 = admin_client.post(f"/api/payments/{pid}/capture", json={"idempotency_key": cap_key})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["status"] == "captured"
    second_marker = (j2.get("metadata") or {}).get("receipt_sent_at")
    assert second_marker == first_marker, (first_marker, second_marker)


def test_payment_response_never_leaks_secrets(admin_client, acme_id):
    _, raw = _create_payment(admin_client, acme_id, customer_email=CUST_EMAIL)
    _assert_no_secrets(raw)
    # And the GET path
    pid = _create_payment(admin_client, acme_id, customer_email=CUST_EMAIL)[0]["id"]
    r = admin_client.get(f"/api/payments/{pid}")
    assert r.status_code == 200
    _assert_no_secrets(r.text)
