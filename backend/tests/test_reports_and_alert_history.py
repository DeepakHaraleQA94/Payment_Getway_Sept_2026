"""Weekly/monthly scheduled reports + provider alert recovery-log history.

Run serially: `pytest tests/ -n0`.
"""
import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.services.report_generation import _period_window

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


# ----------------------------- period window (unit) -----------------------------
def test_period_window_daily_weekly_monthly():
    anchor = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)  # a Monday
    ds, de, _ = _period_window("daily", anchor)
    assert (de - ds).days == 1 and ds.hour == 0

    ws, we, _ = _period_window("weekly", anchor)
    assert (we - ws).days == 7 and we.date() == anchor.date()

    ms, me, label = _period_window("monthly", anchor)
    assert ms.month == 5 and ms.day == 1  # previous calendar month (May)
    assert me.month == 6 and me.day == 1
    assert "May" in label


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
def tenant(admin):
    slug = f"rep-{uuid.uuid4().hex[:8]}"
    tid = admin.post("/api/tenants", json={"name": "Report Co", "slug": slug,
                                           "default_currency": "USD"}).json()["id"]
    admin.post(f"/api/providers?tenant_id={tid}",
               json={"provider_key": "mock", "display_name": "Mock", "mode": "sandbox",
                     "enabled": True, "priority": 10, "supported_currencies": ["USD"]})
    return tid


def _pay(admin, tid, amount, ref):
    return admin.post(f"/api/payments?tenant_id={tid}",
                      json={"reference": ref, "amount_minor": amount, "currency": "USD",
                            "provider_key": "mock", "environment": "sandbox",
                            "idempotency_key": f"{ref}-{uuid.uuid4().hex}"})


def test_run_weekly_and_monthly_reports(admin, tenant):
    for rt in ("weekly", "monthly"):
        r = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type={rt}")
        assert r.status_code == 200, r.text
        assert r.json()["report_type"] == rt

    listing = admin.get(f"/api/reports/scheduled?tenant_id={tenant}").json()
    types = {row["report_type"] for row in listing}
    assert {"weekly", "monthly"}.issubset(types)


def test_run_invalid_report_type_rejected(admin, tenant):
    r = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type=hourly")
    assert r.status_code == 400


def test_alert_history_records_fire_and_recover(admin, tenant):
    # Force a low success rate -> alert fires.
    for i in range(6):
        _pay(admin, tenant, 1000 + i * 100 + 13, f"HDECL-{i}")
    admin.post(f"/api/providers/alerts/evaluate?tenant_id={tenant}")

    # Recover with successes.
    for i in range(12):
        _pay(admin, tenant, 6000 + i, f"HOK-{i}")
    admin.post(f"/api/providers/alerts/evaluate?tenant_id={tenant}")

    # Assert on persisted history (robust to the 60s background eval job racing these calls).
    hist = admin.get(f"/api/providers/alerts/history?tenant_id={tenant}&limit=20")
    assert hist.status_code == 200
    events = hist.json()
    transitions = [e["transition"] for e in events]
    assert "alerting" in transitions and "recovered" in transitions
    # Newest first: the recovery happened after the alert.
    assert transitions.index("recovered") < transitions.index("alerting")
    assert all(e["provider_key"] == "mock" for e in events)


def test_alert_history_never_exposes_secrets(admin, tenant):
    r = admin.get(f"/api/providers/alerts/history?tenant_id={tenant}")
    assert r.status_code == 200
    assert "credentials_ref" not in r.text and "ciphertext" not in r.text


# ----------------------------- custom date range -----------------------------
def test_custom_range_report(admin, tenant):
    r = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}"
                   f"&report_type=custom&start_date=2026-01-01&end_date=2026-01-31")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_type"] == "custom"
    assert body["period_date"] == "2026-01-01"


def test_custom_range_requires_dates(admin, tenant):
    assert admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type=custom").status_code == 400


def test_custom_range_end_before_start_rejected(admin, tenant):
    r = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}"
                   f"&report_type=custom&start_date=2026-02-10&end_date=2026-02-01")
    assert r.status_code == 400


# ----------------------------- email settings (future-ready) -----------------------------
def test_email_settings_roundtrip_and_report_gating(admin, tenant):
    got = admin.get(f"/api/reports/scheduled/email-settings?tenant_id={tenant}").json()
    assert "enabled" in got and "frequencies" in got and "attach_csv" in got

    upd = admin.put(f"/api/reports/scheduled/email-settings?tenant_id={tenant}",
                    json={"enabled": True, "recipient_email": "ops@rep.test",
                          "frequencies": ["weekly"], "attach_csv": True}).json()
    assert upd["enabled"] is True and upd["frequencies"] == ["weekly"]
    assert upd["recipient_email"] == "ops@rep.test"

    # weekly is enabled -> adapter attempts send (noop => skipped_no_provider)
    wk = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type=weekly").json()
    assert wk["email_status"] == "skipped_no_provider"
    # daily not in frequencies -> skipped_frequency
    dl = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type=daily").json()
    assert dl["email_status"] == "skipped_frequency"

    # disable entirely -> disabled
    admin.put(f"/api/reports/scheduled/email-settings?tenant_id={tenant}", json={"enabled": False})
    off = admin.post(f"/api/reports/scheduled/run?tenant_id={tenant}&report_type=weekly").json()
    assert off["email_status"] == "disabled"


def test_email_settings_drops_invalid_frequencies(admin, tenant):
    upd = admin.put(f"/api/reports/scheduled/email-settings?tenant_id={tenant}",
                    json={"frequencies": ["weekly", "hourly", "yearly"]}).json()
    assert upd["frequencies"] == ["weekly"]


# ----------------------------- provider stability score -----------------------------
def test_provider_stability_score(admin, tenant):
    # Ensure at least one drop is on record.
    for i in range(6):
        _pay(admin, tenant, 2000 + i * 111 + 7, f"SDECL-{i}")
    admin.post(f"/api/providers/alerts/evaluate?tenant_id={tenant}")

    r = admin.get(f"/api/providers/stability?tenant_id={tenant}&window_days=30")
    assert r.status_code == 200
    rows = r.json()
    assert rows, "expected at least one provider stability entry"
    row = next(x for x in rows if x["provider_key"] == "mock")
    assert row["drops"] >= 1
    assert 0 <= row["score"] <= 100
    assert row["rating"] in ("stable", "moderate", "flaky")

