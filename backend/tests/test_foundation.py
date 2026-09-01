"""Foundation integration tests for the CloudPay API.

Runs against the live backend on localhost:8001 (started by supervisor).
Covers health, auth, RBAC, tenant isolation, payment/fee/ledger and idempotency.
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def _extract_cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="session")
def admin_token():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        token = _extract_cookie(r, "access_token")
        assert token, "access_token cookie not set on login"
        return token


@pytest.fixture(scope="session")
def admin_client(admin_token):
    # Secure cookies are dropped over http://localhost, so authenticate via Bearer header.
    c = httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {admin_token}"})
    yield c
    c.close()


@pytest.fixture(scope="session")
def acme_tenant_id(admin_client):
    r = admin_client.get("/api/tenants")
    assert r.status_code == 200
    tenants = r.json()
    acme = next(t for t in tenants if t["slug"] == "acme")
    return acme["id"]


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "up"


def test_root(client):
    r = client.get("/api/")
    assert r.status_code == 200
    assert r.json()["service"] == "CloudPay"


def test_login_success_and_me(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == ADMIN_EMAIL
    assert body["is_superadmin"] is True
    assert "*" in body["permissions"]


def test_login_wrong_password():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-pass-xyz"})
        assert r.status_code == 401


def test_unauthenticated_blocked(client):
    r = client.get("/api/tenants")
    assert r.status_code == 401


def test_register_and_isolation():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        email = f"user_{uuid.uuid4().hex[:8]}@test.com"
        r = c.post("/api/auth/register", json={"email": email, "password": "Password123", "name": "T"})
        assert r.status_code == 200, r.text
        token = _extract_cookie(r, "access_token")
        assert token
        c.headers["Authorization"] = f"Bearer {token}"
        # Regular user (no tenant, no permissions) cannot create tenants.
        r2 = c.post("/api/tenants", json={"name": "X", "slug": f"x{uuid.uuid4().hex[:6]}"})
        assert r2.status_code == 403


def test_payment_and_fee(admin_client, acme_tenant_id):
    idem = f"idem-{uuid.uuid4().hex[:10]}"
    r = admin_client.post(
        f"/api/payments?tenant_id={acme_tenant_id}",
        json={"reference": "T-1", "amount_minor": 10000, "currency": "USD",
              "provider_key": "mock", "idempotency_key": idem},
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "succeeded"
    # 2.9% + 30 minor = 290 + 30 = 320
    assert p["fee_minor"] == 320
    assert p["net_minor"] == 9680

    # Idempotency: same key returns the same payment.
    r2 = admin_client.post(
        f"/api/payments?tenant_id={acme_tenant_id}",
        json={"reference": "T-1", "amount_minor": 10000, "currency": "USD",
              "provider_key": "mock", "idempotency_key": idem},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == p["id"]


def test_sandbox_decline(admin_client, acme_tenant_id):
    # Mock rule: amount minor % 100 == 13 declines.
    r = admin_client.post(
        f"/api/payments?tenant_id={acme_tenant_id}",
        json={"reference": "T-DECL", "amount_minor": 113, "currency": "USD", "provider_key": "mock"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "failed"


def test_refund_flow(admin_client, acme_tenant_id):
    r = admin_client.post(
        f"/api/payments?tenant_id={acme_tenant_id}",
        json={"reference": "T-REF", "amount_minor": 5000, "currency": "USD", "provider_key": "mock"},
    )
    pid = r.json()["id"]
    r2 = admin_client.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 2000, "reason": "test"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "succeeded"
    # Over-refund rejected.
    r3 = admin_client.post(f"/api/payments/{pid}/refunds", json={"amount_minor": 999999})
    assert r3.status_code == 400


def test_ledger_and_turnover(admin_client, acme_tenant_id):
    r = admin_client.get(f"/api/ledger/accounts?tenant_id={acme_tenant_id}")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    r2 = admin_client.get(f"/api/turnover?tenant_id={acme_tenant_id}")
    assert r2.status_code == 200
    assert r2.json()["txn_count"] >= 1


def test_audit_log_written(admin_client):
    r = admin_client.get("/api/audit")
    assert r.status_code == 200
    actions = {row["action"] for row in r.json()}
    assert "payment.create" in actions


def test_boundaries_disabled(admin_client):
    r = admin_client.get("/api/monitoring/services")
    assert r.status_code == 200
    b = r.json()["boundaries"]
    assert b["kyc_aml"]["configured"] is False
    assert b["vda"]["enabled"] is False
    assert b["ai_voice"]["enabled"] is False


def test_live_provider_blocked(admin_client, acme_tenant_id):
    # LIVE is architecturally supported but gated by plugin capability: the sandbox-only Mock
    # plugin cannot be configured for the live environment (rejected 400). LIVE is NOT
    # permanently removed from the architecture.
    r = admin_client.post(
        f"/api/providers?tenant_id={acme_tenant_id}",
        json={"provider_key": "mock", "display_name": "Mock Gateway", "mode": "live"},
    )
    assert r.status_code == 400
