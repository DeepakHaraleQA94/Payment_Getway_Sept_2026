"""Focused tests: Settlement Detail drill-down (read-only) + login behavior."""
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


def _login_raw(email, password):
    with httpx.Client(base_url=BASE, timeout=30) as c:
        return c.post("/api/auth/login", json={"email": email, "password": password})


def _client(email, password):
    r = _login_raw(email, password)
    assert r.status_code == 200, r.text
    return httpx.Client(base_url=BASE, timeout=30,
                        headers={"Authorization": f"Bearer {_cookie(r, 'access_token')}"})


@pytest.fixture(scope="module")
def admin_client():
    c = _client(ADMIN_EMAIL, ADMIN_PASSWORD)
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme_id(admin_client):
    return next(t for t in admin_client.get("/api/tenants").json() if t["slug"] == "acme")["id"]


def _settlement(admin_client, acme_id):
    r = admin_client.post(f"/api/settlements/generate?tenant_id={acme_id}&currency=USD")
    assert r.status_code == 200
    return r.json()["id"]


class TestSettlementDetail:
    def test_authorized_view(self, admin_client, acme_id):
        sid = _settlement(admin_client, acme_id)
        r = admin_client.get(f"/api/settlements/{sid}?tenant_id={acme_id}")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == sid
        for k in ("reference", "currency", "gross_minor", "fees_minor", "net_minor",
                  "txn_count", "status", "created_at", "import_source", "reconciliation_context"):
            assert k in d

    def test_no_secrets_exposed(self, admin_client, acme_id):
        sid = _settlement(admin_client, acme_id)
        blob = admin_client.get(f"/api/settlements/{sid}?tenant_id={acme_id}").text.lower()
        for bad in ("password", "hashed_password", "secret", "api_key", "credential", "sk_test_"):
            assert bad not in blob

    def test_unknown_returns_404(self, admin_client, acme_id):
        r = admin_client.get(f"/api/settlements/{uuid.uuid4()}?tenant_id={acme_id}")
        assert r.status_code == 404

    def test_unauthenticated_rejected(self, acme_id):
        with httpx.Client(base_url=BASE, timeout=30) as c:
            r = c.get(f"/api/settlements/{uuid.uuid4()}")
            assert r.status_code in (401, 403)

    def test_cross_tenant_denied(self, admin_client, acme_id):
        # A user in another tenant cannot read acme's settlement.
        suffix = uuid.uuid4().hex[:8]
        tid = admin_client.post("/api/tenants", json={
            "name": f"SdTest {suffix}", "slug": f"sdtest-{suffix}", "country": "US",
            "default_currency": "USD", "contact_email": f"sd{suffix}@t.io"}).json()["id"]
        role_id = admin_client.post(f"/api/roles?tenant_id={tid}", json={
            "name": "Viewer", "description": "x", "permission_codes": ["settlement.manage"]}).json()["id"]
        email = f"sdview-{suffix}@t.io"
        admin_client.post(f"/api/users?tenant_id={tid}", json={
            "email": email, "name": "V", "password": "SdView-Passw0rd!", "role_id": role_id})
        sid = _settlement(admin_client, acme_id)
        other = _client(email, "SdView-Passw0rd!")
        try:
            assert other.get(f"/api/settlements/{sid}?tenant_id={tid}").status_code == 404
        finally:
            other.close()

    def test_read_only_no_ledger_mutation(self, admin_client, acme_id):
        sid = _settlement(admin_client, acme_id)
        before = len(admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json())
        admin_client.get(f"/api/settlements/{sid}?tenant_id={acme_id}")
        admin_client.get(f"/api/settlements/{sid}?tenant_id={acme_id}")
        after = len(admin_client.get(f"/api/ledger/entries?tenant_id={acme_id}").json())
        assert after == before


class TestLogin:
    def test_valid_superadmin_login(self):
        r = _login_raw(ADMIN_EMAIL, ADMIN_PASSWORD)
        assert r.status_code == 200 and _cookie(r, "access_token")

    def test_invalid_password_rejected(self):
        r = _login_raw(ADMIN_EMAIL, "definitely-wrong-password")
        assert r.status_code in (401, 429)  # 429 only if rate-limited

    def test_retired_old_admin_does_not_authenticate(self):
        r = _login_raw("admin@cloudpay.io", ADMIN_PASSWORD)
        assert r.status_code in (401, 429)  # account retired -> never 200
        assert r.status_code != 200

    def test_token_creation_and_me(self, admin_client):
        me = admin_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["is_superadmin"] is True
