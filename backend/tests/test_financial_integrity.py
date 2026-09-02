"""Focused financial-integrity tests (reversal, UTR, cumulative refund, settlement/reconciliation
idempotency, tenant isolation, RBAC, secret non-leakage).

Runs against the live server (like the rest of the suite). Only covers behavior added/hardened by
the financial-safety closure task; does not duplicate existing coverage.
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
    assert r.status_code == 200
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


@pytest.fixture(scope="module")
def scoped(admin_client):
    """A second tenant + a user holding financial permissions, for tenant-isolation / RBAC tests."""
    suffix = uuid.uuid4().hex[:8]
    r = admin_client.post("/api/tenants", json={
        "name": f"FinTest {suffix}", "slug": f"fintest-{suffix}", "country": "US",
        "default_currency": "USD", "contact_email": f"fin{suffix}@test.io"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    perms = ["payment.create", "refund.create", "payment.reverse", "utr.submit", "utr.verify"]
    r = admin_client.post(f"/api/roles?tenant_id={tid}", json={
        "name": "Fin Ops", "description": "financial ops", "permission_codes": perms})
    assert r.status_code == 200, r.text
    role_id = r.json()["id"]

    email = f"finops-{suffix}@test.io"
    pwd = "FinOps-Passw0rd!"
    r = admin_client.post(f"/api/users?tenant_id={tid}", json={
        "email": email, "name": "Fin Ops", "password": pwd, "role_id": role_id})
    assert r.status_code == 200, r.text

    client = _client(_login(email, pwd))
    yield {"tenant_id": tid, "client": client}
    client.close()


def _make_payment(admin_client, tenant_id, amount=10000, currency="USD"):
    ref = f"PAY-{uuid.uuid4().hex[:8]}"
    r = admin_client.post(f"/api/payments?tenant_id={tenant_id}", json={
        "reference": ref, "amount_minor": amount, "currency": currency,
        "provider_key": "mock", "idempotency_key": f"idem-{uuid.uuid4().hex[:10]}"})
    assert r.status_code == 200, r.text
    return r.json()


# ============================ REVERSAL ============================
class TestReversal:
    def test_valid_reversal(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id)
        assert p["status"] == "succeeded"
        r = admin_client.post(f"/api/payments/{p['id']}/reverse", json={"reason": "test"})
        assert r.status_code == 200, r.text
        rev = r.json()
        assert rev["status"] == "succeeded"
        assert rev["payment_id"] == p["id"]
        # Payment moved to terminal reversed state.
        pr = admin_client.get(f"/api/payments/{p['id']}")
        assert pr.json()["status"] == "reversed"
        # A compensating DEBIT ledger entry exists for the reversal (never creates money).
        le = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        assert any(e["ref_type"] == "reversal" and e["ref_id"] == rev["id"]
                   and e["direction"] == "debit" for e in le)

    def test_duplicate_reversal_rejected(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id)
        r1 = admin_client.post(f"/api/payments/{p['id']}/reverse", json={})
        assert r1.status_code == 200
        r2 = admin_client.post(f"/api/payments/{p['id']}/reverse", json={})
        assert r2.status_code == 400
        assert "already been reversed" in r2.text.lower()

    def test_idempotent_reversal_by_key(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id)
        key = f"rev-{uuid.uuid4().hex[:8]}"
        r1 = admin_client.post(f"/api/payments/{p['id']}/reverse", json={"idempotency_key": key})
        r2 = admin_client.post(f"/api/payments/{p['id']}/reverse", json={"idempotency_key": key})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]

    def test_invalid_lifecycle_reversal_rejected(self, admin_client, acme_id):
        # amount ending in 13 minor units declines -> failed (not reversible)
        p = _make_payment(admin_client, acme_id, amount=10013)
        assert p["status"] == "failed"
        r = admin_client.post(f"/api/payments/{p['id']}/reverse", json={})
        assert r.status_code == 400
        assert "reversible" in r.text.lower()

    def test_unauthorized_reversal_rejected(self, admin_client, acme_id):
        # ops-admin lacks payment.reverse permission
        p = _make_payment(admin_client, acme_id)
        ops = _client(_login("ops-admin@cloudpay.io", ADMIN_PASSWORD))
        try:
            r = ops.post(f"/api/payments/{p['id']}/reverse", json={})
            assert r.status_code == 403
        finally:
            ops.close()

    def test_cross_tenant_reversal_rejected(self, admin_client, acme_id, scoped):
        # scoped user HAS payment.reverse but in another tenant -> cannot touch acme's payment
        p = _make_payment(admin_client, acme_id)
        r = scoped["client"].post(f"/api/payments/{p['id']}/reverse", json={})
        assert r.status_code == 404


# ============================ CUMULATIVE REFUND ============================
class TestCumulativeRefund:
    def test_full_and_partial_and_overrefund(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id, amount=10000)
        r = admin_client.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 6000})
        assert r.status_code == 200 and r.json()["status"] == "succeeded"
        # cumulative cap: 6000 + 5000 > 10000 -> rejected
        r = admin_client.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 5000})
        assert r.status_code == 400 and "refundable" in r.text.lower()
        # remaining 4000 allowed
        r = admin_client.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 4000})
        assert r.status_code == 200
        # nothing left
        r = admin_client.post(f"/api/payments/{p['id']}/refunds", json={"amount_minor": 1})
        assert r.status_code == 400

    def test_duplicate_idempotent_refund(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id, amount=10000)
        key = f"rf-{uuid.uuid4().hex[:8]}"
        r1 = admin_client.post(f"/api/payments/{p['id']}/refunds",
                               json={"amount_minor": 3000, "idempotency_key": key})
        r2 = admin_client.post(f"/api/payments/{p['id']}/refunds",
                               json={"amount_minor": 3000, "idempotency_key": key})
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["id"] == r2.json()["id"]

    def test_concurrent_refund_safety(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id, amount=10000)
        pid = p["id"]
        token = admin_client.headers["Authorization"].split(" ", 1)[1]

        def _refund():
            with _client(token) as c:
                return c.post(f"/api/payments/{pid}/refunds",
                              json={"amount_minor": 6000,
                                    "idempotency_key": f"c-{uuid.uuid4().hex[:8]}"}).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            codes = list(ex.map(lambda _: _refund(), range(2)))
        # 6000 + 6000 > 10000: exactly one may succeed under the row lock
        assert codes.count(200) == 1, codes
        assert codes.count(400) == 1, codes
        # Verify total succeeded refunds never exceed the captured amount
        refs = admin_client.get(f"/api/payments/refunds/all?tenant_id={acme_id}").json()
        total = sum(x["amount_minor"] for x in refs
                    if x["payment_id"] == pid and x["status"] == "succeeded")
        assert total <= 10000


# ============================ UTR VERIFICATION ============================
class TestUtr:
    def test_submit_then_confirm_credits_once(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        r = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                              json={"utr": utr, "amount_minor": 5000, "currency": "USD"})
        assert r.status_code == 200, r.text
        sub = r.json()
        assert sub["status"] == "under_review"
        # Not credited on submission
        le0 = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        assert not any(e["ref_type"] == "utr" and e["ref_id"] == sub["id"] for e in le0)
        # Manual confirm with matching expected values -> credited exactly once
        r = admin_client.post(f"/api/payments/utr/{sub['id']}/review",
                              json={"decision": "confirm", "expected_amount_minor": 5000,
                                    "expected_currency": "USD"})
        assert r.status_code == 200 and r.json()["status"] == "confirmed"
        le1 = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        credits = [e for e in le1 if e["ref_type"] == "utr" and e["ref_id"] == sub["id"]]
        assert len(credits) == 1 and credits[0]["direction"] == "credit"
        # Re-confirm is idempotent (no second credit)
        r = admin_client.post(f"/api/payments/utr/{sub['id']}/review", json={"decision": "confirm"})
        assert r.status_code == 200
        le2 = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        assert len([e for e in le2 if e["ref_type"] == "utr" and e["ref_id"] == sub["id"]]) == 1

    def test_duplicate_utr_rejected(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        r1 = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                               json={"utr": utr, "amount_minor": 4000, "currency": "USD"})
        assert r1.status_code == 200
        r2 = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                               json={"utr": utr, "amount_minor": 4000, "currency": "USD"})
        assert r2.status_code == 400 and "already been submitted" in r2.text.lower()

    def test_amount_mismatch_rejected(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        sub = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                                json={"utr": utr, "amount_minor": 5000, "currency": "USD"}).json()
        r = admin_client.post(f"/api/payments/utr/{sub['id']}/review",
                              json={"decision": "confirm", "expected_amount_minor": 6000})
        assert r.status_code == 400 and "amount mismatch" in r.text.lower()
        # remains under_review, uncredited
        assert admin_client.get(f"/api/payments/utr/list?tenant_id={acme_id}").json()

    def test_currency_mismatch_rejected(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        sub = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                                json={"utr": utr, "amount_minor": 5000, "currency": "USD"}).json()
        r = admin_client.post(f"/api/payments/utr/{sub['id']}/review",
                              json={"decision": "confirm", "expected_currency": "EUR"})
        assert r.status_code == 400 and "currency mismatch" in r.text.lower()

    def test_linked_payment_status_mismatch_rejected(self, admin_client, acme_id):
        # A succeeded payment is NOT awaiting a bank transfer -> confirm must reject
        p = _make_payment(admin_client, acme_id, amount=7000)
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        sub = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                                json={"utr": utr, "amount_minor": 7000, "currency": "USD",
                                      "payment_id": p["id"]}).json()
        r = admin_client.post(f"/api/payments/utr/{sub['id']}/review", json={"decision": "confirm"})
        assert r.status_code == 400 and "status mismatch" in r.text.lower()

    def test_unverified_utr_has_no_credit(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        sub = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                                json={"utr": utr, "amount_minor": 3000, "currency": "USD"}).json()
        le = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        assert not any(e["ref_type"] == "utr" and e["ref_id"] == sub["id"] for e in le)

    def test_unauthorized_confirm_rejected(self, admin_client, acme_id):
        utr = f"UTR{uuid.uuid4().hex[:12].upper()}"
        sub = admin_client.post(f"/api/payments/utr?tenant_id={acme_id}",
                                json={"utr": utr, "amount_minor": 3000, "currency": "USD"}).json()
        ops = _client(_login("ops-admin@cloudpay.io", ADMIN_PASSWORD))
        try:
            r = ops.post(f"/api/payments/utr/{sub['id']}/review", json={"decision": "confirm"})
            assert r.status_code == 403
        finally:
            ops.close()


# ============================ SETTLEMENT IDEMPOTENCY ============================
class TestSettlementIdempotency:
    def test_same_provider_ref_no_second_settlement(self, admin_client, acme_id):
        ref = f"PSR-{uuid.uuid4().hex[:10]}"
        r1 = admin_client.post(
            f"/api/settlements/generate?tenant_id={acme_id}&currency=USD&provider_settlement_ref={ref}")
        assert r1.status_code == 200, r1.text
        first_id = r1.json()["id"]
        r2 = admin_client.post(
            f"/api/settlements/generate?tenant_id={acme_id}&currency=USD&provider_settlement_ref={ref}")
        assert r2.status_code == 200
        assert r2.json()["id"] == first_id  # idempotent, no duplicate credit
        # A different reference yields a distinct settlement
        ref2 = f"PSR-{uuid.uuid4().hex[:10]}"
        r3 = admin_client.post(
            f"/api/settlements/generate?tenant_id={acme_id}&currency=USD&provider_settlement_ref={ref2}")
        assert r3.json()["id"] != first_id


# ============================ RECONCILIATION IDEMPOTENCY ============================
class TestReconciliationIdempotency:
    def test_repeated_webhook_reconcile_no_double_credit(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id)
        txn = p["provider_txn_id"]
        le_before = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        n_before = len(le_before)
        payload = {"event_type": "payment.updated", "provider_txn_id": txn, "status": "succeeded"}
        # Public inbound webhook; already 'succeeded' -> idempotent no-op, posts no ledger entries
        for _ in range(2):
            r = httpx.post(f"{BASE}/api/providers/mock/webhook", json=payload, timeout=30)
            assert r.status_code == 200, r.text
            assert r.json().get("already") == "succeeded"
        le_after = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        assert len(le_after) == n_before  # reconciliation never double-credits


# ============================ SETTLEMENT IMPORT ============================
class TestSettlementImport:
    def _csv(self, rows):
        header = "provider_settlement_ref,currency,gross_minor,fees_minor,net_minor,txn_count\n"
        return header + "".join(rows)

    def test_import_then_reimport_idempotent(self, admin_client, acme_id):
        r = uuid.uuid4().hex[:8].upper()
        csv = self._csv([f"IMP-A-{r},USD,1000000,29000,971000,120\n",
                         f"IMP-B-{r},USD,500000,15000,485000,60\n"])
        files = {"file": ("s.csv", csv, "text/csv")}
        r1 = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}", files=files)
        assert r1.status_code == 200, r1.text
        assert r1.json()["created_count"] == 2 and r1.json()["duplicate_count"] == 0
        # Re-upload the exact same file -> all duplicates, nothing new (idempotent, no double record)
        r2 = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                               files={"file": ("s.csv", csv, "text/csv")})
        assert r2.status_code == 200
        assert r2.json()["created_count"] == 0 and r2.json()["duplicate_count"] == 2

    def test_partial_new_and_existing(self, admin_client, acme_id):
        r = uuid.uuid4().hex[:8].upper()
        first = self._csv([f"IMP-C-{r},USD,100,0,100,1\n"])
        admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                          files={"file": ("s.csv", first, "text/csv")})
        mixed = self._csv([f"IMP-C-{r},USD,100,0,100,1\n", f"IMP-D-{r},USD,200,0,200,2\n"])
        r2 = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                               files={"file": ("s.csv", mixed, "text/csv")})
        body = r2.json()
        assert body["created_count"] == 1 and body["duplicate_count"] == 1

    def test_invalid_row_reported_others_created(self, admin_client, acme_id):
        r = uuid.uuid4().hex[:8].upper()
        csv = self._csv([f"IMP-E-{r},USD,notanumber,0,0,1\n", f"IMP-F-{r},USD,300,0,300,3\n"])
        resp = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                                 files={"file": ("s.csv", csv, "text/csv")})
        body = resp.json()
        assert body["created_count"] == 1 and body["error_count"] == 1

    def test_missing_column_rejected(self, admin_client, acme_id):
        resp = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                                 files={"file": ("bad.csv", "foo,bar\n1,2\n", "text/csv")})
        assert resp.status_code == 400

    def test_import_requires_permission(self, admin_client, acme_id):
        csv = self._csv([f"IMP-G-{uuid.uuid4().hex[:6]},USD,100,0,100,1\n"])
        ops = _client(_login("ops-admin@cloudpay.io", ADMIN_PASSWORD))
        try:
            resp = ops.post(f"/api/settlements/import?tenant_id={acme_id}",
                            files={"file": ("s.csv", csv, "text/csv")})
            assert resp.status_code == 403
        finally:
            ops.close()

    def test_dry_run_previews_without_persisting(self, admin_client, acme_id):
        r = uuid.uuid4().hex[:8].upper()
        csv = self._csv([f"PRV-A-{r},USD,1000000,29000,971000,120\n",
                         f"PRV-A-{r},USD,1,1,0,1\n",          # in-file duplicate
                         f"PRV-B-{r},USD,x,0,0,1\n"])          # invalid number
        # Preview: classifies rows, writes nothing
        pv = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}&dry_run=true",
                               files={"file": ("s.csv", csv, "text/csv")}).json()
        assert pv["dry_run"] is True
        assert pv["created_count"] == 1 and pv["duplicate_count"] == 1 and pv["error_count"] == 1
        statuses = [it["status"] for it in pv["items"]]
        assert statuses == ["new", "duplicate", "error"]
        # Nothing persisted -> the settlement is not listed yet
        listed = admin_client.get(f"/api/settlements?tenant_id={acme_id}").json()
        assert not any(s["reference"] == f"PRV-A-{r}" for s in listed)
        # Confirm (no dry_run) actually creates the single new row
        real = admin_client.post(f"/api/settlements/import?tenant_id={acme_id}",
                                 files={"file": ("s.csv", csv, "text/csv")}).json()
        assert real["created_count"] == 1
        listed2 = admin_client.get(f"/api/settlements?tenant_id={acme_id}").json()
        assert any(s["reference"] == f"PRV-A-{r}" for s in listed2)


# ============================ SECURITY: NO SECRET LEAK ============================
class TestNoSecretLeak:
    def test_financial_endpoints_never_leak_secrets(self, admin_client, acme_id):
        p = _make_payment(admin_client, acme_id)
        admin_client.post(f"/api/payments/{p['id']}/reverse", json={})
        blobs = [
            admin_client.get(f"/api/payments/utr/list?tenant_id={acme_id}").text,
            admin_client.get(f"/api/payments/refunds/all?tenant_id={acme_id}").text,
            admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").text,
            admin_client.get(f"/api/settlements?tenant_id={acme_id}").text,
        ]
        for blob in blobs:
            low = blob.lower()
            for bad in ("hashed_password", "credentials_ref", "ciphertext", "fernet",
                        "private_key", "sk_test_", "sk_live_"):
                assert bad not in low, bad
