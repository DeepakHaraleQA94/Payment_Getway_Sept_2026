"""Integration tests for Stripe adapter discovery, capabilities, health, and regression on existing providers.

Verifies:
 - Stripe registered with sandbox-only, live_supported False
 - Mock/example providers still discoverable
 - Payment on 'acme' tenant with provider_key 'mock' still succeeds
 - No secret leakage in provider APIs
 - Auth login (valid + invalid) still works
 - Tenant-scoped endpoint enforces auth (401/403)
"""
import json
import os
import re
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback: read frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

ADMIN_EMAIL = "admin@cloudpay.io"
ADMIN_PASSWORD = "CloudPay-DutqTuzcS1jL64hHJrCy"

SECRET_PATTERNS = [
    re.compile(r"sk_test_[A-Za-z0-9]+"),
    re.compile(r"sk_live_[A-Za-z0-9]+"),
    re.compile(r"whsec_[A-Za-z0-9]+"),
]


def _assert_no_secret_leak(text: str):
    for p in SECRET_PATTERNS:
        m = p.search(text)
        assert not m, f"Secret-like value leaked: {m.group(0)[:8]}..."
    # api_key must not appear with a non-empty string value
    # (fields listing api_key as a credential name label is fine)
    try:
        data = json.loads(text)
    except Exception:
        return
    def scan(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in ("api_key", "secret_key", "webhook_secret"):
                    # allow None/empty/placeholder metadata; disallow a real value string
                    assert v in (None, "", False) or (isinstance(v, str) and not v.startswith(("sk_", "whsec_"))), (
                        f"Leaked credential under key '{k}'"
                    )
                scan(v)
        elif isinstance(obj, list):
            for x in obj:
                scan(x)
    scan(data)


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    tok = body.get("access_token") or body.get("token")
    # Also try cookies (httpOnly access_token cookie)
    if not tok:
        for c in r.cookies:
            if c.name == "access_token":
                tok = c.value
                break
    assert tok, f"no token in login response body/cookies: body={body} cookies={list(r.cookies.keys())}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- Auth regression ----------
class TestAuthRegression:
    def test_valid_login(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        assert r.status_code == 200
        # token may be delivered via httpOnly cookie or in body
        has_body_tok = bool(r.json().get("access_token") or r.json().get("token"))
        has_cookie_tok = any(c.name in ("access_token", "refresh_token") for c in r.cookies)
        assert has_body_tok or has_cookie_tok, f"no auth token in body or cookies: {dict(r.cookies)} body={r.json()}"

    def test_invalid_password_rejected(self):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "definitely-wrong-pw"}, timeout=15)
        assert r.status_code in (400, 401, 403), f"expected reject, got {r.status_code}: {r.text[:200]}"


# ---------- Stripe adapter discovery ----------
class TestStripeDiscovery:
    def test_providers_available_lists_stripe(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/available", headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        _assert_no_secret_leak(r.text)
        providers = r.json()
        # payload could be list or {providers:[...]}
        items = providers if isinstance(providers, list) else providers.get("providers") or providers.get("items") or []
        keys = [p.get("key") for p in items]
        assert "stripe" in keys, f"stripe missing from available providers: {keys}"
        stripe_entry = next(p for p in items if p.get("key") == "stripe")
        # live_supported should be false (either explicit field or via supported_environments)
        live = stripe_entry.get("live_supported")
        envs = stripe_entry.get("supported_environments") or []
        assert live is False or (live is None and "live" not in envs), (
            f"stripe live must be disabled, got live_supported={live} envs={envs}"
        )

    def test_stripe_capabilities(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/stripe/capabilities", headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        _assert_no_secret_leak(r.text)
        cap = r.json()
        assert cap.get("supported_environments") == ["sandbox"], cap.get("supported_environments")
        assert cap.get("live_supported") is False
        assert "card" in (cap.get("payment_methods") or [])
        countries = cap.get("supported_countries") or []
        for c in ("US", "GB", "IN"):
            assert c in countries, f"missing country {c}: {countries}"

    def test_stripe_health(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/stripe/health", headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        _assert_no_secret_leak(r.text)
        j = r.json()
        status = str(j.get("status") or j.get("state") or "").lower()
        # "up" is the target; "unconfigured" indicates STRIPE_API_KEY not set in backend process env.
        # Both are non-error states from the adapter; anything else (down/error) would be a bug.
        assert status in ("up", "healthy", "ok", "operational", "unconfigured"), f"unexpected health: {j}"
        if status == "unconfigured":
            pytest.skip("STRIPE_API_KEY not present in backend process env — flagged in test report as config gap.")

    def test_unknown_provider_returns_404(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/razorpay/capabilities", headers=auth_headers, timeout=15)
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text[:200]}"


# ---------- Existing providers regression ----------
class TestExistingProviders:
    def test_mock_capabilities(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/mock/capabilities", headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]

    def test_example_capabilities(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/providers/examplepsp/capabilities", headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]


# ---------- Payment regression on acme ----------
class TestPaymentRegression:
    def test_create_mock_payment_acme(self, auth_headers):
        ref = f"TEST-STRIPE-REG-{int(time.time())}"
        payload = {
            "amount_minor": 1500,
            "currency": "USD",
            "provider_key": "mock",
            "reference": ref,
            "description": "regression after stripe adapter add",
        }
        # tenant selection via header (project convention)
        headers = {**auth_headers, "X-Tenant-Slug": "acme", "X-Tenant": "acme"}
        r = requests.post(f"{BASE_URL}/api/payments", json=payload, headers=headers, timeout=20)
        assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text[:300]}"
        body = r.json()
        pid = body.get("id") or body.get("payment_id") or body.get("payment", {}).get("id")
        assert pid, f"no id in response: {body}"

        # list payments and confirm presence
        lr = requests.get(f"{BASE_URL}/api/payments", headers=headers, timeout=15)
        assert lr.status_code == 200
        items = lr.json()
        items = items if isinstance(items, list) else items.get("items") or items.get("payments") or []
        refs = [it.get("reference") for it in items]
        assert ref in refs, f"created ref not found in list (top refs: {refs[:5]})"


# ---------- Tenant isolation / RBAC spot check ----------
class TestAuthzSpotCheck:
    def test_unauthed_payments_rejected(self):
        r = requests.get(f"{BASE_URL}/api/payments", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_unauthed_providers_available_rejected(self):
        r = requests.get(f"{BASE_URL}/api/providers/available", timeout=15)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
