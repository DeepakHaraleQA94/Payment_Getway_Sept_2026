"""Iteration 35 — Razorpay ISOLATED real-PSP plugin verification.

Confirms:
  * PLUGIN CONTRACT + DISCOVERY via /api/providers/available
  * test-connection (sandbox up / live-no-creds invalid_credentials / live-with-creds up)
  * SANDBOX card DIRECT payment -> succeeded synchronously w/ exactly one ledger credit
  * SANDBOX UPI ASYNC via amount tail 11 -> pending, then VALID HMAC-SHA256 webhook -> succeeded
    with exactly one ledger credit == net_minor (payment_method persists as 'upi')
  * Duplicate webhook -> {already:'succeeded'} no second credit; bad signature -> 400
  * UPI INTENT / QR generation via generic /providers/razorpay/intent + /qr (no external calls)
  * Refund full -> refunded; partial + over-refund rejected
  * Status + reconcile endpoints work for a razorpay payment
  * Health: sandbox up, live up, foo -> unsupported_environment
  * LIVE SAFETY: no account -> 400; account with no credentials -> 400; never falls back
  * Capability enforcement: JPY / bad country rejected (400)
  * NO SECRET LEAKAGE: create razorpay provider with credentials stores only credentials_ref
  * No razorpay-specific code leaked into core engine/registry/wizard/webhook framework
  * LIVE httpx path structurally verified via mocks — NO real network calls

Uses the acme tenant (platform + acme are the only seeded tenants in this reset).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import uuid
from unittest.mock import patch, MagicMock

import psycopg2
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"

PG_DSN = "host=localhost dbname=cloudpay user=cloudpay password=cloudpay_local_pwd"


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASS)


@pytest.fixture(scope="module")
def tenants(admin):
    r = admin.get(f"{API}/tenants", timeout=15)
    assert r.status_code == 200
    return {t["slug"]: t for t in r.json()}


@pytest.fixture(scope="module")
def acme_id(tenants):
    return tenants["acme"]["id"]


@pytest.fixture(scope="module")
def db_conn():
    conn = psycopg2.connect(PG_DSN)
    yield conn
    try:
        conn.close()
    except Exception:
        pass


def _count_credits(conn, tid: str, pid: str):
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*), COALESCE(SUM(amount_minor),0)
                           FROM ledger_entries
                           WHERE tenant_id=%s AND ref_type='payment' AND ref_id=%s
                             AND direction='credit'""", (tid, pid))
            n, total = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return int(n), int(total or 0)


# =========================================================== DISCOVERY / CONTRACT
class TestDiscoveryAndContract:
    def test_razorpay_in_available(self, admin):
        r = admin.get(f"{API}/providers/available", timeout=15)
        assert r.status_code == 200
        plugins = {p["key"]: p for p in r.json()}
        assert "razorpay" in plugins, f"razorpay missing from discovery, got {set(plugins)}"
        rp = plugins["razorpay"]
        assert rp["display_name"] == "Razorpay"
        # payment methods
        for m in ("upi", "card", "netbanking", "wallet"):
            assert m in rp["payment_methods"], rp["payment_methods"]
        # supported flows (contract fields are strings like "direct","intent","qr")
        flows = set(rp["supported_flows"])
        for f in ("direct", "intent", "qr"):
            assert f in flows, f"missing flow {f}: {flows}"
        # environments
        assert set(rp["supported_environments"]) == {"sandbox", "live"}, rp["supported_environments"]
        # capability flags
        assert rp["supports_refund"] is True
        assert rp["supports_capture"] is True
        assert rp["supports_void"] is False
        assert rp["supports_webhooks"] is True
        assert rp["supports_intent"] is True
        assert rp["supports_qr"] is True
        # live_supported flag (if present)
        if "live_supported" in rp:
            assert rp["live_supported"] is True
        # required_credentials
        req = {c["key"] for c in rp["required_credentials"]}
        assert {"key_id", "key_secret", "webhook_secret"}.issubset(req), req


# =========================================================== TEST-CONNECTION
class TestTestConnection:
    def test_sandbox_up(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "razorpay", "mode": "sandbox"}, timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["status"] == "up"
        assert b["environment"] == "sandbox"

    def test_live_missing_creds_invalid(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "razorpay", "mode": "live"}, timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["status"] == "invalid_credentials", b
        detail = (b.get("detail") or "").lower()
        for k in ("key_id", "key_secret", "webhook_secret"):
            assert k in detail, f"missing '{k}' in detail: {detail}"

    def test_live_with_creds_up(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "razorpay", "mode": "live",
                             "credentials": {"key_id": "rzp_live_TEST",
                                             "key_secret": "TEST_secret",
                                             "webhook_secret": "TEST_wh"}}, timeout=15)
        assert r.status_code == 200
        b = r.json()
        assert b["status"] == "up", b

    def test_no_secret_echoed(self, admin):
        marker = f"SECRET_{uuid.uuid4().hex}"
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "razorpay", "mode": "live",
                             "credentials": {"key_id": marker,
                                             "key_secret": "sec_" + marker,
                                             "webhook_secret": "wh_" + marker}}, timeout=15)
        assert r.status_code == 200
        assert marker not in r.text

    def test_ops_forbidden(self):
        # Ensure provider.manage permission enforced — the endpoint is not anonymous.
        r = requests.post(f"{API}/providers/test-connection",
                          json={"provider_key": "razorpay", "mode": "sandbox"}, timeout=15)
        assert r.status_code in (401, 403), r.status_code


# =========================================================== HEALTH
class TestHealth:
    def test_sandbox_health_up(self, admin):
        r = admin.get(f"{API}/providers/razorpay/health?environment=sandbox", timeout=15)
        assert r.status_code == 200
        assert r.json()["status"] == "up"

    def test_live_health_up(self, admin):
        r = admin.get(f"{API}/providers/razorpay/health?environment=live", timeout=15)
        assert r.status_code == 200
        assert r.json()["status"] == "up"

    def test_unknown_env(self, admin):
        r = admin.get(f"{API}/providers/razorpay/health?environment=foo", timeout=15)
        assert r.status_code == 200
        assert r.json()["status"] == "unsupported_environment"


# =========================================================== SANDBOX CARD DIRECT
class TestSandboxCardDirect:
    def test_card_direct_succeeded_with_credit(self, admin, acme_id, db_conn):
        body = {"reference": f"TEST_RZP_CARD_{uuid.uuid4().hex[:6]}",
                "amount_minor": 25000, "currency": "INR", "country": "IN",
                "provider_key": "razorpay", "environment": "sandbox",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_rzp_card_{uuid.uuid4().hex[:8]}"}
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:400]
        p = r.json()
        assert p["status"] in ("succeeded", "captured"), p
        assert p["payment_method"] == "card"
        assert p["flow"] == "direct"
        assert p["provider_key"] == "razorpay"
        assert p["net_minor"] == p["amount_minor"] - p["fee_minor"]
        # Exactly one ledger credit == net_minor
        n, s = _count_credits(db_conn, acme_id, p["id"])
        assert n == 1, f"expected 1 ledger credit, got {n}"
        assert s == p["net_minor"], f"credit {s} != net {p['net_minor']}"
        pytest.rzp_card_pid = p["id"]  # for status/reconcile tests


# =========================================================== SANDBOX UPI ASYNC + WEBHOOK
class TestSandboxUpiAsyncWebhook:
    @pytest.fixture(scope="class")
    def upi_pending(self, admin, acme_id, db_conn):
        # Amount tail == 11 -> sandbox returns 'created' -> pending
        body = {"reference": f"TEST_RZP_UPI_{uuid.uuid4().hex[:6]}",
                "amount_minor": 30011, "currency": "INR", "country": "IN",
                "provider_key": "razorpay", "environment": "sandbox",
                "payment_method": "upi", "flow": "intent",
                "idempotency_key": f"TEST_rzp_upi_{uuid.uuid4().hex[:8]}"}
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:400]
        p = r.json()
        assert p["status"] == "pending", f"expected pending for tail=11, got {p['status']}: {p}"
        assert p["payment_method"] == "upi"
        assert p["flow"] == "intent"
        # No credits yet
        n0, _ = _count_credits(db_conn, acme_id, p["id"])
        assert n0 == 0
        # fetch provider_txn_id from DB
        ptxn = p.get("provider_txn_id")
        if not ptxn:
            with db_conn.cursor() as cur:
                cur.execute("SELECT provider_txn_id FROM payments WHERE id=%s", (p["id"],))
                ptxn = cur.fetchone()[0]
            db_conn.commit()
        return {"pid": p["id"], "ptxn": ptxn, "amount": p["amount_minor"]}

    def _sign_and_send(self, body_dict: dict, secret: str):
        """Sign with HMAC-SHA256 and POST via curl (per credentials note)."""
        raw = json.dumps(body_dict).encode()
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        # curl per the request note
        cmd = ["curl", "-s", "-o", "/tmp/rzp_wh_out.json", "-w", "%{http_code}",
               "-X", "POST", f"{API}/providers/razorpay/webhook",
               "-H", "Content-Type: application/json",
               "-H", f"X-Razorpay-Signature: {sig}",
               "--data-binary", raw.decode()]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        code = int(out.stdout.strip() or "0")
        try:
            with open("/tmp/rzp_wh_out.json") as f:
                data = json.loads(f.read() or "{}")
        except Exception:
            data = {}
        return code, data

    def test_valid_webhook_reconciles_and_credits_once(self, upi_pending, db_conn, acme_id):
        secret = "TEST_wh_secret_iter35"
        body = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": upi_pending["ptxn"], "status": "captured"}}},
            "_webhook_secret": secret,
        }
        code, data = self._sign_and_send(body, secret)
        assert code == 200, f"webhook returned {code}: {data}"
        # Verify payment now succeeded and ledger credit exists (net == amount - fee)
        with db_conn.cursor() as cur:
            cur.execute("SELECT status, amount_minor, fee_minor, net_minor FROM payments WHERE id=%s",
                        (upi_pending["pid"],))
            status, amt, fee, net = cur.fetchone()
        db_conn.commit()
        assert status == "succeeded", f"expected succeeded, got {status}"
        n, s = _count_credits(db_conn, acme_id, upi_pending["pid"])
        assert n == 1, f"expected 1 credit, got {n}"
        assert s == net, f"credit {s} != net {net}"

    def test_duplicate_webhook_no_second_credit(self, upi_pending, db_conn, acme_id):
        secret = "TEST_wh_secret_iter35"
        body = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": upi_pending["ptxn"], "status": "captured"}}},
            "_webhook_secret": secret,
        }
        code, data = self._sign_and_send(body, secret)
        assert code == 200
        assert data.get("already") == "succeeded", data
        n, _ = _count_credits(db_conn, acme_id, upi_pending["pid"])
        assert n == 1, f"duplicate produced extra credit: n={n}"

    def test_bad_signature_returns_400(self, upi_pending):
        secret = "TEST_wh_secret_iter35"
        body = {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": upi_pending["ptxn"], "status": "captured"}}},
            "_webhook_secret": secret,
        }
        # Sign with WRONG secret
        raw = json.dumps(body).encode()
        wrong_sig = hmac.new(b"WRONG", raw, hashlib.sha256).hexdigest()
        cmd = ["curl", "-s", "-o", "/tmp/rzp_wh_bad.json", "-w", "%{http_code}",
               "-X", "POST", f"{API}/providers/razorpay/webhook",
               "-H", "Content-Type: application/json",
               "-H", f"X-Razorpay-Signature: {wrong_sig}",
               "--data-binary", raw.decode()]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        code = int(out.stdout.strip() or "0")
        assert code == 400, f"expected 400, got {code}"
        with open("/tmp/rzp_wh_bad.json") as f:
            txt = f.read().lower()
        assert "invalid" in txt and ("signature" in txt or "payload" in txt), txt


# =========================================================== INTENT + QR
class TestIntentAndQr:
    def test_generate_intent(self, admin, acme_id):
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/providers/razorpay/intent",
                       json={"reference": f"TEST_INT_{uuid.uuid4().hex[:6]}",
                             "amount_minor": 5000, "currency": "INR"},
                       headers=h, timeout=15)
        assert r.status_code == 200, r.text[:300]
        b = r.json()
        assert "intent_id" in b
        assert b["intent_id"].startswith("order_sim_") or b["intent_id"]

    def test_generate_qr(self, admin, acme_id):
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/providers/razorpay/qr",
                       json={"reference": f"TEST_QR_{uuid.uuid4().hex[:6]}",
                             "amount_minor": 5000, "currency": "INR"},
                       headers=h, timeout=15)
        assert r.status_code == 200, r.text[:300]
        b = r.json()
        assert "qr_id" in b
        # QR payload should be upi:// scheme in sandbox
        payload = b.get("qr_payload") or ""
        assert payload.startswith("upi://"), f"expected upi:// payload, got {payload[:80]}"


# =========================================================== REFUND
class TestRefund:
    def test_full_refund(self, admin, acme_id):
        # Create a fresh card payment
        body = {"reference": f"TEST_RZP_REF_{uuid.uuid4().hex[:6]}",
                "amount_minor": 12000, "currency": "INR", "country": "IN",
                "provider_key": "razorpay", "environment": "sandbox",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_rzp_ref_{uuid.uuid4().hex[:8]}"}
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200, r.text[:300]
        p = r.json()
        assert p["status"] in ("succeeded", "captured")
        # Full refund
        rr = admin.post(f"{API}/payments/{p['id']}/refunds?tenant_id={acme_id}",
                        json={"amount_minor": p["amount_minor"], "reason": "TEST full"},
                        headers=h, timeout=30)
        assert rr.status_code == 200, rr.text[:300]
        pget = admin.get(f"{API}/payments/{p['id']}?tenant_id={acme_id}", headers=h, timeout=15).json()
        assert pget["status"] == "refunded", pget["status"]

    def test_partial_refund_and_over_refund_rejected(self, admin, acme_id):
        body = {"reference": f"TEST_RZP_PREF_{uuid.uuid4().hex[:6]}",
                "amount_minor": 20000, "currency": "INR", "country": "IN",
                "provider_key": "razorpay", "environment": "sandbox",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_rzp_pref_{uuid.uuid4().hex[:8]}"}
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body, headers=h, timeout=30)
        assert r.status_code == 200
        p = r.json()
        # Partial 5000
        r1 = admin.post(f"{API}/payments/{p['id']}/refunds?tenant_id={acme_id}",
                        json={"amount_minor": 5000, "reason": "TEST partial"},
                        headers=h, timeout=30)
        assert r1.status_code == 200, r1.text[:300]
        # Attempt to over-refund: remaining 15000, ask 20000 -> rejected
        r2 = admin.post(f"{API}/payments/{p['id']}/refunds?tenant_id={acme_id}",
                        json={"amount_minor": 20000, "reason": "TEST over"},
                        headers=h, timeout=30)
        assert r2.status_code == 400, f"expected 400 for over-refund, got {r2.status_code}: {r2.text[:200]}"


# =========================================================== STATUS + RECONCILE
class TestStatusAndReconcile:
    def test_status_endpoint(self, admin):
        pid = getattr(pytest, "rzp_card_pid", None)
        if not pid:
            pytest.skip("no razorpay card payment id from earlier test")
        # get the provider_txn_id via API
        r = admin.get(f"{API}/payments/{pid}", timeout=15)
        # tenant scoped - try both routes; if unavailable, fall back to DB via plugin endpoint
        # Directly test generic status endpoint (needs a provider_txn_id)
        conn = psycopg2.connect(PG_DSN)
        with conn.cursor() as cur:
            cur.execute("SELECT provider_txn_id FROM payments WHERE id=%s", (pid,))
            ptxn = cur.fetchone()[0]
        conn.close()
        rs = admin.get(f"{API}/providers/razorpay/status/{ptxn}", timeout=15)
        assert rs.status_code == 200, rs.text[:200]
        assert rs.json().get("normalized_status") in ("succeeded", "pending", "authorized"), rs.json()

    def test_reconcile_endpoint(self, admin):
        pid = getattr(pytest, "rzp_card_pid", None)
        if not pid:
            pytest.skip("no razorpay card payment id from earlier test")
        conn = psycopg2.connect(PG_DSN)
        with conn.cursor() as cur:
            cur.execute("SELECT provider_txn_id FROM payments WHERE id=%s", (pid,))
            ptxn = cur.fetchone()[0]
        conn.close()
        rr = admin.post(f"{API}/providers/razorpay/reconcile/{ptxn}", timeout=15)
        assert rr.status_code == 200, rr.text[:200]


# =========================================================== LIVE SAFETY
class TestLiveSafety:
    def test_live_no_account_rejected(self, admin, acme_id, db_conn):
        # Ensure no razorpay live account exists on acme
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM payment_providers WHERE tenant_id=%s AND provider_key='razorpay' AND mode='live'",
                        (acme_id,))
        db_conn.commit()
        body = {"reference": f"TEST_RZP_LIVE1_{uuid.uuid4().hex[:6]}",
                "amount_minor": 5000, "currency": "INR", "country": "IN",
                "provider_key": "razorpay", "environment": "live",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_rzp_live1_{uuid.uuid4().hex[:8]}"}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body,
                       headers={"X-Tenant-Id": str(acme_id)}, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        assert "live" in r.text.lower() or "account" in r.text.lower()

    def test_live_account_without_creds_rejected(self, admin, acme_id, db_conn):
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                       json={"provider_key": "razorpay",
                             "display_name": "TEST Razorpay Live",
                             "mode": "live", "enabled": True, "priority": 100,
                             "supported_currencies": ["INR"],
                             "supported_methods": ["card"],
                             "supported_flows": ["direct"]},
                       headers=h, timeout=15)
        assert r.status_code == 200, r.text[:300]
        prov_id = r.json()["id"]
        try:
            body = {"reference": f"TEST_RZP_LIVE2_{uuid.uuid4().hex[:6]}",
                    "amount_minor": 5000, "currency": "INR", "country": "IN",
                    "provider_key": "razorpay", "environment": "live",
                    "payment_method": "card", "flow": "direct",
                    "idempotency_key": f"TEST_rzp_live2_{uuid.uuid4().hex[:8]}"}
            r2 = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body, headers=h, timeout=20)
            assert r2.status_code == 400, r2.text[:300]
            assert "credential" in r2.text.lower(), r2.text[:300]
        finally:
            admin.delete(f"{API}/providers/{prov_id}?tenant_id={acme_id}", headers=h, timeout=15)


# =========================================================== CAPABILITY ENFORCEMENT
class TestCapabilityEnforcement:
    def test_unsupported_currency_rejected(self, admin, acme_id):
        body = {"reference": f"TEST_RZP_CAP_{uuid.uuid4().hex[:6]}",
                "amount_minor": 1000, "currency": "JPY", "country": "JP",
                "provider_key": "razorpay", "environment": "sandbox",
                "payment_method": "card", "flow": "direct",
                "idempotency_key": f"TEST_rzp_cap_{uuid.uuid4().hex[:8]}"}
        r = admin.post(f"{API}/payments?tenant_id={acme_id}", json=body,
                       headers={"X-Tenant-Id": str(acme_id)}, timeout=20)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
        low = r.text.lower()
        assert "cannot process" in low or "currency" in low or "capabilit" in low or "eligible" in low


# =========================================================== NO SECRET LEAKAGE
class TestSecretLeakage:
    def test_credentials_stored_as_ref_only(self, admin, acme_id, db_conn):
        # Clean prior sandbox razorpay for acme
        h = {"X-Tenant-Id": str(acme_id)}
        r0 = admin.get(f"{API}/providers?tenant_id={acme_id}", headers=h, timeout=15)
        for p in (r0.json() or []):
            if p["provider_key"] == "razorpay" and p["mode"] == "sandbox":
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={acme_id}", headers=h, timeout=15)
        marker = f"SECRET_{uuid.uuid4().hex}"
        r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                       json={"provider_key": "razorpay",
                             "display_name": "TEST Razorpay Sandbox Creds",
                             "mode": "sandbox", "enabled": True, "priority": 250,
                             "supported_currencies": ["INR"],
                             "supported_methods": ["card"],
                             "supported_flows": ["direct"],
                             "credentials": {"key_id": marker,
                                             "key_secret": "sec_" + marker,
                                             "webhook_secret": "wh_" + marker}},
                       headers=h, timeout=20)
        assert r.status_code == 200, r.text[:400]
        created = r.json()
        prov_id = created["id"]
        try:
            assert marker not in r.text, "raw credential leaked in create response"
            assert created.get("credentials_ref"), created
            # GET providers must not echo the raw secret
            r2 = admin.get(f"{API}/providers?tenant_id={acme_id}", headers=h, timeout=15)
            assert marker not in r2.text, "raw credential leaked in GET providers"
            # DB config JSON should not contain the raw secret
            with db_conn.cursor() as cur:
                cur.execute("SELECT config, credentials_ref FROM payment_providers WHERE id=%s",
                            (prov_id,))
                cfg, ref = cur.fetchone()
            db_conn.commit()
            assert marker not in json.dumps(cfg or {})
            assert ref, "credentials_ref should be persisted"
        finally:
            admin.delete(f"{API}/providers/{prov_id}?tenant_id={acme_id}", headers=h, timeout=15)


# =========================================================== NO CORE LEAKAGE (grep)
class TestNoCoreLeakage:
    def test_no_razorpay_in_core_engine(self):
        for path in ("/app/backend/app/services/payment_engine.py",
                     "/app/backend/app/providers/registry.py",
                     "/app/backend/app/routers/config.py"):
            with open(path) as f:
                src = f.read()
            # 'razorpay' may only appear as: (a) the /webhook/example URL doc line, or
            # (b) a comment/docstring. It must NOT appear as a code branch like
            # `provider_key == "razorpay"` or `if key == "razorpay"`.
            bad = [
                'provider_key == "razorpay"',
                'provider_key=="razorpay"',
                "provider_key == 'razorpay'",
                'key == "razorpay"',
            ]
            for pat in bad:
                assert pat not in src, f"core leaked razorpay-specific branch in {path}: {pat}"


# =========================================================== LIVE PATH MOCKED (structural)
class TestLivePathMocked:
    def test_live_create_calls_orders_endpoint(self):
        """Structurally verify the LIVE httpx path without hitting the real network."""
        from app.providers.razorpay_provider import RazorpayProvider
        from app.providers.contracts import ChargeRequest, ProviderConfiguration

        provider = RazorpayProvider()
        cfg = ProviderConfiguration(
            provider_key="razorpay", mode="live", credential_ref=None,
            options={"credentials": {"key_id": "rzp_live_TEST", "key_secret": "TEST"}})
        req = ChargeRequest(amount_minor=25000, currency="INR", reference="ref_1", metadata={})

        with patch("httpx.request") as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"id":"order_LiveMocked_1","status":"created"}'
            mock_resp.json.return_value = {"id": "order_LiveMocked_1", "status": "created"}
            mock_req.return_value = mock_resp

            result = provider.create_payment(req, config=cfg)
            assert mock_req.called, "httpx.request should be called for LIVE"
            args, kwargs = mock_req.call_args
            # method + URL
            assert args[0] == "POST"
            assert "api.razorpay.com/v1/orders" in args[1]
            # Basic auth uses (key_id, key_secret) — NOT sandbox fallback
            assert kwargs.get("auth") == ("rzp_live_TEST", "TEST")
            # returned pending (async), never falls back to sandbox mock
            assert result.status == "pending"
            assert result.provider_txn_id == "order_LiveMocked_1"

    def test_live_missing_creds_raises_provider_error(self):
        from app.providers.razorpay_provider import RazorpayProvider
        from app.providers.contracts import ChargeRequest, ProviderConfiguration, ProviderError

        provider = RazorpayProvider()
        cfg = ProviderConfiguration(
            provider_key="razorpay", mode="live", credential_ref=None,
            options={"credentials": {}})
        req = ChargeRequest(amount_minor=1000, currency="INR", reference="r", metadata={})
        with pytest.raises(ProviderError):
            provider.create_payment(req, config=cfg)


# =========================================================== TEARDOWN
@pytest.fixture(scope="module", autouse=True)
def _cleanup(db_conn, acme_id):
    yield
    try:
        with db_conn.cursor() as cur:
            cur.execute("""DELETE FROM ledger_entries WHERE ref_type='payment' AND ref_id IN (
                             SELECT id FROM payments WHERE reference LIKE 'TEST_RZP_%')""")
            cur.execute("DELETE FROM payments WHERE reference LIKE 'TEST_RZP_%'")
            cur.execute("DELETE FROM payment_providers WHERE display_name LIKE 'TEST Razorpay%'")
        db_conn.commit()
    except Exception:
        db_conn.rollback()
