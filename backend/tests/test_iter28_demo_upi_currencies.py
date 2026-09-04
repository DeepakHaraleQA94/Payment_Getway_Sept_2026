"""Iteration 28: Currency catalog + Demo UPI checkout + Health badge."""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def tenant_id(session):
    r = session.get(f"{API}/tenants", timeout=15)
    assert r.status_code == 200, r.text[:200]
    tenants = r.json()
    for t in tenants:
        if t.get("slug") != "platform":
            return t["id"]
    return tenants[0]["id"]


# ---- Currency catalog ----
class TestCurrencies:
    def test_requires_auth(self):
        r = requests.get(f"{API}/currencies", timeout=15)
        assert r.status_code in (401, 403), f"Expected 401/403 got {r.status_code}"

    def test_list(self, session):
        r = session.get(f"{API}/currencies", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 50
        codes = {c["code"]: c for c in data}
        assert "INR" in codes and "JPY" in codes and "USD" in codes
        assert codes["INR"]["name"] == "Indian Rupee"
        assert codes["INR"]["decimals"] == 2
        assert codes["JPY"]["decimals"] == 0
        for c in data:
            assert set(c.keys()) >= {"code", "name", "decimals", "symbol"}


# ---- Demo UPI checkout ----
class TestDemoUpiCheckout:
    def test_create_session(self, session, tenant_id):
        body = {
            "reference": f"TEST_upi_{uuid.uuid4().hex[:8]}",
            "amount_minor": 12500,
            "currency": "INR",
            "provider_key": "demo_upi",
            "description": "demo upi test",
        }
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        assert r.status_code in (200, 201), f"{r.status_code} {r.text[:300]}"
        s = r.json()
        assert s.get("provider_key") == "demo_upi"
        assert s.get("currency") == "INR"
        assert s.get("token")
        pytest.demo_upi_token = s["token"]
        pytest.demo_upi_token2 = None
        pytest.demo_upi_session_id = s.get("id")

    def test_public_get_session(self):
        r = requests.get(f"{API}/public/checkout/{pytest.demo_upi_token}", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["provider_key"] == "demo_upi"
        assert data["currency"] == "INR"

    def test_upi_info(self):
        r = requests.get(f"{API}/public/checkout/{pytest.demo_upi_token}/upi", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "vpa" in data and data["vpa"]
        assert "upi_link" in data and data["upi_link"].startswith("upi://pay?")
        apps = data.get("apps", [])
        keys = {a["key"] for a in apps}
        assert {"phonepe", "gpay", "paytm", "bhim", "qr"}.issubset(keys)

    def test_upi_info_wrong_provider(self, session, tenant_id):
        # create a card session, then hit /upi -> 400
        body = {"reference": f"TEST_card_{uuid.uuid4().hex[:8]}",
                "amount_minor": 5000, "currency": "USD", "provider_key": "mock"}
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        assert r.status_code in (200, 201), r.text[:200]
        tok = r.json()["token"]
        pytest.card_token = tok
        r2 = requests.get(f"{API}/public/checkout/{tok}/upi", timeout=15)
        assert r2.status_code == 400

    def test_upi_pay_simulated_failed(self, session, tenant_id):
        # separate session
        body = {"reference": f"TEST_upi_{uuid.uuid4().hex[:8]}", "amount_minor": 7500,
                "currency": "INR", "provider_key": "demo_upi"}
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        tok = r.json()["token"]
        r2 = requests.post(f"{API}/public/checkout/{tok}/upi/pay",
                           json={"upi_app": "phonepe", "outcome": "failed"}, timeout=20)
        assert r2.status_code == 200, r2.text[:300]
        assert r2.json().get("status") == "simulated"
        # session should still be open
        rs = requests.get(f"{API}/public/checkout/{tok}", timeout=15)
        assert rs.json().get("status") != "paid"

    def test_upi_pay_simulated_pending(self, session, tenant_id):
        body = {"reference": f"TEST_upi_{uuid.uuid4().hex[:8]}", "amount_minor": 8800,
                "currency": "INR", "provider_key": "demo_upi"}
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        tok = r.json()["token"]
        r2 = requests.post(f"{API}/public/checkout/{tok}/upi/pay",
                           json={"upi_app": "gpay", "outcome": "pending"}, timeout=20)
        assert r2.status_code == 200
        assert r2.json().get("status") == "simulated"

    def test_upi_pay_success(self, session, tenant_id):
        body = {"reference": f"TEST_upi_{uuid.uuid4().hex[:8]}", "amount_minor": 15000,
                "currency": "INR", "provider_key": "demo_upi"}
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        tok = r.json()["token"]
        r2 = requests.post(f"{API}/public/checkout/{tok}/upi/pay",
                           json={"upi_app": "phonepe", "outcome": "success",
                                 "customer_email": "test@example.com"}, timeout=30)
        assert r2.status_code == 200, r2.text[:300]
        data = r2.json()
        assert data.get("status") == "paid"
        assert data.get("payment_id")
        # verify session is paid
        rs = requests.get(f"{API}/public/checkout/{tok}", timeout=15)
        assert rs.json().get("status") == "paid"

    def test_upi_pay_qr_success(self, session, tenant_id):
        body = {"reference": f"TEST_upiqr_{uuid.uuid4().hex[:8]}", "amount_minor": 9900,
                "currency": "INR", "provider_key": "demo_upi"}
        r = session.post(f"{API}/checkout/sessions", json=body,
                         headers={"X-Tenant-Id": str(tenant_id)}, timeout=20)
        tok = r.json()["token"]
        r2 = requests.post(f"{API}/public/checkout/{tok}/upi/pay",
                           json={"upi_app": "qr", "outcome": "success"}, timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("status") == "paid"


# ---- Provider health ----
class TestProviderHealth:
    def test_mock_health(self, session, tenant_id):
        r = session.get(f"{API}/providers/mock/health?environment=sandbox",
                        headers={"X-Tenant-Id": str(tenant_id)}, timeout=15)
        assert r.status_code == 200, r.text[:200]
        data = r.json()
        assert data.get("provider") == "mock"
        # should have some healthy indicator
        assert "status" in data or "healthy" in data or "ok" in data

    def test_demo_upi_health(self, session, tenant_id):
        r = session.get(f"{API}/providers/demo_upi/health?environment=sandbox",
                        headers={"X-Tenant-Id": str(tenant_id)}, timeout=15)
        assert r.status_code == 200
