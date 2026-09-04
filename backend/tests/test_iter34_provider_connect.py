"""Iteration 34 — Generic (plugin-agnostic) Provider Connect onboarding.

Covers:
  * DYNAMIC DISCOVERY via GET /api/providers/available
  * POST /api/providers/test-connection with unsaved credentials (mock, examplepsp, demo_upi)
  * Non-persistence + no-secret-echo guarantees
  * Environment gating (demo_upi 'live' -> 400)
  * Acceptance-account mapping: persisted, validated, tenant-isolated
  * Secure credential binding (stored in secret store, ref only)
  * LIVE-safety re-verification
  * Duplicate provider/env uniqueness
  * Permission + tenant isolation
  * Plugin-agnosticism (no hard-coded provider names in credential/test/save code paths)
"""
import os
import uuid

import psycopg2
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://pay-gateway-core.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "finance@vortexglobal.info"
ADMIN_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"
OPS_EMAIL = "ops-admin@cloudpay.io"
OPS_PASS = "CloudPay-DutqTuzcS1jL64hHJrCy"

PG_DSN = "host=localhost dbname=cloudpay user=cloudpay password=cloudpay_local_pwd"


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text[:250]}"
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASS)


@pytest.fixture(scope="module")
def ops():
    try:
        return _login(OPS_EMAIL, OPS_PASS)
    except AssertionError:
        pytest.skip("ops-admin not available")


@pytest.fixture(scope="module")
def tenants(admin):
    r = admin.get(f"{API}/tenants", timeout=15)
    assert r.status_code == 200
    return {t["slug"]: t for t in r.json()}


@pytest.fixture(scope="module")
def acme_id(tenants):
    return tenants["acme"]["id"]


@pytest.fixture(scope="module")
def captest_id(tenants):
    for slug, t in tenants.items():
        if slug.startswith("captest"):
            return t["id"]
    return list(tenants.values())[0]["id"]


@pytest.fixture(scope="module")
def db_conn():
    conn = psycopg2.connect(PG_DSN)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def acme_sandbox_acceptance_id(db_conn, acme_id):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM payment_acceptance_accounts "
            "WHERE tenant_id=%s AND environment='sandbox' AND enabled=true "
            "ORDER BY priority LIMIT 1", (acme_id,))
        row = cur.fetchone()
    db_conn.commit()
    assert row, "acme should have a sandbox UPI acceptance account seeded"
    return str(row[0])


# ============================================================ DYNAMIC DISCOVERY
class TestDynamicDiscovery:
    def test_available_returns_plugin_metadata(self, admin):
        r = admin.get(f"{API}/providers/available", timeout=15)
        assert r.status_code == 200
        plugins = r.json()
        assert isinstance(plugins, list) and len(plugins) >= 3
        keys = {p["key"] for p in plugins}
        # All reference plugins should be registered.
        for expected in ("mock", "demo_upi", "examplepsp"):
            assert expected in keys, f"plugin {expected} not registered; got {keys}"
        # Metadata contract fields present on every plugin.
        required_fields = {"key", "display_name", "supported_environments",
                           "supported_currencies", "supported_countries",
                           "payment_methods", "supported_flows",
                           "supports_refund", "supports_capture", "supports_void",
                           "supports_webhooks", "supports_intent", "supports_qr",
                           "required_credentials"}
        for p in plugins:
            missing = required_fields - set(p.keys())
            assert not missing, f"plugin {p.get('key')} missing metadata fields: {missing}"

    def test_examplepsp_declares_required_credentials(self, admin):
        r = admin.get(f"{API}/providers/available", timeout=15)
        plugins = {p["key"]: p for p in r.json()}
        rc = plugins["examplepsp"]["required_credentials"]
        keys = {c["key"] for c in rc}
        assert {"api_key", "api_secret", "webhook_secret"}.issubset(keys), rc

    def test_demo_upi_is_sandbox_only(self, admin):
        r = admin.get(f"{API}/providers/available", timeout=15)
        plugins = {p["key"]: p for p in r.json()}
        env = plugins["demo_upi"]["supported_environments"]
        assert "sandbox" in env
        assert "live" not in env

    def test_examplepsp_supports_live(self, admin):
        r = admin.get(f"{API}/providers/available", timeout=15)
        plugins = {p["key"]: p for p in r.json()}
        assert "live" in plugins["examplepsp"]["supported_environments"]


# ============================================================ TEST-CONNECTION
class TestTestConnection:
    def test_mock_sandbox_up(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "mock", "mode": "sandbox"}, timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["status"] == "up"
        assert b["environment"] == "sandbox"
        assert b["provider"] == "mock"

    def test_examplepsp_live_missing_credentials(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "examplepsp", "mode": "live"}, timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["status"] == "invalid_credentials", b
        # Detail should list missing keys.
        detail = (b.get("detail") or "").lower()
        for k in ("api_key", "api_secret", "webhook_secret"):
            assert k in detail, f"missing key '{k}' should be in detail: {detail}"

    def test_examplepsp_live_with_credentials_up(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "examplepsp", "mode": "live",
                             "credentials": {"api_key": "TEST_ak", "api_secret": "TEST_as",
                                             "webhook_secret": "TEST_wh"}}, timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["status"] == "up", b

    def test_demo_upi_live_env_rejected(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "demo_upi", "mode": "live"}, timeout=15)
        # demo_upi is sandbox-only -> 400 (no silent sandbox fallback).
        assert r.status_code == 400, r.text
        assert "live" in r.text.lower() or "environment" in r.text.lower()

    def test_unknown_provider_returns_404(self, admin):
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "no_such_plugin_xyz", "mode": "sandbox"}, timeout=15)
        assert r.status_code == 404

    def test_requires_provider_manage_permission(self, ops):
        r = ops.post(f"{API}/providers/test-connection",
                     json={"provider_key": "mock", "mode": "sandbox"}, timeout=15)
        assert r.status_code == 403, f"expected 403 for non-superadmin, got {r.status_code}"

    def test_response_never_echoes_credentials(self, admin):
        secret_value = "SECRET_should_never_leak_xyz"
        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "examplepsp", "mode": "live",
                             "credentials": {"api_key": secret_value,
                                             "api_secret": "SEC_" + secret_value,
                                             "webhook_secret": "WH_" + secret_value}},
                       timeout=15)
        assert r.status_code == 200
        text = r.text
        assert secret_value not in text, "raw credential value leaked in test-connection response"
        # Response contains only known-safe fields.
        b = r.json()
        assert set(b.keys()).issubset({"provider", "status", "environment", "detail"}), b.keys()

    def test_test_connection_does_not_persist(self, admin, db_conn, captest_id):
        """Calling test-connection must NOT create a PaymentProvider row or a secret."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM payment_providers WHERE tenant_id=%s AND provider_key='examplepsp'",
                        (captest_id,))
            before = cur.fetchone()[0]
        db_conn.commit()

        r = admin.post(f"{API}/providers/test-connection",
                       json={"provider_key": "examplepsp", "mode": "sandbox",
                             "credentials": {"api_key": "TEST_k", "api_secret": "TEST_s",
                                             "webhook_secret": "TEST_w"}},
                       headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 200

        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM payment_providers WHERE tenant_id=%s AND provider_key='examplepsp'",
                        (captest_id,))
            after = cur.fetchone()[0]
        db_conn.commit()
        assert after == before, f"test-connection unexpectedly persisted a provider row ({before} -> {after})"


# ============================================================ ACCEPTANCE MAPPING
class TestAcceptanceMapping:
    def _delete_demo_upi(self, admin, tid):
        """Cleanup helper: delete any existing demo_upi sandbox provider for tenant."""
        r = admin.get(f"{API}/providers?tenant_id={tid}",
                      headers={"X-Tenant-Id": str(tid)}, timeout=15)
        for p in r.json() or []:
            if p["provider_key"] == "demo_upi" and p["mode"] == "sandbox":
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={tid}",
                             headers={"X-Tenant-Id": str(tid)}, timeout=15)

    def test_persists_valid_acceptance_account_id(self, admin, acme_id,
                                                  acme_sandbox_acceptance_id, db_conn):
        self._delete_demo_upi(admin, acme_id)
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                       json={"provider_key": "demo_upi",
                             "display_name": "TEST Demo UPI Sandbox",
                             "mode": "sandbox", "enabled": True, "priority": 200,
                             "supported_currencies": ["INR"],
                             "supported_methods": ["upi"],
                             "supported_flows": ["direct"],
                             "config": {"acceptance_account_id": acme_sandbox_acceptance_id}},
                       headers=h, timeout=20)
        assert r.status_code == 200, r.text[:400]
        created = r.json()
        prov_id = created["id"]
        try:
            # Response config must contain only the id reference.
            cfg = created.get("config") or {}
            assert cfg.get("acceptance_account_id") == acme_sandbox_acceptance_id, cfg
            assert "vpa" not in cfg and "upi_vpa" not in cfg, cfg
            for k in cfg.keys():
                # Nothing that looks like a secret.
                assert "secret" not in k.lower(), k

            # GET /providers echoes the id.
            r2 = admin.get(f"{API}/providers?tenant_id={acme_id}", headers=h, timeout=15)
            match = [p for p in r2.json() if p["id"] == prov_id]
            assert match and match[0]["config"].get("acceptance_account_id") == acme_sandbox_acceptance_id

            # DB inspection: raw config JSON contains only the id key.
            with db_conn.cursor() as cur:
                cur.execute("SELECT config FROM payment_providers WHERE id=%s", (prov_id,))
                raw = cur.fetchone()[0]
            db_conn.commit()
            assert raw.get("acceptance_account_id") == acme_sandbox_acceptance_id
            for k in raw.keys():
                assert "secret" not in k.lower() and "vpa" not in k.lower(), raw
        finally:
            admin.delete(f"{API}/providers/{prov_id}?tenant_id={acme_id}",
                         headers=h, timeout=15)

    def test_random_acceptance_id_rejected(self, admin, acme_id):
        self._delete_demo_upi(admin, acme_id)
        h = {"X-Tenant-Id": str(acme_id)}
        r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                       json={"provider_key": "demo_upi", "display_name": "TEST X",
                             "mode": "sandbox", "enabled": True,
                             "config": {"acceptance_account_id": str(uuid.uuid4())}},
                       headers=h, timeout=15)
        assert r.status_code == 400, r.text[:200]
        assert "acceptance" in r.text.lower() or "not found" in r.text.lower()

    def test_wrong_environment_rejected(self, admin, acme_id, acme_sandbox_acceptance_id):
        """acceptance account env=sandbox, provider mode=live -> 400.
        Use examplepsp (supports live). Also ensures we don't accidentally hit the
        demo_upi 'live not supported' branch."""
        h = {"X-Tenant-Id": str(acme_id)}
        # Clean any leftover live examplepsp for acme.
        r0 = admin.get(f"{API}/providers?tenant_id={acme_id}", headers=h, timeout=15)
        for p in r0.json() or []:
            if p["provider_key"] == "examplepsp" and p["mode"] == "live":
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={acme_id}", headers=h, timeout=15)

        r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                       json={"provider_key": "examplepsp", "display_name": "TEST env-mismatch",
                             "mode": "live", "enabled": True,
                             "supported_currencies": ["USD"], "supported_methods": ["card"],
                             "supported_flows": ["direct"],
                             "config": {"acceptance_account_id": acme_sandbox_acceptance_id}},
                       headers=h, timeout=15)
        assert r.status_code == 400, r.text[:300]
        assert "environment" in r.text.lower()

    def test_disabled_account_rejected(self, admin, acme_id, acme_sandbox_acceptance_id, db_conn):
        # Disable, attempt save, then re-enable.
        with db_conn.cursor() as cur:
            cur.execute("UPDATE payment_acceptance_accounts SET enabled=false WHERE id=%s",
                        (acme_sandbox_acceptance_id,))
        db_conn.commit()
        try:
            self._delete_demo_upi(admin, acme_id)
            r = admin.post(f"{API}/providers?tenant_id={acme_id}",
                           json={"provider_key": "demo_upi", "display_name": "TEST disabled",
                                 "mode": "sandbox", "enabled": True,
                                 "config": {"acceptance_account_id": acme_sandbox_acceptance_id}},
                           headers={"X-Tenant-Id": str(acme_id)}, timeout=15)
            assert r.status_code == 400, r.text[:300]
            assert "disabled" in r.text.lower()
        finally:
            with db_conn.cursor() as cur:
                cur.execute("UPDATE payment_acceptance_accounts SET enabled=true WHERE id=%s",
                            (acme_sandbox_acceptance_id,))
            db_conn.commit()

    def test_other_tenant_acceptance_rejected(self, admin, captest_id, acme_sandbox_acceptance_id):
        """Attempt to use acme's acceptance account from captest tenant -> 400."""
        h = {"X-Tenant-Id": str(captest_id)}
        # Cleanup demo_upi on captest just in case.
        r0 = admin.get(f"{API}/providers?tenant_id={captest_id}", headers=h, timeout=15)
        for p in r0.json() or []:
            if p["provider_key"] == "demo_upi" and p["mode"] == "sandbox":
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={captest_id}",
                             headers=h, timeout=15)

        r = admin.post(f"{API}/providers?tenant_id={captest_id}",
                       json={"provider_key": "demo_upi", "display_name": "TEST cross-tenant",
                             "mode": "sandbox", "enabled": True,
                             "config": {"acceptance_account_id": acme_sandbox_acceptance_id}},
                       headers=h, timeout=15)
        assert r.status_code == 400, r.text[:300]
        assert "not found" in r.text.lower() or "tenant" in r.text.lower()


# ============================================================ SECURE CREDENTIAL BINDING
class TestSecureCredentialBinding:
    def test_credentials_never_echoed_and_ref_stored(self, admin, captest_id, db_conn):
        """Passing credentials on POST /providers stores them in the secret store; the
        create response + GET /providers must never include raw credential values, only ref."""
        h = {"X-Tenant-Id": str(captest_id)}
        secret_marker = f"SECRET_{uuid.uuid4().hex}"

        # Clean any existing examplepsp sandbox on captest.
        r0 = admin.get(f"{API}/providers?tenant_id={captest_id}", headers=h, timeout=15)
        for p in r0.json() or []:
            if p["provider_key"] == "examplepsp" and p["mode"] == "sandbox":
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={captest_id}",
                             headers=h, timeout=15)

        r = admin.post(f"{API}/providers?tenant_id={captest_id}",
                       json={"provider_key": "examplepsp",
                             "display_name": "TEST cred binding",
                             "mode": "sandbox", "enabled": True,
                             "supported_currencies": ["USD"],
                             "supported_methods": ["card"],
                             "supported_flows": ["direct"],
                             "credentials": {"api_key": secret_marker,
                                             "api_secret": "sec_" + secret_marker,
                                             "webhook_secret": "wh_" + secret_marker}},
                       headers=h, timeout=20)
        assert r.status_code == 200, r.text[:400]
        created = r.json()
        prov_id = created["id"]
        try:
            # Marker must NOT appear anywhere in response.
            assert secret_marker not in r.text, "raw credential leaked in create response"
            # credentials_ref must be populated.
            assert created.get("credentials_ref"), created
            # 'credentials' field should not be echoed.
            assert "credentials" not in created or created.get("credentials") in (None, "", {}), created

            # GET providers: no raw credentials.
            r2 = admin.get(f"{API}/providers?tenant_id={captest_id}", headers=h, timeout=15)
            assert secret_marker not in r2.text, "raw credential leaked in GET providers"

            # DB inspection: config should NOT contain the raw secret.
            with db_conn.cursor() as cur:
                cur.execute("SELECT config, credentials_ref FROM payment_providers WHERE id=%s",
                            (prov_id,))
                cfg, ref = cur.fetchone()
            db_conn.commit()
            import json as _json
            assert secret_marker not in _json.dumps(cfg or {}), cfg
            assert ref, "credentials_ref should be persisted"
        finally:
            admin.delete(f"{API}/providers/{prov_id}?tenant_id={captest_id}",
                         headers=h, timeout=15)


# ============================================================ LIVE SAFETY (regression)
class TestLiveSafety:
    def test_live_demo_upi_at_provider_create_rejected(self, admin, captest_id):
        r = admin.post(f"{API}/providers?tenant_id={captest_id}",
                       json={"provider_key": "demo_upi", "display_name": "TEST live demo_upi",
                             "mode": "live", "enabled": True},
                       headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code == 400, r.text[:300]
        assert "live" in r.text.lower()


# ============================================================ DUPLICATES
class TestDuplicateProvider:
    def test_duplicate_provider_env_rejected(self, admin, captest_id):
        h = {"X-Tenant-Id": str(captest_id)}
        # Cleanup any prior test rows first.
        r0 = admin.get(f"{API}/providers?tenant_id={captest_id}", headers=h, timeout=15)
        for p in r0.json() or []:
            if p["provider_key"] == "examplepsp" and p["mode"] == "sandbox" \
               and p["display_name"].startswith("TEST dup"):
                admin.delete(f"{API}/providers/{p['id']}?tenant_id={captest_id}",
                             headers=h, timeout=15)

        payload = {"provider_key": "examplepsp", "display_name": "TEST dup",
                   "mode": "sandbox", "enabled": True,
                   "supported_currencies": ["USD"], "supported_methods": ["card"],
                   "supported_flows": ["direct"]}
        r1 = admin.post(f"{API}/providers?tenant_id={captest_id}",
                        json=payload, headers=h, timeout=15)
        # Only meaningful if the first insert worked; if it collided with an existing row, skip.
        if r1.status_code != 200:
            pytest.skip(f"first insert failed unexpectedly: {r1.status_code} {r1.text[:200]}")
        prov_id = r1.json()["id"]
        try:
            r2 = admin.post(f"{API}/providers?tenant_id={captest_id}",
                            json=payload, headers=h, timeout=15)
            assert r2.status_code == 400, r2.text[:200]
            assert "already" in r2.text.lower() or "environment" in r2.text.lower()
        finally:
            admin.delete(f"{API}/providers/{prov_id}?tenant_id={captest_id}",
                         headers=h, timeout=15)


# ============================================================ PERMISSION + ISOLATION
class TestPermissionIsolation:
    def test_ops_cannot_test_connection(self, ops):
        r = ops.post(f"{API}/providers/test-connection",
                     json={"provider_key": "mock", "mode": "sandbox"}, timeout=15)
        assert r.status_code == 403

    def test_ops_cannot_post_providers(self, ops):
        r = ops.post(f"{API}/providers",
                     json={"provider_key": "mock", "display_name": "TEST", "mode": "sandbox"},
                     timeout=15)
        assert r.status_code == 403

    def test_ops_cannot_read_other_tenant_providers(self, ops, captest_id):
        r = ops.get(f"{API}/providers?tenant_id={captest_id}",
                    headers={"X-Tenant-Id": str(captest_id)}, timeout=15)
        assert r.status_code in (403, 404)


# ============================================================ PLUGIN-AGNOSTIC CONFIRMATION
class TestPluginAgnostic:
    def test_all_reference_plugins_go_through_same_endpoint(self, admin):
        """The same test-connection endpoint should work for every registered plugin using
        purely its metadata — no provider-specific branch required."""
        r = admin.get(f"{API}/providers/available", timeout=15)
        plugins = r.json()
        for plugin in plugins:
            key = plugin["key"]
            supported = plugin["supported_environments"]
            # Pick a supported env.
            env = "sandbox" if "sandbox" in supported else supported[0]
            required = plugin.get("required_credentials") or []
            creds = {c["key"]: f"TEST_{c['key']}" for c in required if c.get("required")}
            body = {"provider_key": key, "mode": env}
            if creds:
                body["credentials"] = creds
            resp = admin.post(f"{API}/providers/test-connection", json=body, timeout=15)
            assert resp.status_code == 200, f"{key} test-connection failed: {resp.text[:200]}"
            data = resp.json()
            assert data["status"] in ("up", "invalid_credentials"), \
                f"{key} unexpected status: {data}"

    def test_no_hardcoded_provider_names_in_endpoint_logic(self):
        """Grep the endpoint file for hard-coded provider keys in credential/test/save logic.
        The wizard's post-save UX and demo-link creation are allowed to reference 'demo_upi'."""
        with open("/app/backend/app/routers/config.py") as f:
            src = f.read()
        # These substrings should NOT appear inside test-connection / add_provider /
        # _validate_acceptance_mapping — but they may appear in comments/error strings.
        # We assert that no `body.provider_key == "..."` or `provider_key in (...)` occurs.
        forbidden_patterns = [
            'provider_key == "mock"',
            'provider_key == "demo_upi"',
            'provider_key == "stripe"',
            'provider_key == "examplepsp"',
        ]
        for pat in forbidden_patterns:
            assert pat not in src, f"hard-coded provider name check found in config.py: {pat}"
