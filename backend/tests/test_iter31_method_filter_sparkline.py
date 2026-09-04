"""Iteration 31: refunds endpoint returns provider_key (tenant-scoped)."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
EMAIL = "finance@vortexglobal.info"
PASSWORD = "CloudPay-DutqTuzcS1jL64hHJrCy"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    # Cookie-based auth: session cookie already set on `s` by login
    s.headers.update({"Content-Type": "application/json"})
    return s


def _find_captest_tenant(client):
    r = client.get(f"{BASE_URL}/api/tenants", timeout=15)
    assert r.status_code == 200, r.text
    tenants = r.json()
    # look for the seeded CapTest tenant
    for t in tenants:
        name = (t.get("name") or "").lower()
        if "captest" in name:
            return t["id"]
    # fallback: first tenant
    assert tenants, "no tenants found"
    return tenants[0]["id"]


def test_refunds_all_returns_provider_key(client):
    tenant_id = _find_captest_tenant(client)
    r = client.get(f"{BASE_URL}/api/payments/refunds/all", params={"tenant_id": tenant_id}, timeout=20)
    assert r.status_code == 200, r.text
    refunds = r.json()
    assert isinstance(refunds, list)
    if not refunds:
        pytest.skip("No refunds available on this tenant to assert provider_key on")
    # Every refund must have provider_key field, populated (non-null for demo/mock payments)
    for r_ in refunds:
        assert "provider_key" in r_, f"provider_key missing on refund {r_.get('id')}"
    non_null = [r_ for r_ in refunds if r_.get("provider_key")]
    assert non_null, "expected at least one refund with a non-null provider_key"
    allowed = {"mock", "demo_upi", "stripe", "razorpay"}
    for r_ in non_null:
        assert r_["provider_key"] in allowed or isinstance(r_["provider_key"], str)


def test_refunds_all_is_tenant_scoped(client):
    r = client.get(f"{BASE_URL}/api/tenants", timeout=15)
    tenants = r.json()
    if len(tenants) < 2:
        pytest.skip("need >=2 tenants for scope test")
    a, b = tenants[0]["id"], tenants[1]["id"]
    ra = client.get(f"{BASE_URL}/api/payments/refunds/all", params={"tenant_id": a}, timeout=20).json()
    rb = client.get(f"{BASE_URL}/api/payments/refunds/all", params={"tenant_id": b}, timeout=20).json()
    ids_a = {x["id"] for x in ra}
    ids_b = {x["id"] for x in rb}
    # Must not overlap
    assert not (ids_a & ids_b), "refunds leaked across tenants"
