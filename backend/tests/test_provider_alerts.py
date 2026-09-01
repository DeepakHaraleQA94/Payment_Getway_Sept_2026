"""Provider Health Alerts — threshold evaluation, notifications (dedupe), recovery.

Notifications reuse the existing email (noop by default) + outbound-webhook abstractions; no
external dependency. Run serially: `pytest tests/ -n0`.
"""
import os
import uuid

import httpx
import pytest

from app.services.alert_service import _evaluate_account

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


# ----------------------------- threshold logic (unit) -----------------------------
def test_evaluate_account_rules():
    unhealthy = {"enabled": True, "health_status": "unsupported_environment",
                 "metrics": {"total": 0, "success_rate": None, "succeeded": 0}}
    assert _evaluate_account(unhealthy)[:2] == (True, "critical")

    low_rate = {"enabled": True, "health_status": "up",
                "metrics": {"total": 10, "success_rate": 0.2, "succeeded": 2}}
    assert _evaluate_account(low_rate)[:2] == (True, "warning")

    healthy = {"enabled": True, "health_status": "up",
               "metrics": {"total": 10, "success_rate": 0.9, "succeeded": 9}}
    assert _evaluate_account(healthy)[0] is False

    disabled = {"enabled": False, "health_status": "up",
                "metrics": {"total": 10, "success_rate": 0.0, "succeeded": 0}}
    assert _evaluate_account(disabled)[0] is False  # operator-disabled: not alerted

    low_sample = {"enabled": True, "health_status": "up",
                  "metrics": {"total": 2, "success_rate": 0.0, "succeeded": 0}}
    assert _evaluate_account(low_sample)[0] is False  # below min sample


# ----------------------------- HTTP -----------------------------
def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    tok = _cookie(c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}), "access_token")
    assert tok
    c.headers["Authorization"] = f"Bearer {tok}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def alert_tenant(admin):
    slug = f"alert-{uuid.uuid4().hex[:8]}"
    tid = admin.post("/api/tenants", json={"name": "Alert Co", "slug": slug, "default_currency": "USD"}).json()["id"]
    admin.post(f"/api/providers?tenant_id={tid}",
               json={"provider_key": "mock", "display_name": "Mock", "mode": "sandbox",
                     "enabled": True, "priority": 10, "supported_currencies": ["USD"]})
    return tid


def _pay(admin, tid, amount, ref):
    return admin.post(f"/api/payments?tenant_id={tid}",
                      json={"reference": ref, "amount_minor": amount, "currency": "USD",
                            "provider_key": "mock", "environment": "sandbox",
                            "idempotency_key": f"{ref}-{uuid.uuid4().hex}"})


def test_alert_fires_on_low_success_rate_then_dedupes_then_recovers(admin, alert_tenant):
    # 6 declines (amount % 100 == 13) -> 0% success over >= min_sample -> warning alert.
    for i in range(6):
        _pay(admin, alert_tenant, 1000 + i * 100 + 13, f"DECL-{i}")

    ev1 = admin.post(f"/api/providers/alerts/evaluate?tenant_id={alert_tenant}").json()
    assert any(a["provider_key"] == "mock" and a["severity"] == "warning" for a in ev1["active_alerts"])
    assert any(c["transition"] == "alerting" for c in ev1["changes"])

    # Second evaluation must NOT re-fire (dedupe): still active, but no new transition.
    ev2 = admin.post(f"/api/providers/alerts/evaluate?tenant_id={alert_tenant}").json()
    assert ev2["changes"] == []
    assert any(a["provider_key"] == "mock" for a in ev2["active_alerts"])

    # GET active alerts reflects the alerting state.
    active = admin.get(f"/api/providers/alerts?tenant_id={alert_tenant}").json()
    assert any(a["provider_key"] == "mock" for a in active)

    # Add enough successes to push success rate back above threshold -> recovery notice.
    for i in range(10):
        _pay(admin, alert_tenant, 5000 + i, f"OK-{i}")
    ev3 = admin.post(f"/api/providers/alerts/evaluate?tenant_id={alert_tenant}").json()
    assert any(c["transition"] == "recovered" for c in ev3["changes"])
    assert admin.get(f"/api/providers/alerts?tenant_id={alert_tenant}").json() == []


def test_alerts_endpoint_never_exposes_secrets(admin, alert_tenant):
    r = admin.get(f"/api/providers/alerts?tenant_id={alert_tenant}")
    assert r.status_code == 200
    assert "credentials_ref" not in r.text and "sec_" not in r.text
