"""Payment state-machine & financial-invariant tests (PROMPT 04)."""
import os
import uuid

import httpx
import pytest

from app.services import payment_state

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    c.headers["Authorization"] = f"Bearer {_cookie(r, 'access_token')}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme(admin):
    return next(t["id"] for t in admin.get("/api/tenants").json() if t["slug"] == "acme")


# ---- unit: transition validator ----
def test_valid_transitions_allowed():
    payment_state.validate_transition("pending", "succeeded")
    payment_state.validate_transition("pending", "failed")
    payment_state.validate_transition("succeeded", "partially_refunded")
    payment_state.validate_transition("partially_refunded", "refunded")
    payment_state.validate_transition("succeeded", "succeeded")  # no-op allowed


@pytest.mark.parametrize("current,new", [
    ("succeeded", "pending"),   # terminal-ish backward
    ("failed", "succeeded"),    # failed cannot become succeeded
    ("cancelled", "succeeded"), # cancelled cannot be processed as success
    ("refunded", "partially_refunded"),
])
def test_invalid_transitions_rejected(current, new):
    with pytest.raises(payment_state.InvalidTransition):
        payment_state.validate_transition(current, new)


# ---- API: idempotent creation ----
def test_idempotent_payment_creation(admin, acme):
    idem = f"idem-{uuid.uuid4().hex}"
    body = {"reference": "SM-1", "amount_minor": 5000, "currency": "USD",
            "provider_key": "mock", "idempotency_key": idem}
    r1 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    r2 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # no duplicate payment


def test_no_client_status_injection(admin, acme):
    # Client-supplied 'status' must be ignored; server sets it via the engine.
    r = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "SM-2", "amount_minor": 700, "currency": "USD",
                         "provider_key": "mock", "status": "refunded"})
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded"  # not the injected 'refunded'


def test_sandbox_decline_no_ledger_credit(admin, acme):
    # amount ending in .13 declines; failed payments must not post a credit / net.
    r = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "SM-FAIL", "amount_minor": 113, "currency": "USD", "provider_key": "mock"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["net_minor"] == 0 and body["fee_minor"] == 0


def test_duplicate_and_over_refund(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "SM-RF", "amount_minor": 6000, "currency": "USD", "provider_key": "mock"}).json()
    pid = p["id"]
    idem = f"rf-{uuid.uuid4().hex}"
    r1 = admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 2000, "idempotency_key": idem})
    r2 = admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 2000, "idempotency_key": idem})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # idempotent refund, not duplicated
    # Over-refund beyond remaining balance rejected.
    assert admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 999999}).status_code == 400


def test_full_refund_then_no_further_refund(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "SM-FULL", "amount_minor": 3000, "currency": "USD", "provider_key": "mock"}).json()
    pid = p["id"]
    assert admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 3000}).status_code == 200
    detail = admin.get(f"/api/payments/{pid}").json()
    assert detail["status"] == "refunded"
    # A refunded (terminal) payment cannot be refunded again.
    assert admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 100}).status_code == 400
