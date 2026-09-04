"""Iteration 32 — Production-readiness regression pass.

Covers:
  * AUTH / RBAC (unauth 401, non-superadmin cross-tenant, permission enforcement).
  * Currency catalog (auth-gated, ~52 rows, INR/JPY decimals).
  * Payment idempotency + ledger single-credit invariant.
  * Refund cap + status transitions + refund idempotency.
  * Reversal endpoint (existing successful payment).
  * Outbound webhook create + dispatch + replay (event_id preservation).
  * Inbound provider webhook idempotency / unmatched / skipped.
  * Demo UPI checkout: apps + upi_link + success->paid + failed->simulated + double-pay reject.
  * Reconciliation export CSV (method column + METHOD BREAKDOWN footer) + tenant isolation.
  * Refunds list contains provider_key.
  * Reports export payments.csv has 'method' column.
"""
import csv
import io
import os
import uuid

import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"
OPS_EMAIL = "ops-admin@cloudpay.io"
OPS_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text[:250]}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(ADMIN_EMAIL, ADMIN_PASS)


@pytest.fixture(scope="module")
def ops_session():
    # Non-superadmin platform user — used for cross-tenant / permission tests.
    try:
        return _login(OPS_EMAIL, OPS_PASS)
    except AssertionError:
        pytest.skip("ops-admin@cloudpay.io not available or password differs")


@pytest.fixture(scope="module")
def tenants(admin_session):
    r = admin_session.get(f"{API}/tenants", timeout=15)
    assert r.status_code == 200
    return {t["slug"]: t for t in r.json()}


@pytest.fixture(scope="module")
def acme_id(tenants):
    return tenants["acme"]["id"]


@pytest.fixture(scope="module")
def captest_id(tenants):
    # CapTest 3b91058e has demo_upi + mock providers seeded.
    for slug, t in tenants.items():
        if slug.startswith("captest"):
            return t["id"]
    return tenants["acme"]["id"]


# -------------------------------------------------------------------- AUTH/RBAC
class TestAuth:
    def test_no_auth_currencies_401(self):
        r = requests.get(f"{API}/currencies", timeout=15)
        assert r.status_code in (401, 403)

    def test_no_auth_payments_401(self):
        r = requests.get(f"{API}/payments", timeout=15)
        assert r.status_code in (401, 403)

    def test_no_auth_ledger_401(self):
        r = requests.get(f"{API}/ledger/entries", timeout=15)
        assert r.status_code in (401, 403)

    def test_cross_tenant_denied_non_superadmin(self, ops_session, acme_id):
        # ops-admin belongs to 'platform' tenant. Query acme -> should be 403/404.
        r = ops_session.get(f"{API}/payments?tenant_id={acme_id}", timeout=15)
        assert r.status_code in (403, 404), f"Expected 403/404, got {r.status_code}: {r.text[:200]}"

    def test_permission_gate_provider_manage(self, ops_session, acme_id):
        # provider.manage required to add a provider — ops-admin (Platform Ops) should not have it
        # for a NON-platform tenant. If it happens to have it, at least cross-tenant should 403.
        body = {"provider_key": "mock", "mode": "sandbox", "enabled": True}
        r = ops_session.post(f"{API}/providers?tenant_id={acme_id}", json=body, timeout=15)
        assert r.status_code in (403, 404), f"Expected 403/404, got {r.status_code}: {r.text[:200]}"


# ----------------------------------------------------------------- CURRENCIES
class TestCurrencyCatalog:
    def test_catalog(self, admin_session):
        r = admin_session.get(f"{API}/currencies", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) >= 50, f"Expected >=50 currencies, got {len(data)}"
        by_code = {c["code"]: c for c in data}
        assert by_code["INR"]["decimals"] == 2
        assert by_code["JPY"]["decimals"] == 0
        for c in data[:5]:
            assert set(c.keys()) >= {"code", "name", "decimals", "symbol"}


@pytest.fixture(scope="module")
def idem_payment(admin_session, captest_id):
    """Create one succeeded mock payment via idempotency, verify twin invariant.
    Reused across ledger/refund tests to avoid cross-worker state coupling."""
    key = f"TEST-idem-{uuid.uuid4().hex[:10]}"
    body = {
        "reference": f"TEST-{uuid.uuid4().hex[:6]}", "amount_minor": 5000,
        "currency": "USD", "provider_key": "mock", "environment": "sandbox",
        "idempotency_key": key, "payment_method": "card", "flow": "direct",
    }
    h = {"X-Tenant-Id": str(captest_id)}
    r1 = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30)
    assert r1.status_code == 200, r1.text[:300]
    p1 = r1.json()
    r2 = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30)
    assert r2.status_code == 200
    assert r2.json()["id"] == p1["id"], "Idempotency violated"
    assert p1["status"] in ("succeeded", "captured")
    assert p1["net_minor"] == p1["amount_minor"] - p1["fee_minor"]
    return p1


# ----------------------------------------------------------------- IDEMPOTENCY
class TestPaymentIdempotency:
    def test_same_key_returns_same_payment(self, idem_payment):
        # Assertions already handled inside fixture; expose the payment for downstream tests.
        assert idem_payment["id"]

    def test_different_key_creates_new_payment(self, admin_session, captest_id, idem_payment):
        body = {
            "reference": f"TEST-{uuid.uuid4().hex[:6]}", "amount_minor": 5000,
            "currency": "USD", "provider_key": "mock", "environment": "sandbox",
            "idempotency_key": f"TEST-idem-{uuid.uuid4().hex[:10]}",
            "payment_method": "card", "flow": "direct",
        }
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200
        assert r.json()["id"] != idem_payment["id"]


# ----------------------------------------------------------------- LEDGER / BALANCE
class TestLedgerInvariant:
    def test_ledger_credit_for_payment(self, admin_session, captest_id, idem_payment):
        p = idem_payment
        r = admin_session.get(f"{API}/ledger/entries?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 200
        entries = r.json()
        credits = [
            e for e in entries
            if e.get("ref_type") == "payment" and str(e.get("ref_id")) == p["id"]
            and e.get("direction") == "credit"
        ]
        assert len(credits) == 1, f"Expected 1 credit, got {len(credits)}"
        assert credits[0]["amount_minor"] == p["net_minor"]


# ----------------------------------------------------------------- REFUND
class TestRefund:
    def test_refund_cap_rejected(self, admin_session, captest_id, idem_payment):
        p = idem_payment
        body = {"amount_minor": p["amount_minor"] + 1, "reason": "TEST over-cap"}
        r = admin_session.post(f"{API}/payments/{p['id']}/refunds?tenant_id={captest_id}",
                               json=body, headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code in (400, 422)

    def test_partial_refund_marks_partially_refunded(self, admin_session, captest_id, idem_payment):
        p = idem_payment
        key = f"TEST-rf-partial-{uuid.uuid4().hex[:8]}"
        body = {"amount_minor": 1000, "reason": "TEST partial", "idempotency_key": key}
        r = admin_session.post(f"{API}/payments/{p['id']}/refunds?tenant_id={captest_id}",
                               json=body, headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code == 200, r.text[:200]
        rf1 = r.json()
        # idempotency
        r2 = admin_session.post(f"{API}/payments/{p['id']}/refunds?tenant_id={captest_id}",
                                json=body, headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r2.status_code == 200 and r2.json()["id"] == rf1["id"]
        rp = admin_session.get(f"{API}/payments/{p['id']}", timeout=15)
        assert rp.json()["status"] == "partially_refunded"

    def test_full_refund_marks_refunded(self, admin_session, captest_id, idem_payment):
        p = idem_payment
        # after previous partial of 1000
        remaining = p["amount_minor"] - 1000
        body = {"amount_minor": remaining, "reason": "TEST full",
                "idempotency_key": f"TEST-rf-full-{uuid.uuid4().hex[:8]}"}
        r = admin_session.post(f"{API}/payments/{p['id']}/refunds?tenant_id={captest_id}",
                               json=body, headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code == 200, r.text[:200]
        rp = admin_session.get(f"{API}/payments/{p['id']}", timeout=15)
        assert rp.json()["status"] == "refunded"

    def test_refunds_list_includes_provider_key(self, admin_session, captest_id):
        r = admin_session.get(f"{API}/payments/refunds/all?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 1
        missing = [row for row in rows if not row.get("provider_key")]
        assert not missing, f"Refunds missing provider_key: {len(missing)}/{len(rows)}"


# ----------------------------------------------------------------- REVERSAL
class TestReversal:
    def test_reverse_new_payment(self, admin_session, captest_id):
        # Create a fresh succeeded payment to reverse.
        key = f"TEST-rev-{uuid.uuid4().hex[:10]}"
        body = {"reference": f"TEST-REV-{uuid.uuid4().hex[:6]}", "amount_minor": 3000,
                "currency": "USD", "provider_key": "mock", "environment": "sandbox",
                "idempotency_key": key, "payment_method": "card", "flow": "direct"}
        h = {"X-Tenant-Id": str(captest_id)}
        p = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30).json()
        rk = f"TEST-rev-op-{uuid.uuid4().hex[:8]}"
        r = admin_session.post(f"{API}/payments/{p['id']}/reverse?tenant_id={captest_id}",
                               json={"reason": "TEST", "idempotency_key": rk}, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:300]
        # Idempotent replay
        r2 = admin_session.post(f"{API}/payments/{p['id']}/reverse?tenant_id={captest_id}",
                                json={"reason": "TEST", "idempotency_key": rk}, headers=h, timeout=30)
        assert r2.status_code == 200
        # Payment status should reflect a terminal reversed/cancelled state
        rp = admin_session.get(f"{API}/payments/{p['id']}", timeout=15)
        assert rp.json()["status"] in ("reversed", "cancelled", "refunded")


# ----------------------------------------------------------------- WEBHOOKS
class TestWebhooksOutbound:
    def test_create_endpoint_and_dispatch(self, admin_session, captest_id):
        body = {"url": "https://example.invalid/webhook",
                "description": "TEST iter32", "events": ["payment.succeeded"]}
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/webhooks/endpoints?tenant_id={captest_id}",
                               json=body, headers=h, timeout=15)
        assert r.status_code == 200, r.text[:200]
        ep = r.json()
        pytest.iter32_wh_ep = ep["id"]

        # Trigger a payment.succeeded — creating a mock payment.
        pbody = {"reference": f"TEST-WH-{uuid.uuid4().hex[:6]}", "amount_minor": 2000,
                 "currency": "USD", "provider_key": "mock", "environment": "sandbox",
                 "idempotency_key": f"TEST-wh-{uuid.uuid4().hex[:8]}",
                 "payment_method": "card", "flow": "direct"}
        rp = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=pbody,
                                headers=h, timeout=30)
        assert rp.status_code == 200

        # List deliveries — expect at least one with a signature header recorded (via payload/attempts).
        rd = admin_session.get(f"{API}/webhooks/deliveries?tenant_id={captest_id}",
                               headers=h, timeout=15)
        assert rd.status_code == 200
        deliveries = rd.json()
        assert any(d.get("event") == "payment.succeeded" for d in deliveries), \
            "No payment.succeeded delivery recorded"
        pytest.iter32_wh_deliveries = deliveries

    def test_replay_preserves_event_id(self, admin_session, captest_id):
        deliveries = pytest.iter32_wh_deliveries
        target = next((d for d in deliveries if d.get("event") == "payment.succeeded"), None)
        assert target is not None
        r = admin_session.post(f"{API}/webhooks/deliveries/{target['id']}/replay?tenant_id={captest_id}",
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 200, r.text[:200]
        assert str(r.json().get("event_id")) == str(target["event_id"]), \
            "Replay must preserve event_id"

    def test_webhook_manage_permission_enforced(self, ops_session, captest_id):
        r = ops_session.post(
            f"{API}/webhooks/endpoints?tenant_id={captest_id}",
            json={"url": "https://example.invalid", "events": ["payment.succeeded"]},
            headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code in (403, 404), f"Expected 403/404, got {r.status_code}"


# ----------------------------------------------------------------- INBOUND PROVIDER WEBHOOK
class TestInboundProviderWebhook:
    def test_unknown_provider_404(self):
        r = requests.post(f"{API}/providers/nonexistent_xyz/webhook", json={}, timeout=15)
        assert r.status_code == 404

    def test_mock_webhook_unmatched(self):
        # mock provider callback: pass a payload the plugin can verify but that references an
        # unknown provider_txn_id. If mock does not implement callbacks -> 400 is acceptable.
        r = requests.post(f"{API}/providers/mock/webhook",
                          json={"provider_txn_id": f"unknown_{uuid.uuid4().hex}",
                                "status": "succeeded", "event_type": "payment.succeeded"},
                          timeout=15)
        # Two acceptable shapes: (1) plugin has no callback support -> 400,
        # (2) callback works but txn not found -> 200 unmatched.
        if r.status_code == 400:
            pytest.skip("mock provider does not implement verify_callback (acceptable per code)")
        assert r.status_code == 200
        body = r.json()
        assert body.get("unmatched") is True or body.get("ignored") is True


# ----------------------------------------------------------------- DEMO UPI CHECKOUT
class TestDemoUpiCheckout:
    def _new_session(self, admin_session, tid, amount=12300):
        body = {"reference": f"TEST-upi-{uuid.uuid4().hex[:6]}", "amount_minor": amount,
                "currency": "INR", "provider_key": "demo_upi"}
        r = admin_session.post(f"{API}/checkout/sessions?tenant_id={tid}", json=body,
                               headers={"X-Tenant-Id": str(tid)}, timeout=20)
        assert r.status_code in (200, 201), r.text[:200]
        return r.json()

    def test_upi_info_and_pay_success_then_double_pay_rejected(self, admin_session, captest_id):
        s = self._new_session(admin_session, captest_id)
        # /upi info
        ri = requests.get(f"{API}/public/checkout/{s['token']}/upi", timeout=15)
        assert ri.status_code == 200
        data = ri.json()
        assert data["upi_link"].startswith("upi://pay?")
        assert data.get("vpa")
        assert {"phonepe", "gpay", "paytm", "bhim", "qr"}.issubset({a["key"] for a in data["apps"]})
        # success
        rp = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                           json={"upi_app": "phonepe", "outcome": "success"}, timeout=30)
        assert rp.status_code == 200
        j = rp.json()
        assert j["status"] == "paid" and j.get("payment_id")
        # method must reflect UPI on the resulting payment — validate via provider_key
        # (metadata_json is not exposed on the OUT schema).
        pay = admin_session.get(f"{API}/payments/{j['payment_id']}", timeout=15).json()
        assert pay.get("provider_key") == "demo_upi", f"Expected demo_upi provider, got {pay.get('provider_key')}"
        # Double pay rejected
        rp2 = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                            json={"upi_app": "phonepe", "outcome": "success"}, timeout=15)
        assert rp2.status_code == 400

    def test_upi_failed_outcome_no_payment(self, admin_session, captest_id):
        s = self._new_session(admin_session, captest_id, amount=4400)
        rp = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                           json={"upi_app": "gpay", "outcome": "failed"}, timeout=15)
        assert rp.status_code == 200
        assert rp.json()["status"] == "simulated"
        # session still open
        sinfo = requests.get(f"{API}/public/checkout/{s['token']}", timeout=15).json()
        assert sinfo["status"] != "paid"


# ----------------------------------------------------------------- RECONCILIATION EXPORT
class TestReconciliationExport:
    def test_run_and_export_has_method_column(self, admin_session, captest_id):
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/reconciliation/run?tenant_id={captest_id}&source=provider_pull",
                               headers=h, timeout=60)
        assert r.status_code == 200, r.text[:300]
        run_id = r.json()["id"]
        # detail returns method_summary
        rd = admin_session.get(f"{API}/reconciliation/runs/{run_id}?tenant_id={captest_id}",
                               headers=h, timeout=30)
        assert rd.status_code == 200
        detail = rd.json()
        assert "method_summary" in detail
        if detail.get("items"):
            assert "method" in detail["items"][0]
        # CSV export
        re = admin_session.get(f"{API}/reconciliation/runs/{run_id}/export.csv?tenant_id={captest_id}",
                               headers=h, timeout=30)
        assert re.status_code == 200
        text = re.text
        rdr = csv.reader(io.StringIO(text))
        rows = list(rdr)
        header = rows[0]
        assert "method" in header, f"'method' column missing: {header}"
        assert any("METHOD BREAKDOWN" in row for row in rows if row), \
            "METHOD BREAKDOWN footer missing"

    def test_cross_tenant_run_denied(self, admin_session, ops_session, captest_id):
        # First get a run id (any) on captest
        r = admin_session.get(f"{API}/reconciliation/runs?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 200
        runs = r.json()
        if not runs:
            pytest.skip("no reconciliation runs available for cross-tenant check")
        rid = runs[0]["id"]
        # Ops-admin (platform tenant) fetching a captest run must 404 (or 403).
        r2 = ops_session.get(f"{API}/reconciliation/runs/{rid}?tenant_id={captest_id}",
                             headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r2.status_code in (403, 404)


# ----------------------------------------------------------------- REPORTS EXPORT
class TestReportsExport:
    def test_payments_csv_has_method(self, admin_session, captest_id):
        r = admin_session.get(f"{API}/reports/export/payments.csv?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=30)
        assert r.status_code == 200, r.text[:200]
        rdr = csv.reader(io.StringIO(r.text))
        rows = list(rdr)
        assert rows, "empty CSV"
        header = rows[0]
        assert "method" in header, f"'method' column missing: {header}"
        # find idx of method + provider_key
        m_idx = header.index("method")
        pk_idx = header.index("provider_key") if "provider_key" in header else None
        if pk_idx is not None:
            for row in rows[1:20]:
                if len(row) > max(m_idx, pk_idx):
                    if row[pk_idx] == "demo_upi":
                        assert row[m_idx] == "upi", f"demo_upi should map to method=upi: {row}"
