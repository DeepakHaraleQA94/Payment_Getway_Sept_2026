"""Iteration 33 — Phase-1 real-provider hardening verification.

Covers the additive fixes described in the review request:
  * GAP1: ASYNC provider webhook path posts fee/net + a single ledger credit when a
    non-terminal payment reconciles to succeeded via /providers/mock/webhook, is idempotent,
    and does NOT double-credit synchronous payments that already have a ledger credit.
  * GAP2: Payment.payment_method + Payment.flow are persisted on create, echoed on the
    OUT schema, and used authoritatively by reconciliation + reports CSV.
  * GAP3: LIVE payments are rejected for (a) sandbox-only providers (demo_upi), (b) providers
    supporting live but without a configured/enabled account, and (c) providers requiring
    credentials but with no credentials_ref persisted.
  * GAP4: Malformed / unknown webhook payloads return 400 / {unmatched} / 404 respectively.
  * Unsupported capability rejection when explicitly selecting an ineligible provider.
  * Tenant isolation preserved.
  * Demo UPI still works end-to-end (regression sanity).
"""
import json
import os
import uuid

import psycopg2
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"
OPS_EMAIL = "ops-admin@cloudpay.io"
OPS_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"

PG_DSN = "host=localhost dbname=cloudpay user=cloudpay password=cloudpay_local_pwd"


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
    try:
        return _login(OPS_EMAIL, OPS_PASS)
    except AssertionError:
        pytest.skip("ops-admin not available")


@pytest.fixture(scope="module")
def tenants(admin_session):
    r = admin_session.get(f"{API}/tenants", timeout=15)
    assert r.status_code == 200
    return {t["slug"]: t for t in r.json()}


@pytest.fixture(scope="module")
def captest_id(tenants):
    for slug, t in tenants.items():
        if slug.startswith("captest"):
            return t["id"]
    return list(tenants.values())[0]["id"]


@pytest.fixture(scope="module")
def acme_id(tenants):
    return tenants["acme"]["id"]


@pytest.fixture(scope="module")
def db_conn():
    conn = psycopg2.connect(PG_DSN)
    yield conn
    conn.close()


def _insert_pending_payment(conn, tenant_id: str, amount_minor: int = 20000,
                            currency: str = "USD", provider_key: str = "mock") -> tuple[str, str]:
    """Insert a pending, un-credited payment with a unique provider_txn_id (simulates an ASYNC
    provider that returned 'pending' at charge time and will confirm via webhook later)."""
    ptxn = f"ASYNC_{uuid.uuid4().hex[:16]}"
    ref = f"ASYNC_TEST_{uuid.uuid4().hex[:8]}"
    pid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO payments (id, tenant_id, reference, provider_key, provider_txn_id,
                                  amount_minor, currency, fee_minor, net_minor, status,
                                  risk_score, metadata_json, environment, payment_method, flow)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 'pending', 0, %s::jsonb, 'sandbox', 'card', 'direct')
        """, (pid, tenant_id, ref, provider_key, ptxn, amount_minor, currency, json.dumps({})))
    conn.commit()
    return pid, ptxn


def _count_credits(conn, tenant_id: str, payment_id: str) -> tuple[int, int]:
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COALESCE(SUM(amount_minor), 0)
                FROM ledger_entries
                WHERE tenant_id=%s AND ref_type='payment' AND ref_id=%s AND direction='credit'
            """, (tenant_id, payment_id))
            n, total = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return int(n), int(total or 0)


# ============================================================ GAP1: ASYNC WEBHOOK CREDIT
class TestAsyncWebhookCredit:
    def test_pending_to_succeeded_posts_exactly_one_credit(self, db_conn, acme_id, admin_session):
        tid = acme_id
        pid, ptxn = _insert_pending_payment(db_conn, tid, amount_minor=20000)
        # Precondition: no ledger credit yet.
        n0, s0 = _count_credits(db_conn, tid, pid)
        assert n0 == 0 and s0 == 0

        r = requests.post(
            f"{API}/providers/mock/webhook",
            json={"event_type": "payment.succeeded", "provider_txn_id": ptxn, "status": "succeeded"},
            timeout=20)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("reconciled") is True and body.get("status") == "succeeded", body

        # Reload payment state from DB.
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, amount_minor, fee_minor, net_minor FROM payments WHERE id=%s", (pid,))
            status, amount, fee, net = cur.fetchone()
        db_conn.commit()
        assert status == "succeeded"
        assert amount == 20000
        # Acme has a mock USD fee rule (2.9% + 30) => fee=610, net=19390.
        assert fee == 610, f"expected fee=610 for 20000 USD via mock on acme, got {fee}"
        assert net == amount - fee == 19390

        # Exactly ONE credit == net_minor.
        n1, s1 = _count_credits(db_conn, tid, pid)
        assert n1 == 1, f"expected exactly 1 ledger credit, got {n1}"
        assert s1 == net, f"credit amount {s1} != net_minor {net}"

        # Also verify via API.
        pget = admin_session.get(f"{API}/payments/{pid}?tenant_id={tid}",
                                 headers={"X-Tenant-Id": str(tid)}, timeout=15).json()
        assert pget["status"] == "succeeded"
        assert pget["fee_minor"] == fee and pget["net_minor"] == net
        # Stash for the duplicate-webhook test.
        pytest.async_pid = pid
        pytest.async_ptxn = ptxn
        pytest.async_net = net
        pytest.async_tid = tid

    def test_duplicate_webhook_is_idempotent(self, db_conn):
        ptxn = getattr(pytest, "async_ptxn", None)
        pid = getattr(pytest, "async_pid", None)
        tid = getattr(pytest, "async_tid", None)
        assert ptxn and pid, "previous test must have run"
        r = requests.post(
            f"{API}/providers/mock/webhook",
            json={"event_type": "payment.succeeded", "provider_txn_id": ptxn, "status": "succeeded"},
            timeout=20)
        assert r.status_code == 200
        assert r.json().get("already") == "succeeded", r.json()

        n, s = _count_credits(db_conn, tid, pid)
        assert n == 1, f"duplicate webhook produced {n} credits"
        assert s == pytest.async_net

    def test_no_duplicate_credit_for_sync_payment(self, admin_session, db_conn, captest_id):
        """A normal synchronous mock payment already has 1 credit from the charge flow. Even if a
        matching mock webhook arrives afterwards, ensure_success_credit must be a no-op."""
        key = f"TEST_syncwh_{uuid.uuid4().hex[:8]}"
        body = {"reference": f"TEST_SYNCWH_{uuid.uuid4().hex[:6]}", "amount_minor": 7000,
                "currency": "USD", "provider_key": "mock", "environment": "sandbox",
                "idempotency_key": key, "payment_method": "card", "flow": "direct"}
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200
        p = r.json()
        assert p["status"] in ("succeeded", "captured")
        pid = p["id"]
        # Baseline: exactly one credit already.
        n0, s0 = _count_credits(db_conn, captest_id, pid)
        assert n0 == 1 and s0 == p["net_minor"]

        # Fetch provider_txn_id from DB (not exposed on OUT necessarily — but it is).
        ptxn = p.get("provider_txn_id")
        if not ptxn:
            with db_conn.cursor() as cur:
                cur.execute("SELECT provider_txn_id FROM payments WHERE id=%s", (pid,))
                ptxn = cur.fetchone()[0]
        assert ptxn

        # Fire a "success" webhook. Because state is ALREADY 'succeeded', the router returns
        # {already: 'succeeded'} BEFORE ensure_success_credit is even called — no second credit.
        r2 = requests.post(f"{API}/providers/mock/webhook",
                           json={"event_type": "payment.succeeded", "provider_txn_id": ptxn,
                                 "status": "succeeded"}, timeout=20)
        assert r2.status_code == 200
        n1, s1 = _count_credits(db_conn, captest_id, pid)
        assert n1 == 1, f"sync payment double-credited (webhook posted an extra credit): {n1}"
        assert s1 == s0


# ============================================================ GAP2: PAYMENT METHOD / FLOW PERSISTENCE
class TestPaymentMethodFlowPersistence:
    def test_card_direct_persisted_and_echoed(self, admin_session, captest_id):
        body = {"reference": f"TEST_PM_{uuid.uuid4().hex[:6]}", "amount_minor": 4200,
                "currency": "USD", "provider_key": "mock", "environment": "sandbox",
                "idempotency_key": f"TEST_pm_{uuid.uuid4().hex[:8]}",
                "payment_method": "card", "flow": "direct"}
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:300]
        p = r.json()
        assert p["payment_method"] == "card"
        assert p["flow"] == "direct"
        # GET must persist.
        p2 = admin_session.get(f"{API}/payments/{p['id']}?tenant_id={captest_id}",
                               headers=h, timeout=15).json()
        assert p2["payment_method"] == "card"
        assert p2["flow"] == "direct"

    def test_demo_upi_persists_method_upi(self, admin_session, captest_id):
        # Create a demo_upi checkout session and pay -> resulting payment must have method=upi.
        s = admin_session.post(f"{API}/checkout/sessions?tenant_id={captest_id}",
                               json={"reference": f"TEST_UPI_PM_{uuid.uuid4().hex[:6]}",
                                     "amount_minor": 3300, "currency": "INR",
                                     "provider_key": "demo_upi"},
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20).json()
        rp = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                           json={"upi_app": "gpay", "outcome": "success"}, timeout=30)
        assert rp.status_code == 200
        pid = rp.json()["payment_id"]
        p = admin_session.get(f"{API}/payments/{pid}?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=15).json()
        assert p["payment_method"] == "upi", f"demo_upi should persist payment_method='upi', got {p.get('payment_method')}"
        assert p["provider_key"] == "demo_upi"
        pytest.upi_pid = pid

    def test_reports_csv_method_reflects_persisted_column(self, admin_session, captest_id):
        r = admin_session.get(f"{API}/reports/export/payments.csv?tenant_id={captest_id}",
                              headers={"X-Tenant-Id": str(captest_id)}, timeout=30)
        assert r.status_code == 200
        import csv
        import io
        rows = list(csv.reader(io.StringIO(r.text)))
        header = rows[0]
        assert "method" in header
        m_idx = header.index("method")
        pk_idx = header.index("provider")
        # Confirm demo_upi rows show 'upi', and mock rows show 'card' (from persisted column).
        seen_upi = seen_card = False
        for row in rows[1:]:
            if len(row) <= max(m_idx, pk_idx):
                continue
            if row[pk_idx] == "demo_upi":
                assert row[m_idx] == "upi", f"demo_upi row should have method=upi: {row}"
                seen_upi = True
            elif row[pk_idx] == "mock":
                # mock is card in our tests
                if row[m_idx] == "card":
                    seen_card = True
        assert seen_upi, "no demo_upi row found in reports CSV"
        assert seen_card, "no mock/card row found in reports CSV"

    def test_reconciliation_run_detail_method_summary_uses_column(self, admin_session, captest_id):
        h = {"X-Tenant-Id": str(captest_id)}
        r = admin_session.post(f"{API}/reconciliation/run?tenant_id={captest_id}&source=provider_pull",
                               headers=h, timeout=60)
        assert r.status_code == 200
        run_id = r.json()["id"]
        rd = admin_session.get(f"{API}/reconciliation/runs/{run_id}?tenant_id={captest_id}",
                               headers=h, timeout=30).json()
        assert "method_summary" in rd
        # method_summary keys are the persisted methods; must include at least 'card' or 'upi'.
        keys = set((rd.get("method_summary") or {}).keys())
        assert keys, f"empty method_summary: {rd.get('method_summary')}"
        # It should NOT be all-'card' when we know we have demo_upi payments in this tenant.
        assert "upi" in keys or any("upi" in (i.get("method") or "") for i in rd.get("items", [])), \
            f"reconciliation method_summary missing 'upi' despite demo_upi payments: {keys}"


# ============================================================ GAP3: LIVE SAFETY
class TestLiveSafety:
    def test_live_demo_upi_rejected(self, admin_session, captest_id):
        body = {"reference": f"TEST_LIVE_UPI_{uuid.uuid4().hex[:6]}", "amount_minor": 1000,
                "currency": "INR", "provider_key": "demo_upi", "environment": "live",
                "payment_method": "upi", "flow": "direct",
                "idempotency_key": f"TEST_live_upi_{uuid.uuid4().hex[:8]}"}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body,
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        assert "live" in r.text.lower()

    def test_live_no_account_rejected(self, admin_session, captest_id):
        # examplepsp supports live but no account is configured on captest.
        body = {"reference": f"TEST_LIVE_NOACCT_{uuid.uuid4().hex[:6]}", "amount_minor": 1000,
                "currency": "USD", "provider_key": "examplepsp", "environment": "live",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_live_noacct_{uuid.uuid4().hex[:8]}"}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body,
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        assert "no live provider account" in r.text.lower() or "account" in r.text.lower()

    def test_live_account_without_credentials_rejected(self, admin_session, captest_id, db_conn):
        # Configure examplepsp mode=live on captest WITHOUT credentials, then attempt a live payment.
        h = {"X-Tenant-Id": str(captest_id)}
        # Clean any leftover from previous flaky runs
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM payment_providers WHERE tenant_id=%s AND provider_key='examplepsp' AND mode='live'",
                        (captest_id,))
        db_conn.commit()

        r = admin_session.post(f"{API}/providers?tenant_id={captest_id}",
                               json={"provider_key": "examplepsp", "display_name": "TEST ExamplePSP Live",
                                     "mode": "live", "enabled": True,
                                     "priority": 100, "supported_currencies": ["USD"],
                                     "payment_methods": ["card"], "supported_flows": ["direct"]},
                               headers=h, timeout=15)
        assert r.status_code == 200, r.text[:300]
        prov_id = r.json()["id"]
        try:
            body = {"reference": f"TEST_LIVE_NOCREDS_{uuid.uuid4().hex[:6]}", "amount_minor": 1500,
                    "currency": "USD", "provider_key": "examplepsp", "environment": "live",
                    "payment_method": "card", "flow": "direct",
                    "idempotency_key": f"TEST_live_nocreds_{uuid.uuid4().hex[:8]}"}
            r2 = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body,
                                    headers=h, timeout=20)
            assert r2.status_code == 400, f"expected 400, got {r2.status_code}: {r2.text[:200]}"
            msg = r2.text.lower()
            assert "credentials" in msg or "credential" in msg, \
                f"expected 'credentials' rejection, got: {r2.text[:200]}"
        finally:
            # Cleanup — delete the created live provider account.
            admin_session.delete(f"{API}/providers/{prov_id}?tenant_id={captest_id}",
                                 headers=h, timeout=15)


# ============================================================ Unsupported capability rejection
class TestUnsupportedCapability:
    def test_unsupported_currency_rejected(self, admin_session, captest_id):
        # mock supports USD/EUR/GBP/INR/AED, NOT JPY.
        body = {"reference": f"TEST_CAP_{uuid.uuid4().hex[:6]}", "amount_minor": 1000,
                "currency": "JPY", "provider_key": "mock", "environment": "sandbox",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_cap_{uuid.uuid4().hex[:8]}"}
        r = admin_session.post(f"{API}/payments?tenant_id={captest_id}", json=body,
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        assert "cannot process" in r.text.lower() or "currency" in r.text.lower() \
            or "capabilit" in r.text.lower(), r.text[:200]


# ============================================================ GAP4: INVALID WEBHOOK
class TestInvalidWebhook:
    def test_malformed_body_returns_400(self):
        r = requests.post(f"{API}/providers/mock/webhook",
                          data="not-json-{{",
                          headers={"Content-Type": "application/json"}, timeout=15)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"

    def test_unknown_provider_txn_id_returns_unmatched(self):
        r = requests.post(f"{API}/providers/mock/webhook",
                          json={"event_type": "payment.succeeded",
                                "provider_txn_id": f"UNKNOWN_{uuid.uuid4().hex}",
                                "status": "succeeded"}, timeout=15)
        assert r.status_code == 200
        assert r.json().get("unmatched") is True, r.json()

    def test_unknown_provider_key_returns_404(self):
        r = requests.post(f"{API}/providers/nonexistent_xyz/webhook",
                          json={"event_type": "x"}, timeout=15)
        assert r.status_code == 404


# ============================================================ TENANT ISOLATION
class TestTenantIsolation:
    def test_ops_cannot_read_captest_payments(self, ops_session, captest_id):
        r = ops_session.get(f"{API}/payments?tenant_id={captest_id}",
                            headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code in (403, 404)

    def test_ops_cannot_read_captest_ledger(self, ops_session, captest_id):
        r = ops_session.get(f"{API}/ledger/entries?tenant_id={captest_id}",
                            headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code in (403, 404)


# ============================================================ DEMO UPI regression
class TestDemoUpiStillWorks:
    def test_demo_upi_success_flow(self, admin_session, captest_id):
        s = admin_session.post(f"{API}/checkout/sessions?tenant_id={captest_id}",
                               json={"reference": f"TEST_UPI_REG_{uuid.uuid4().hex[:6]}",
                                     "amount_minor": 5500, "currency": "INR",
                                     "provider_key": "demo_upi"},
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20).json()
        rp = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                           json={"upi_app": "phonepe", "outcome": "success"}, timeout=30)
        assert rp.status_code == 200
        j = rp.json()
        assert j["status"] == "paid"

    def test_demo_upi_failed_outcome_no_payment(self, admin_session, captest_id):
        s = admin_session.post(f"{API}/checkout/sessions?tenant_id={captest_id}",
                               json={"reference": f"TEST_UPI_REGF_{uuid.uuid4().hex[:6]}",
                                     "amount_minor": 4400, "currency": "INR",
                                     "provider_key": "demo_upi"},
                               headers={"X-Tenant-Id": str(captest_id)}, timeout=20).json()
        rp = requests.post(f"{API}/public/checkout/{s['token']}/upi/pay",
                           json={"upi_app": "gpay", "outcome": "failed"}, timeout=15)
        assert rp.status_code == 200
        assert rp.json()["status"] == "simulated"


# ============================================================ CLEANUP (module-scoped teardown)
@pytest.fixture(scope="module", autouse=True)
def _cleanup(db_conn):
    yield
    # Remove any TEST_/ASYNC_ prefixed rows we may have created.
    try:
        with db_conn.cursor() as cur:
            cur.execute("""
                DELETE FROM ledger_entries WHERE ref_type='payment' AND ref_id IN (
                    SELECT id FROM payments WHERE reference LIKE 'ASYNC_TEST_%'
                )""")
            cur.execute("DELETE FROM payments WHERE reference LIKE 'ASYNC_TEST_%'")
        db_conn.commit()
    except Exception:
        db_conn.rollback()
