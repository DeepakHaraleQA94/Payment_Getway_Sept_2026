"""Focused tests for the NEW line-level reconciliation & matching engine (report-only, additive)."""
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
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


def _pay(admin_client, acme_id, amount, currency="USD"):
    r = admin_client.post(f"/api/payments?tenant_id={acme_id}", json={
        "reference": f"RC-{uuid.uuid4().hex[:8]}", "amount_minor": amount, "currency": currency,
        "provider_key": "mock", "idempotency_key": f"ik-{uuid.uuid4().hex[:10]}"})
    assert r.status_code == 200
    return r.json()


def _csv(lines):
    return "provider_txn_id,reference,amount_minor,currency,status\n" + "".join(lines)


def _run(admin_client, acme_id, csv_text, source="upload", currency=None, run_ref=None):
    q = f"?tenant_id={acme_id}&source={source}"
    if currency:
        q += f"&currency={currency}"
    if run_ref:
        q += f"&run_ref={run_ref}"
    return admin_client.post(f"/api/reconciliation/run{q}",
                             files={"file": ("lines.csv", csv_text, "text/csv")})


class TestReconciliationMatching:
    def test_matched_and_mismatches_and_missing(self, admin_client, acme_id):
        cur = "EUR"  # isolate this run from other USD payments
        p1 = _pay(admin_client, acme_id, 10000, cur)
        p2 = _pay(admin_client, acme_id, 25000, cur)
        p3 = _pay(admin_client, acme_id, 30000, cur)  # will be missing_at_provider (not in file)
        csv = _csv([
            f"{p1['provider_txn_id']},{p1['reference']},10000,{cur},succeeded\n",   # matched
            f"{p2['provider_txn_id']},{p2['reference']},99999,{cur},succeeded\n",    # amount_mismatch
            f"mock_ghost_{uuid.uuid4().hex[:8]},GHOST,500,{cur},succeeded\n",         # missing_in_cloudpay
            f"{p1['provider_txn_id']},{p1['reference']},10000,{cur},succeeded\n",    # duplicate of p1
        ])
        r = _run(admin_client, acme_id, csv, source="upload", currency=cur)
        assert r.status_code == 200, r.text
        s = r.json()["summary"]
        assert s["matched"] == 1
        assert s["amount_mismatch"] == 1
        assert s["missing_in_cloudpay"] == 1
        assert s["duplicate"] == 1
        assert s["missing_at_provider"] >= 1  # p3 not in file
        # all 7 categories present in the summary keys
        for cat in ("matched", "amount_mismatch", "currency_mismatch", "status_mismatch",
                    "missing_in_cloudpay", "missing_at_provider", "duplicate"):
            assert cat in s

    def test_currency_and_status_mismatch(self, admin_client, acme_id):
        cur = "GBP"
        p1 = _pay(admin_client, acme_id, 4000, cur)
        p2 = _pay(admin_client, acme_id, 7000, cur)
        csv = _csv([
            f"{p1['provider_txn_id']},{p1['reference']},4000,USD,succeeded\n",  # currency_mismatch
            f"{p2['provider_txn_id']},{p2['reference']},7000,{cur},failed\n",    # status_mismatch
        ])
        r = _run(admin_client, acme_id, csv, source="upload", currency=cur)
        s = r.json()["summary"]
        assert s["currency_mismatch"] == 1 and s["status_mismatch"] == 1

    def test_idempotent_run_ref(self, admin_client, acme_id):
        cur = "USD"
        p1 = _pay(admin_client, acme_id, 1000, cur)
        csv = _csv([f"{p1['provider_txn_id']},{p1['reference']},1000,{cur},succeeded\n"])
        ref = f"RUN-{uuid.uuid4().hex[:8]}"
        r1 = _run(admin_client, acme_id, csv, source="upload", currency=cur, run_ref=ref)
        r2 = _run(admin_client, acme_id, csv, source="upload", currency=cur, run_ref=ref)
        assert r1.json()["id"] == r2.json()["id"]

    def test_detail_filter(self, admin_client, acme_id):
        cur = "USD"
        p1 = _pay(admin_client, acme_id, 5000, cur)
        csv = _csv([f"{p1['provider_txn_id']},{p1['reference']},8888,{cur},succeeded\n"])
        run_id = _run(admin_client, acme_id, csv, source="upload", currency=cur).json()["id"]
        d = admin_client.get(f"/api/reconciliation/runs/{run_id}?tenant_id={acme_id}&outcome=amount_mismatch").json()
        assert all(i["outcome"] == "amount_mismatch" for i in d["items"])
        assert any(i["provider_amount_minor"] == 8888 for i in d["items"])

    def test_read_only_no_ledger_change(self, admin_client, acme_id):
        cur = "USD"
        p1 = _pay(admin_client, acme_id, 6000, cur)
        before = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        csv = _csv([f"{p1['provider_txn_id']},{p1['reference']},6000,{cur},succeeded\n"])
        _run(admin_client, acme_id, csv, source="upload", currency=cur)
        after = admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json()
        # reconciliation must NOT create or remove any ledger entry
        assert len(after) == len(before)

    def test_provider_pull_source(self, admin_client, acme_id):
        cur = "USD"
        _pay(admin_client, acme_id, 3000, cur)
        # No file; provider_pull classifies internal payments via provider status.
        r = admin_client.post(
            f"/api/reconciliation/run?tenant_id={acme_id}&source=provider_pull&currency={cur}")
        assert r.status_code == 200
        assert r.json()["total_lines"] >= 1


class TestReconciliationRbacTenant:
    def test_run_requires_permission(self, admin_client, acme_id):
        ops = _client(_login("ops-admin@cloudpay.io", ADMIN_PASSWORD))
        try:
            r = ops.post(f"/api/reconciliation/run?tenant_id={acme_id}&source=provider_pull")
            assert r.status_code == 403
            assert ops.get(f"/api/reconciliation/runs?tenant_id={acme_id}").status_code == 403
        finally:
            ops.close()

    def test_cross_tenant_run_detail_rejected(self, admin_client, acme_id):
        # A user in another tenant with reconciliation.view cannot read acme's run.
        suffix = uuid.uuid4().hex[:8]
        tid = admin_client.post("/api/tenants", json={
            "name": f"RcTest {suffix}", "slug": f"rctest-{suffix}", "country": "US",
            "default_currency": "USD", "contact_email": f"rc{suffix}@t.io"}).json()["id"]
        role_id = admin_client.post(f"/api/roles?tenant_id={tid}", json={
            "name": "Rc Ops", "description": "x",
            "permission_codes": ["reconciliation.run", "reconciliation.view"]}).json()["id"]
        email = f"rcops-{suffix}@t.io"
        admin_client.post(f"/api/users?tenant_id={tid}", json={
            "email": email, "name": "Rc Ops", "password": "RcOps-Passw0rd!", "role_id": role_id})
        # acme run created by admin
        p1 = _pay(admin_client, acme_id, 1200, "USD")
        csv = _csv([f"{p1['provider_txn_id']},{p1['reference']},1200,USD,succeeded\n"])
        run_id = _run(admin_client, acme_id, csv, source="upload", currency="USD").json()["id"]
        other = _client(_login(email, "RcOps-Passw0rd!"))
        try:
            # cannot see acme's runs in their own list
            assert other.get(f"/api/reconciliation/runs?tenant_id={tid}").json() == []
            # cannot open acme's run detail
            assert other.get(f"/api/reconciliation/runs/{run_id}?tenant_id={tid}").status_code == 404
        finally:
            other.close()
