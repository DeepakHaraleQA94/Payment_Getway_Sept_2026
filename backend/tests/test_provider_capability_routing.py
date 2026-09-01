"""Capability-aware, multi-country routing tests (generic provider architecture).

Uses only Mock + the example reference plugin (no real PSP, no real credentials). Covers
country/currency/method/flow capability matching for India/Sri Lanka/UK/USA, negative
rejections, sandbox/live separation, priority routing, failover, tenant isolation and
idempotency (failover never double-charges). Run serially: `pytest tests/ -n0`.
"""
import os
import uuid

import httpx
import pytest

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    c.headers["Authorization"] = f"Bearer {_cookie(c.post('/api/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD}), 'access_token')}"
    yield c
    c.close()


def _tenant(admin, country=None):
    slug = f"cap-{uuid.uuid4().hex[:8]}"
    body = {"name": "Cap Co", "slug": slug, "default_currency": "USD"}
    if country:
        body["country"] = country
    return admin.post("/api/tenants", json=body).json()["id"]


def _provider(admin, tid, *, key="mock", mode="sandbox", priority=100, currencies=None,
              countries=None, methods=None, flows=None):
    body = {"provider_key": key, "display_name": f"{key} {mode}", "mode": mode, "enabled": True,
            "priority": priority, "supported_currencies": currencies or [],
            "supported_countries": countries or [], "supported_methods": methods or [],
            "supported_flows": flows or []}
    r = admin.post(f"/api/providers?tenant_id={tid}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(admin, tid, **kw):
    body = {"reference": kw.pop("reference", f"R-{uuid.uuid4().hex[:6]}"),
            "amount_minor": kw.pop("amount_minor", 25000), "currency": kw.pop("currency", "USD"),
            "provider_key": kw.pop("provider_key", "auto"), "environment": kw.pop("environment", "sandbox")}
    body.update(kw)
    return admin.post(f"/api/payments?tenant_id={tid}", json=body)


# ----------------------------- positive country/currency/method/flow matches -----------------------------
def test_india_inr_upi_capability_match(admin):
    tid = _tenant(admin, country="IN")
    _provider(admin, tid, key="mock", currencies=["INR"], countries=["IN"],
              methods=["upi", "card"], flows=["intent", "qr", "direct"])
    r = _pay(admin, tid, currency="INR", country="IN", payment_method="upi", flow="qr",
             amount_minor=25000)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "succeeded"
    assert data["provider_key"] == "mock"
    trace = data["metadata"]["routing_trace"]
    assert any(t["provider_key"] == "mock" and t["selected"] for t in trace)


def test_srilanka_lkr_provider_match(admin):
    tid = _tenant(admin, country="LK")
    _provider(admin, tid, key="mock", currencies=["LKR"], countries=["LK"],
              methods=["card"], flows=["direct"])
    r = _pay(admin, tid, currency="LKR", country="LK", payment_method="card", flow="direct")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


def test_uk_gbp_provider_match(admin):
    tid = _tenant(admin, country="GB")
    _provider(admin, tid, key="mock", currencies=["GBP"], countries=["GB"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="GBP", country="GB", payment_method="card", flow="direct")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


def test_usa_usd_provider_match(admin):
    tid = _tenant(admin, country="US")
    _provider(admin, tid, key="mock", currencies=["USD"], countries=["US"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="USD", country="US", payment_method="card", flow="direct")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


# ----------------------------- negative rejections -----------------------------
def test_unsupported_currency_rejected(admin):
    tid = _tenant(admin, country="IN")
    _provider(admin, tid, key="mock", currencies=["INR"], countries=["IN"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="USD", country="IN", payment_method="card", flow="direct")
    assert r.status_code == 400 and "No eligible provider" in r.json()["detail"]


def test_unsupported_country_rejected(admin):
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", currencies=["USD"], countries=["US"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="USD", country="IN", payment_method="card", flow="direct")
    assert r.status_code == 400 and "No eligible provider" in r.json()["detail"]


def test_unsupported_payment_method_rejected(admin):
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", currencies=["USD"], countries=["US"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="USD", country="US", payment_method="upi", flow="direct")
    assert r.status_code == 400 and "No eligible provider" in r.json()["detail"]


def test_unsupported_flow_rejected(admin):
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", currencies=["USD"], countries=["US"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, currency="USD", country="US", payment_method="card", flow="qr")
    assert r.status_code == 400 and "No eligible provider" in r.json()["detail"]


def test_explicit_provider_capability_enforced(admin):
    """Explicit provider selection is also blocked when it cannot process the payment."""
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", currencies=["INR"], countries=["IN"], methods=["card"],
              flows=["direct"])
    r = _pay(admin, tid, provider_key="mock", currency="USD", country="IN", payment_method="card",
             flow="direct")
    assert r.status_code == 400 and "cannot process" in r.json()["detail"]


# ----------------------------- sandbox / live separation -----------------------------
def test_sandbox_live_separation(admin):
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", mode="sandbox", priority=10, currencies=["USD"],
              countries=["US"], methods=["card"], flows=["direct"])
    _provider(admin, tid, key="examplepsp", mode="live", priority=5, currencies=["USD"],
              countries=["US"], methods=["card"], flows=["direct"])
    # A sandbox payment must only ever consider the sandbox account (never the live one).
    r = _pay(admin, tid, currency="USD", country="US", environment="sandbox")
    assert r.status_code == 200, r.text
    trace = r.json()["metadata"]["routing_trace"]
    keys = {t["provider_key"] for t in trace}
    assert keys == {"mock"} and r.json()["provider_key"] == "mock"
    assert all(t["environment"] == "sandbox" for t in trace)
    # Mock (sandbox-only) can never be used for a live request.
    r2 = _pay(admin, tid, provider_key="mock", currency="USD", country="US", environment="live")
    assert r2.status_code == 400


# ----------------------------- priority + failover -----------------------------
def _dual_provider_tenant(admin):
    tid = _tenant(admin)
    _provider(admin, tid, key="mock", mode="sandbox", priority=10, currencies=["USD"],
              countries=["US"], methods=["card"], flows=["direct"])
    _provider(admin, tid, key="examplepsp", mode="sandbox", priority=20, currencies=["USD"],
              countries=["US"], methods=["card"], flows=["direct"])
    return tid


def test_priority_routing_picks_highest(admin):
    tid = _dual_provider_tenant(admin)
    r = _pay(admin, tid, currency="USD", country="US", amount_minor=5000)  # mock succeeds
    assert r.status_code == 200, r.text
    assert r.json()["provider_key"] == "mock"  # priority 10 wins
    assert r.json()["metadata"]["routing_attempts"][0]["provider_key"] == "mock"


def test_failover_to_next_provider(admin):
    tid = _dual_provider_tenant(admin)
    # amount ending 13 -> mock declines -> failover to examplepsp (priority 20) which succeeds.
    r = _pay(admin, tid, currency="USD", country="US", amount_minor=5013)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "succeeded"
    assert data["provider_key"] == "examplepsp"
    attempts = data["metadata"]["routing_attempts"]
    assert attempts[0]["provider_key"] == "mock" and attempts[0]["success"] is False
    assert any(a["provider_key"] == "examplepsp" and a["success"] for a in attempts)


def test_failover_is_idempotent_no_duplicate_charge(admin):
    tid = _dual_provider_tenant(admin)
    key = f"idem-{uuid.uuid4().hex}"
    r1 = _pay(admin, tid, currency="USD", country="US", amount_minor=5013, idempotency_key=key,
              reference="IDEM-1")
    r2 = _pay(admin, tid, currency="USD", country="US", amount_minor=5013, idempotency_key=key,
              reference="IDEM-1")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # same payment row: no duplicate charge
    # Exactly one payment exists for that idempotency key.
    listing = admin.get(f"/api/payments?tenant_id={tid}").json()
    rows = listing if isinstance(listing, list) else listing.get("items", listing)
    matching = [p for p in rows if p["id"] == r1.json()["id"]]
    assert len(matching) == 1


# ----------------------------- tenant isolation in routing -----------------------------
def test_routing_is_tenant_isolated(admin):
    tid_a = _tenant(admin, country="US")
    _provider(admin, tid_a, key="mock", currencies=["USD"], countries=["US"], methods=["card"],
              flows=["direct"])
    tid_b = _tenant(admin, country="US")  # no providers configured
    # Tenant A routes; tenant B has no eligible provider despite A being configured.
    assert _pay(admin, tid_a, currency="USD", country="US").status_code == 200
    rb = _pay(admin, tid_b, currency="USD", country="US")
    assert rb.status_code == 400 and "No eligible provider" in rb.json()["detail"]


def test_routing_trace_contains_no_secrets(admin):
    tid = _dual_provider_tenant(admin)
    r = _pay(admin, tid, currency="USD", country="US", amount_minor=5013)
    text = str(r.json()["metadata"]).lower()
    for leak in ("credential", "ciphertext", "secret", "api_key", "fernet"):
        assert leak not in text
