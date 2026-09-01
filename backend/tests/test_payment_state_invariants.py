"""Supplemental invariant tests for PROMPT 04 payment state-machine.

Verifies (beyond test_payment_state.py) the ledger + audit invariants:
 - successful payment posts exactly ONE ledger credit of net_minor (ref_type=payment)
 - idempotent replay does NOT add extra ledger entries
 - successful refund posts exactly ONE ledger debit (ref_type=refund)
 - failed sandbox payment posts NO ledger credit
 - audit entries for payment.create / refund.create carry previous_state, new_state,
   correlation_id, and do NOT expose sensitive card / customer secrets.
"""
import os
import re
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

SENSITIVE_PATTERNS = [
    re.compile(r"\b4\d{15}\b"),          # bare 16-digit PAN starting with 4
    re.compile(r"\bcvv\b", re.I),
    re.compile(r"\bcvc\b", re.I),
    re.compile(r"card_number", re.I),
    re.compile(r"pan\b", re.I),
]


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


def _ledger_ids_by_ref(admin, tenant_id, ref_type):
    r = admin.get(f"/api/ledger/entries?tenant_id={tenant_id}")
    assert r.status_code == 200, r.text
    return {e["id"] for e in r.json() if e.get("ref_type") == ref_type}


def test_success_payment_posts_exactly_one_credit_and_is_idempotent(admin, acme):
    before = _ledger_ids_by_ref(admin, acme, "payment")
    idem = f"idem-{uuid.uuid4().hex}"
    body = {"reference": "INV-LEDGER-1", "amount_minor": 4200, "currency": "USD",
            "provider_key": "mock", "idempotency_key": idem}
    r1 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r1.status_code == 200, r1.text
    pid = r1.json()["id"]
    net1 = r1.json()["net_minor"]
    assert r1.json()["status"] == "succeeded"

    after1_ids = _ledger_ids_by_ref(admin, acme, "payment")
    new_ids = after1_ids - before
    assert len(new_ids) == 1, f"expected 1 new payment ledger entry, got {len(new_ids)}"

    # Verify the new entry is a credit with amount = net_minor
    all_entries = admin.get(f"/api/ledger/entries?tenant_id={acme}").json()
    new_entry = next(e for e in all_entries if e["id"] in new_ids)
    assert new_entry["direction"] == "credit"
    assert new_entry["amount_minor"] == net1
    assert new_entry["ref_type"] == "payment"

    # Idempotent replay must NOT add ledger entries.
    r2 = admin.post(f"/api/payments?tenant_id={acme}", json=body)
    assert r2.status_code == 200 and r2.json()["id"] == pid
    after2_ids = _ledger_ids_by_ref(admin, acme, "payment")
    assert after2_ids == after1_ids, "idempotent replay must not add ledger entries"


def test_failed_payment_posts_no_ledger_credit(admin, acme):
    before = _ledger_ids_by_ref(admin, acme, "payment")
    r = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "INV-FAIL-LEDGER", "amount_minor": 213,
                         "currency": "USD", "provider_key": "mock"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert r.json()["net_minor"] == 0 and r.json()["fee_minor"] == 0
    after = _ledger_ids_by_ref(admin, acme, "payment")
    assert after == before, "failed payment must not post a ledger credit"


def test_refund_posts_single_debit_and_is_idempotent(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "INV-RF-LEDGER", "amount_minor": 8000,
                         "currency": "USD", "provider_key": "mock"}).json()
    pid = p["id"]
    before = _ledger_ids_by_ref(admin, acme, "refund")
    idem = f"rf-{uuid.uuid4().hex}"
    r1 = admin.post(f"/api/payments/{pid}/refunds",
                    json={"amount_minor": 3000, "idempotency_key": idem})
    assert r1.status_code == 200, r1.text
    rid = r1.json()["id"]
    after1 = _ledger_ids_by_ref(admin, acme, "refund")
    new_ids = after1 - before
    assert len(new_ids) == 1

    all_entries = admin.get(f"/api/ledger/entries?tenant_id={acme}").json()
    new_entry = next(e for e in all_entries if e["id"] in new_ids)
    assert new_entry["direction"] == "debit"
    assert new_entry["amount_minor"] == 3000
    assert new_entry["ref_type"] == "refund"

    r2 = admin.post(f"/api/payments/{pid}/refunds",
                    json={"amount_minor": 3000, "idempotency_key": idem})
    assert r2.status_code == 200 and r2.json()["id"] == rid
    after2 = _ledger_ids_by_ref(admin, acme, "refund")
    assert after2 == after1, "idempotent refund must not duplicate ledger debit"


def test_audit_contains_state_transition_and_correlation_id(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "INV-AUDIT-1", "amount_minor": 1500,
                         "currency": "USD", "provider_key": "mock"}).json()
    pid = p["id"]
    logs = _audit_for(admin, acme, pid, "payment.create")
    assert logs, "expected payment.create audit entry"
    ch = logs[0]["changes"] or {}
    assert "previous_state" in ch and "new_state" in ch, f"audit changes missing states: {ch}"
    assert ch["new_state"] in {"succeeded", "failed", "pending", "authorized", "captured"}
    assert ch.get("correlation_id"), "correlation_id missing"

    # No sensitive data (card numbers, cvv, pan) in the audit payload.
    blob = str(logs[0])
    for pat in SENSITIVE_PATTERNS:
        assert not pat.search(blob), f"sensitive data leaked in audit: {pat.pattern}"


def _audit_for(admin, tenant_id, resource_id, action):
    r = admin.get(f"/api/audit?tenant_id={tenant_id}")
    assert r.status_code == 200, r.text
    return [a for a in r.json() if a.get("resource_id") == resource_id and a.get("action") == action]


def test_refund_audit_has_previous_and_new_state(admin, acme):
    p = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "INV-AUDIT-RF", "amount_minor": 2500,
                         "currency": "USD", "provider_key": "mock"}).json()
    pid = p["id"]
    rf = admin.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 1000}).json()
    rid = rf["id"]
    logs = _audit_for(admin, acme, rid, "refund.create")
    assert logs, "expected refund.create audit entry"
    ch = logs[0]["changes"] or {}
    assert ch.get("previous_state") == "succeeded"
    assert ch.get("new_state") in {"partially_refunded", "refunded"}
    assert ch.get("correlation_id"), "correlation_id missing on refund audit"
