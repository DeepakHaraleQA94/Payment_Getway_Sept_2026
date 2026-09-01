"""Tests for hosted checkout, API keys, webhooks and CSV export (iteration 2)."""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = "admin@cloudpay.io"
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _extract_cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin_token():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        return _extract_cookie(r, "access_token")


@pytest.fixture(scope="module")
def admin_client(admin_token):
    c = httpx.Client(base_url=BASE, timeout=30,
                     headers={"Authorization": f"Bearer {admin_token}"})
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme_id(admin_client):
    r = admin_client.get("/api/tenants")
    assert r.status_code == 200
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


# ---- API Keys ----
class TestApiKeys:
    def test_create_and_list_and_revoke(self, admin_client, acme_id):
        r = admin_client.post(f"/api/api-keys?tenant_id={acme_id}",
                              json={"label": f"TEST_key_{uuid.uuid4().hex[:6]}"})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["secret"].startswith("sk_test_")
        assert body["last4"] and len(body["last4"]) == 4
        key_id = body["id"]
        secret = body["secret"]

        # List should include the new key (without secret)
        r2 = admin_client.get(f"/api/api-keys?tenant_id={acme_id}")
        assert r2.status_code == 200
        assert any(k["id"] == key_id for k in r2.json())
        assert all("secret" not in k for k in r2.json())

        # Use secret via X-API-Key to create a checkout session
        r3 = admin_client.post(
            "/api/v1/checkout/sessions",
            headers={"X-API-Key": secret},
            json={"amount_minor": 4200, "currency": "USD", "description": "api key test"},
        )
        assert r3.status_code == 200, r3.text
        j = r3.json()
        assert j["token"] and j["checkout_url"].startswith("/checkout/")

        # Revoke
        r4 = admin_client.delete(f"/api/api-keys/{key_id}")
        assert r4.status_code == 200
        r5 = admin_client.get(f"/api/api-keys?tenant_id={acme_id}")
        assert next(k for k in r5.json() if k["id"] == key_id)["active"] is False

        # Revoked key should be rejected
        r6 = admin_client.post(
            "/api/v1/checkout/sessions",
            headers={"X-API-Key": secret},
            json={"amount_minor": 500, "currency": "USD"},
        )
        assert r6.status_code in (401, 403), r6.status_code

    def test_bad_api_key_rejected(self, admin_client):
        r = admin_client.post(
            "/api/v1/checkout/sessions",
            headers={"X-API-Key": "sk_test_invalidkeyvalue"},
            json={"amount_minor": 100, "currency": "USD"},
        )
        assert r.status_code in (401, 403)


# ---- Hosted Checkout ----
class TestHostedCheckout:
    def test_create_session_public_and_pay(self, admin_client, acme_id):
        r = admin_client.post(
            f"/api/checkout/sessions?tenant_id={acme_id}",
            json={"amount_minor": 5500, "currency": "USD", "description": "Hosted test"},
        )
        assert r.status_code == 200, r.text
        session = r.json()
        token = session["token"]
        assert session["status"] == "open"

        # Public GET
        r2 = httpx.get(f"{BASE}/api/public/checkout/{token}", timeout=15)
        assert r2.status_code == 200
        pub = r2.json()
        assert pub["amount_minor"] == 5500
        assert pub["merchant"]  # merchant name present
        assert pub["status"] == "open"

        # Public pay (no auth)
        r3 = httpx.post(f"{BASE}/api/public/checkout/{token}/pay",
                        json={"customer_email": "buyer@example.com"}, timeout=30)
        assert r3.status_code == 200, r3.text
        assert r3.json()["status"] == "paid"

        # Second pay attempt rejected
        r4 = httpx.post(f"{BASE}/api/public/checkout/{token}/pay",
                        json={"customer_email": "buyer@example.com"}, timeout=15)
        assert r4.status_code == 400
        assert "already" in r4.json().get("detail", "").lower()

        # Session now paid in listing
        r5 = admin_client.get(f"/api/checkout/sessions?tenant_id={acme_id}")
        assert any(s["token"] == token and s["status"] == "paid" for s in r5.json())

    def test_public_get_missing_token(self):
        r = httpx.get(f"{BASE}/api/public/checkout/nonexistent-token-xyz", timeout=15)
        assert r.status_code == 404


# ---- Webhooks ----
class TestWebhooks:
    def test_events_and_endpoint_lifecycle(self, admin_client, acme_id):
        r = admin_client.get("/api/webhooks/events")
        assert r.status_code == 200
        events = r.json()["events"]
        assert "payment.succeeded" in events

        # Create endpoint
        r2 = admin_client.post(
            f"/api/webhooks/endpoints?tenant_id={acme_id}",
            json={"url": "https://httpbin.org/post",
                  "description": "TEST_wh",
                  "events": ["payment.succeeded", "refund.succeeded"]},
        )
        assert r2.status_code == 200, r2.text
        ep_id = r2.json()["id"]

        # Trigger a test dispatch
        r3 = admin_client.post(f"/api/webhooks/endpoints/{ep_id}/test")
        assert r3.status_code == 200

        # Deliveries include a record for this tenant
        r4 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        assert r4.status_code == 200
        deliveries = r4.json()
        assert isinstance(deliveries, list)
        assert any(d["event"] == "payment.succeeded" for d in deliveries)

        # Auto-dispatch on payment creation
        rp = admin_client.post(
            f"/api/payments?tenant_id={acme_id}",
            json={"reference": "T-WH", "amount_minor": 700, "currency": "USD",
                  "provider_key": "mock"},
        )
        assert rp.status_code == 200
        pid = rp.json()["id"]

        # Slight delay for dispatch
        import time
        time.sleep(1.5)
        r5 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        deliveries = r5.json()
        # Should have at least one payment.succeeded delivery targeting our URL
        assert any(d["event"] == "payment.succeeded" and "httpbin" in (d.get("target_url") or "")
                   for d in deliveries), "No auto-dispatched payment.succeeded delivery found"

        # Refund → refund.succeeded webhook
        admin_client.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 200})
        time.sleep(1.5)
        r6 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        assert any(d["event"] == "refund.succeeded" for d in r6.json()), \
            "No refund.succeeded delivery found"

        # Cleanup
        admin_client.delete(f"/api/webhooks/endpoints/{ep_id}")


# ---- CSV Exports ----
class TestCsvExports:
    @pytest.mark.parametrize("path", ["payments.csv", "settlements.csv", "ledger.csv"])
    def test_csv_export(self, admin_client, acme_id, path):
        r = admin_client.get(f"/api/reports/export/{path}?tenant_id={acme_id}")
        assert r.status_code == 200, r.text
        ct = r.headers.get("content-type", "")
        assert "text/csv" in ct, ct
        cd = r.headers.get("content-disposition", "")
        assert path in cd
        # Header row present
        first_line = r.text.splitlines()[0]
        assert "," in first_line
        if path == "payments.csv":
            assert "reference" in first_line and "amount" in first_line

    def test_csv_requires_auth(self):
        r = httpx.get(f"{BASE}/api/reports/export/payments.csv", timeout=15)
        assert r.status_code == 401
