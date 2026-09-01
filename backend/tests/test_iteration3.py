"""Iteration 3: Checkout Branding, Webhook Retry/Replay, Scheduled Reports."""
import io
import os
import time
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = "admin@cloudpay.io"
ADMIN_PASSWORD = "Admin@12345"


def _extract_cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin_client():
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    tok = _extract_cookie(r, "access_token")
    c.headers.update({"Authorization": f"Bearer {tok}"})
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme_id(admin_client):
    r = admin_client.get("/api/tenants")
    return next(t for t in r.json() if t["slug"] == "acme")["id"]


# ---- Branding ----
class TestBranding:
    def test_patch_accent_color(self, admin_client, acme_id):
        r = admin_client.patch(f"/api/tenants/{acme_id}/branding",
                               json={"brand_accent": "#10b981"})
        assert r.status_code == 200, r.text
        assert r.json()["brand_accent"] == "#10b981"

    def test_upload_logo_and_public_serve(self, admin_client, acme_id):
        # 1x1 PNG bytes
        png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
               b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
               b"\x00\x00\x00\x03\x00\x01\x5cH\x5f\x9e\x00\x00\x00\x00IEND\xaeB`\x82")
        files = {"file": ("TEST_logo.png", io.BytesIO(png), "image/png")}
        r = admin_client.post(f"/api/tenants/{acme_id}/logo", files=files)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["file_id"]
        assert body["logo_url"].startswith("/api/public/files/")
        file_id = body["file_id"]

        # Public serve (unauthenticated)
        r2 = httpx.get(f"{BASE}/api/public/files/{file_id}", timeout=15)
        assert r2.status_code == 200
        assert r2.headers["content-type"].startswith("image/")
        assert r2.content == png

    def test_public_checkout_returns_branding(self, admin_client, acme_id):
        # Create checkout session
        r = admin_client.post(f"/api/checkout/sessions?tenant_id={acme_id}",
                              json={"amount_minor": 1234, "currency": "USD",
                                    "description": "branding test"})
        assert r.status_code == 200
        token = r.json()["token"]

        r2 = httpx.get(f"{BASE}/api/public/checkout/{token}", timeout=15)
        assert r2.status_code == 200
        data = r2.json()
        assert "brand_accent" in data
        assert data["brand_accent"] == "#10b981"
        assert "logo_url" in data and data["logo_url"]

    def test_bad_image_rejected(self, admin_client, acme_id):
        files = {"file": ("bad.exe", io.BytesIO(b"not an image"), "application/octet-stream")}
        r = admin_client.post(f"/api/tenants/{acme_id}/logo", files=files)
        assert r.status_code == 400


# ---- Webhook Retries / Replay ----
class TestWebhookRetries:
    @pytest.fixture(scope="class")
    def endpoint_500(self, admin_client, acme_id):
        r = admin_client.post(f"/api/webhooks/endpoints?tenant_id={acme_id}",
                              json={"url": "https://httpbin.org/status/500",
                                    "description": "TEST_wh_500",
                                    "events": ["payment.succeeded"]})
        assert r.status_code == 200
        ep_id = r.json()["id"]
        yield ep_id
        admin_client.delete(f"/api/webhooks/endpoints/{ep_id}")

    @pytest.fixture(scope="class")
    def endpoint_400(self, admin_client, acme_id):
        r = admin_client.post(f"/api/webhooks/endpoints?tenant_id={acme_id}",
                              json={"url": "https://httpbin.org/status/400",
                                    "description": "TEST_wh_400",
                                    "events": ["payment.succeeded"]})
        assert r.status_code == 200
        ep_id = r.json()["id"]
        yield ep_id
        admin_client.delete(f"/api/webhooks/endpoints/{ep_id}")

    def test_500_marks_retrying(self, admin_client, acme_id, endpoint_500):
        r = admin_client.post(f"/api/webhooks/endpoints/{endpoint_500}/test")
        assert r.status_code == 200
        time.sleep(1)
        r2 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        deliveries = [d for d in r2.json() if d.get("target_url") == "https://httpbin.org/status/500"]
        assert deliveries, "no delivery found"
        d = deliveries[0]
        assert d["status"] == "retrying", d
        assert d["retryable"] is True
        assert d["next_attempt_at"] is not None
        assert d["max_attempts"] == 8
        assert d["attempts"] >= 1

    def test_400_marks_failed_no_retry(self, admin_client, acme_id, endpoint_400):
        r = admin_client.post(f"/api/webhooks/endpoints/{endpoint_400}/test")
        assert r.status_code == 200
        time.sleep(1)
        r2 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        deliveries = [d for d in r2.json() if d.get("target_url") == "https://httpbin.org/status/400"]
        assert deliveries, "no 400 delivery found"
        d = deliveries[0]
        assert d["status"] == "failed", d
        assert d["retryable"] is False
        assert d["next_attempt_at"] is None

    def test_replay_preserves_event_id_and_audit(self, admin_client, acme_id, endpoint_500):
        # Get a retrying delivery
        r = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        target = next(d for d in r.json() if d.get("target_url") == "https://httpbin.org/status/500")
        original_id = target["id"]
        original_event_id = target["event_id"]

        # Count payments before
        rp = admin_client.get(f"/api/payments?tenant_id={acme_id}")
        assert rp.status_code == 200
        payments_before = len(rp.json())

        # Replay
        r2 = admin_client.post(f"/api/webhooks/deliveries/{original_id}/replay")
        assert r2.status_code == 200
        body = r2.json()
        assert body["id"] != original_id
        assert body["event_id"] == original_event_id

        # Verify new row exists as is_replay
        r3 = admin_client.get(f"/api/webhooks/deliveries?tenant_id={acme_id}")
        new_row = next((d for d in r3.json() if d["id"] == body["id"]), None)
        assert new_row is not None
        assert new_row["is_replay"] is True
        assert new_row["event_id"] == original_event_id

        # Payments count unchanged (replay must not re-process)
        rp2 = admin_client.get(f"/api/payments?tenant_id={acme_id}")
        assert len(rp2.json()) == payments_before

        # Audit contains webhook.replay
        ra = admin_client.get(f"/api/audit?tenant_id={acme_id}")
        assert ra.status_code == 200
        entries = ra.json()
        assert any(e.get("action") == "webhook.replay" for e in entries), \
            "webhook.replay audit entry missing"


# ---- Scheduled Reports ----
class TestScheduledReports:
    def test_run_now_and_download(self, admin_client, acme_id):
        r = admin_client.post(f"/api/reports/scheduled/run?tenant_id={acme_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "payments_count" in body
        assert "settlements_count" in body
        assert body["email_status"] == "skipped_no_provider"
        assert body["file_id"]

        # List includes it
        r2 = admin_client.get(f"/api/reports/scheduled?tenant_id={acme_id}")
        assert r2.status_code == 200
        rows = r2.json()
        assert any(row["id"] == body["id"] for row in rows)
        row = next(row for row in rows if row["id"] == body["id"])
        assert row["email_status"] == "skipped_no_provider"
        assert row["recipient_email"]  # tenant contact email

        # Download CSV
        r3 = admin_client.get(f"/api/reports/scheduled/download/{body['file_id']}")
        assert r3.status_code == 200
        assert "text/csv" in r3.headers.get("content-type", "")
        text = r3.text
        assert "PAYMENTS" in text
        assert "SETTLEMENTS" in text

    def test_download_requires_auth(self, admin_client, acme_id):
        r = admin_client.post(f"/api/reports/scheduled/run?tenant_id={acme_id}")
        file_id = r.json()["file_id"]
        r2 = httpx.get(f"{BASE}/api/reports/scheduled/download/{file_id}", timeout=15)
        assert r2.status_code == 401

    def test_public_files_rejects_report(self, admin_client, acme_id):
        r = admin_client.post(f"/api/reports/scheduled/run?tenant_id={acme_id}")
        file_id = r.json()["file_id"]
        # Report should NOT be served through the logo-only public endpoint
        r2 = httpx.get(f"{BASE}/api/public/files/{file_id}", timeout=15)
        assert r2.status_code == 404
