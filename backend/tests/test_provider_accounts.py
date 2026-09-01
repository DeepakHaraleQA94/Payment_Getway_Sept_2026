"""Provider Account Management + Secret Store + Environment Selection tests.

Covers this SRD phase:
 * persistent per-(tenant, provider, environment) provider accounts with independent enable
   flags and credential references
 * secure secret store (Fernet, encrypted at rest, references only — never raw secrets in API)
 * explicit environment selection at execution time through the generic interface

Run serially: `pytest tests/ -n0`.
"""
import asyncio
import os
import uuid

import httpx
import pytest

from app.core.database import AsyncSessionLocal
from app.models.payment import ProviderSecret
from app.services.secret_store import EncryptedDbSecretStore, get_secret_store

BASE = os.environ.get("TEST_BASE_URL", "http://localhost:8001")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@cloudpay.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")


# ----------------------------- secret store (unit) -----------------------------
def test_secret_store_roundtrip_and_encrypted_at_rest():
    async def run():
        async with AsyncSessionLocal() as db:
            store = get_secret_store()
            tenant_id = await _any_tenant_id(db)
            ref = await store.put(db, tenant_id=tenant_id,
                                  secret={"api_key": "sk_secret_value", "webhook_secret": "whsec_x"})
            await db.commit()
            got = await store.get(db, ref)
            assert got == {"api_key": "sk_secret_value", "webhook_secret": "whsec_x"}
            row = (await db.execute(_sel_secret(ref))).scalar_one()
            # Ciphertext at rest must not contain the plaintext secret.
            assert "sk_secret_value" not in row.ciphertext
            assert isinstance(store, EncryptedDbSecretStore)
            await store.delete(db, ref)
            await db.commit()
            assert await store.get(db, ref) is None
    asyncio.run(run())


def _sel_secret(ref):
    from sqlalchemy import select
    return select(ProviderSecret).where(ProviderSecret.ref == ref)


async def _any_tenant_id(db):
    from sqlalchemy import select
    from app.models.tenant import Tenant
    return (await db.execute(select(Tenant).limit(1))).scalar_one().id


# ----------------------------- HTTP fixtures -----------------------------
def _cookie(resp, name):
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    return None


@pytest.fixture(scope="module")
def admin():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    tok = _cookie(r, "access_token")
    assert tok, "admin login failed"
    c.headers["Authorization"] = f"Bearer {tok}"
    yield c
    c.close()


@pytest.fixture(scope="module")
def acme(admin):
    return next(t["id"] for t in admin.get("/api/tenants").json() if t["slug"] == "acme")


@pytest.fixture(scope="module")
def fresh_tenant(admin):
    slug = f"acct-{uuid.uuid4().hex[:8]}"
    r = admin.post("/api/tenants", json={"name": "Account Test Co", "slug": slug, "default_currency": "USD"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ----------------------------- provider accounts (HTTP) -----------------------------
def test_add_provider_with_credentials_stores_reference_only(admin, fresh_tenant):
    body = {"provider_key": "mock", "display_name": "Mock Sandbox", "mode": "sandbox",
            "enabled": True, "priority": 10, "supported_currencies": ["USD"],
            "credentials": {"api_key": "sk_test_ABC", "webhook_secret": "whsec_zzz"}}
    r = admin.post(f"/api/providers?tenant_id={fresh_tenant}", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    # Response exposes a credential REFERENCE, never the raw secret.
    assert out["credentials_ref"] and out["credentials_ref"].startswith("sec_")
    assert "sk_test_ABC" not in r.text and "whsec_zzz" not in r.text


def test_per_environment_accounts_and_duplicate_guard(admin, fresh_tenant):
    # A second sandbox account for the same provider is a duplicate.
    dup = admin.post(f"/api/providers?tenant_id={fresh_tenant}",
                     json={"provider_key": "mock", "display_name": "Dup", "mode": "sandbox"})
    assert dup.status_code == 400 and "already configured" in dup.text.lower()
    # Live is capability-gated (mock is sandbox-only) — not a duplicate, an unsupported env.
    live = admin.post(f"/api/providers?tenant_id={fresh_tenant}",
                      json={"provider_key": "mock", "display_name": "Live", "mode": "live"})
    assert live.status_code == 400 and "environment" in live.text.lower()


def test_enable_disable_and_rotate_credentials(admin, fresh_tenant):
    accounts = admin.get(f"/api/providers?tenant_id={fresh_tenant}").json()
    pid = accounts[0]["id"]
    # Disable then re-enable.
    d = admin.patch(f"/api/providers/{pid}", json={"enabled": False})
    assert d.status_code == 200 and d.json()["enabled"] is False
    e = admin.patch(f"/api/providers/{pid}", json={"enabled": True})
    assert e.status_code == 200 and e.json()["enabled"] is True
    # Rotate credentials — reference stays stable, raw secret never returned.
    old_ref = accounts[0]["credentials_ref"]
    rot = admin.put(f"/api/providers/{pid}/credentials",
                    json={"credentials": {"api_key": "sk_test_ROTATED"}})
    assert rot.status_code == 200
    assert rot.json()["credentials_ref"] == old_ref
    assert "sk_test_ROTATED" not in rot.text


def test_disabled_account_blocks_payment(admin, fresh_tenant):
    accounts = admin.get(f"/api/providers?tenant_id={fresh_tenant}").json()
    pid = accounts[0]["id"]
    admin.patch(f"/api/providers/{pid}", json={"enabled": False})
    r = admin.post(f"/api/payments?tenant_id={fresh_tenant}",
                   json={"reference": "DIS-1", "amount_minor": 1000, "currency": "USD",
                         "provider_key": "mock", "environment": "sandbox",
                         "idempotency_key": f"dis-{uuid.uuid4().hex}"})
    assert r.status_code == 400 and "disabled" in r.text.lower()
    admin.patch(f"/api/providers/{pid}", json={"enabled": True})


def test_delete_provider_account(admin, fresh_tenant):
    accounts = admin.get(f"/api/providers?tenant_id={fresh_tenant}").json()
    pid = accounts[0]["id"]
    r = admin.delete(f"/api/providers/{pid}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert admin.get(f"/api/providers?tenant_id={fresh_tenant}").json() == []


# ----------------------------- environment selection (HTTP) -----------------------------
def test_payment_environment_selection(admin, acme):
    ok = admin.post(f"/api/payments?tenant_id={acme}",
                    json={"reference": "ENV-OK", "amount_minor": 4000, "currency": "USD",
                          "provider_key": "mock", "environment": "sandbox",
                          "idempotency_key": f"env-{uuid.uuid4().hex}"})
    assert ok.status_code == 200 and ok.json()["environment"] == "sandbox"
    assert ok.json()["status"] == "succeeded"
    # Live selection is safely rejected (mock does not support live; no real money path).
    live = admin.post(f"/api/payments?tenant_id={acme}",
                      json={"reference": "ENV-LIVE", "amount_minor": 4000, "currency": "USD",
                            "provider_key": "mock", "environment": "live",
                            "idempotency_key": f"env-live-{uuid.uuid4().hex}"})
    assert live.status_code == 400 and "live" in live.text.lower()


def test_payment_defaults_to_sandbox(admin, acme):
    r = admin.post(f"/api/payments?tenant_id={acme}",
                   json={"reference": "ENV-DEF", "amount_minor": 4000, "currency": "USD",
                         "provider_key": "mock", "idempotency_key": f"env-def-{uuid.uuid4().hex}"})
    assert r.status_code == 200 and r.json()["environment"] == "sandbox"
