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
    thr = {"success_rate_threshold": 0.5, "min_sample": 5}
    unhealthy = {"enabled": True, "health_status": "unsupported_environment",
                 "metrics": {"total": 0, "success_rate": None, "succeeded": 0}}
    assert _evaluate_account(unhealthy, thr)[:2] == (True, "critical")

    low_rate = {"enabled": True, "health_status": "up",
                "metrics": {"total": 10, "success_rate": 0.2, "succeeded": 2}}
    assert _evaluate_account(low_rate, thr)[:2] == (True, "warning")

    healthy = {"enabled": True, "health_status": "up",
               "metrics": {"total": 10, "success_rate": 0.9, "succeeded": 9}}
    assert _evaluate_account(healthy, thr)[0] is False

    disabled = {"enabled": False, "health_status": "up",
                "metrics": {"total": 10, "success_rate": 0.0, "succeeded": 0}}
    assert _evaluate_account(disabled, thr)[0] is False  # operator-disabled: not alerted

    low_sample = {"enabled": True, "health_status": "up",
                  "metrics": {"total": 2, "success_rate": 0.0, "succeeded": 0}}
    assert _evaluate_account(low_sample, thr)[0] is False  # below min sample


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
    # End-state: provider is alerting (robust to the background eval job racing this call).
    assert any(a["provider_key"] == "mock" and a["severity"] == "warning" for a in ev1["active_alerts"])
    # The "alerting" transition was recorded (by this call or the background job) in history.
    hist1 = admin.get(f"/api/providers/alerts/history?tenant_id={alert_tenant}&limit=20").json()
    assert any(h["transition"] == "alerting" and h["provider_key"] == "mock" for h in hist1)

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
    admin.post(f"/api/providers/alerts/evaluate?tenant_id={alert_tenant}")
    # End-state: no active alerts, and a "recovered" transition is on record. Robust to the
    # background health-eval job (60s interval) which may apply the transition first.
    assert admin.get(f"/api/providers/alerts?tenant_id={alert_tenant}").json() == []
    hist2 = admin.get(f"/api/providers/alerts/history?tenant_id={alert_tenant}&limit=20").json()
    assert any(h["transition"] == "recovered" and h["provider_key"] == "mock" for h in hist2)


def test_alerts_endpoint_never_exposes_secrets(admin, alert_tenant):
    r = admin.get(f"/api/providers/alerts?tenant_id={alert_tenant}")
    assert r.status_code == 200
    assert "credentials_ref" not in r.text and "sec_" not in r.text


def test_alert_settings_get_and_update(admin, alert_tenant):
    # Defaults from env initially.
    d = admin.get(f"/api/providers/alerts/settings?tenant_id={alert_tenant}").json()
    assert d["success_rate_threshold"] == 0.5 and d["min_sample"] == 5
    # Per-tenant override persists.
    u = admin.put(f"/api/providers/alerts/settings?tenant_id={alert_tenant}",
                  json={"success_rate_threshold": 0.8, "min_sample": 3})
    assert u.status_code == 200 and u.json()["success_rate_threshold"] == 0.8 and u.json()["min_sample"] == 3
    assert admin.get(f"/api/providers/alerts/settings?tenant_id={alert_tenant}").json()["min_sample"] == 3
    # Validation.
    assert admin.put(f"/api/providers/alerts/settings?tenant_id={alert_tenant}",
                     json={"success_rate_threshold": 1.5}).status_code == 400
    # Reset to defaults for other tests' determinism.
    admin.put(f"/api/providers/alerts/settings?tenant_id={alert_tenant}",
              json={"success_rate_threshold": 0.5, "min_sample": 5})
