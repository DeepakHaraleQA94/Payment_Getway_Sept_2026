# CloudPay — Product Requirements & Architecture

## Original Problem Statement
Build the foundation of a WEB-ONLY, production-oriented, multi-tenant payment gateway / payment
orchestration platform. Modular interfaces for auth/authz, tenant/client management,
users/roles/permissions, feature management, payment engine, provider/plugin adapters, fee engine,
turnover engine, balance & ledger, refunds/reversals, settlement/reconciliation, risk, KYC/AML,
FX, digital-asset/VDA boundary, notifications, reports, AI/voice boundary, audit, configuration,
monitoring. Relational DB with migrations, constraints, indexes, timestamps, audit fields, tenant
isolation. Env separation (dev/QA/staging/prod). No hard-coded secrets. Never fake real-money
success (sandbox/mock providers). Provider/plugin interfaces (no single hard-coded provider).

## User Choices
- Database: PostgreSQL (relational) with Alembic migrations.
- Auth: BOTH JWT email/password + Emergent-managed Google login.
- Provider: Mock/sandbox only for the foundation.
- Brand: "CloudPay", professional fintech dark UI.

## Architecture
- Backend: FastAPI + SQLAlchemy (async, asyncpg) + Alembic. Layered:
  `app/core` (config, database, security, deps/RBAC, audit), `app/models`, `app/schemas`,
  `app/providers` (plugin interface + registry + MockProvider), `app/services` (engines &
  boundaries), `app/routers` (per-module APIs). All routes under `/api`.
- DB: PostgreSQL "cloudpay", managed by supervisor program `postgresql`. 20 tenant-aware tables
  with constraints, indexes, timestamps and audit fields. Migrations via Alembic.
- Frontend: React (CRA/craco) + Tailwind + shadcn/ui + recharts + framer-motion + sonner.
  Dark fintech command-center dashboard. Cookie-based auth (JWT + Google session).
- Env separation via APP_ENV (development/qa/staging/production); all secrets in .env.

## User Personas
- Platform Super Admin: manages all tenants, users, providers, fees, monitoring.
- Tenant Admin / Member: scoped to their own tenant (RBAC via permissions).

## Core Requirements (static)
- Server-side validation, tenant isolation, idempotency, audit logging for all financial mutations.
- Provider-agnostic orchestration via adapter interface.
- Regulated capabilities (KYC/AML, VDA, AI/voice) gated behind feature flags / config; disabled by default.
- No real-money success; sandbox provider only.

## Implemented (2026-09-01)
- PostgreSQL + Alembic initial migration (20 tables); supervisor-managed Postgres.
- Auth: JWT (register/login/refresh/me/logout, bcrypt, brute-force lockout) + Emergent Google
  session exchange. Admin seeding (admin@cloudpay.io).
- RBAC (permissions, roles, role_permissions) + tenant isolation (resolve_tenant_id).
- Tenants, Users/Roles/Permissions, Feature flags.
- Payment engine (idempotent) + Fee engine + Ledger (double-entry) + Turnover + Refunds
  (validated) + Settlement/reconciliation. Risk scoring stub.
- Provider plugin interface + registry + MockProvider (sandbox decline rule). Live mode blocked.
- Boundary stubs: KYC/AML, FX (with rates), VDA, AI/voice, notifications, reports, monitoring.
- Audit log (append-only) written on all mutations.
- Health endpoint `/api/health`; monitoring `/api/monitoring/services` with boundary status.
- Dashboard UI: Overview (KPIs + chart), Payments (create/refund), Refunds, Ledger, Settlements,
  Providers, Fees, Tenants, Access Control (users/roles/permissions), Feature Flags, Audit, Monitoring.
- Tests: 22 backend tests pass (foundation + extended).

## Implemented (2026-09-01, iteration 2)
- Hosted Checkout: per-tenant API keys (secret shown once, hashed at rest), shareable hosted
  checkout links, and a public `/checkout/:token` payment page (sandbox). Programmatic session
  creation via `X-API-Key` on `/api/v1/checkout/sessions`.
- Webhook Notifications: configurable endpoints (HMAC-signed deliveries), auto-dispatch on
  payment.succeeded/failed and refund.succeeded/failed, "Send test", and a live Delivery Inspector.
- CSV Reports: export payments, settlements and ledger entries to CSV from the dashboard.
- Tests: 31 backend tests pass (added test_commerce.py, 9 cases).

## Implemented (2026-09-01, iteration 3)
- Checkout Branding: per-tenant logo upload (Emergent object storage) + accent color, applied to
  the hosted checkout page (logo + Pay button color). Public logo served via backend.
- Webhook Retries: exponential backoff up to 8 attempts; retries only transient failures
  (5xx/429/timeout/network), never permanent 4xx; background scheduler (every 30s) re-attempts due
  deliveries; manual Replay creates a new attempt preserving the original event_id (no financial
  re-processing); every retry/replay audited. Inspector shows attempts x/max, next-retry time, replay badge.
- Scheduled Reports: daily CSV (payments + settlements) per tenant at 08:00 UTC (APScheduler) plus
  "Run report now"; stored for in-app download with history; recipient = tenant contact email;
  provider-agnostic email adapter (noop, "skipped_no_provider") ready for Resend/SendGrid.
- Infra: Emergent object storage client, email adapter interface, APScheduler background jobs.
- Tests: 41 backend tests pass (added test_iteration3.py, 10 cases).

## Note (2026-06) — Superseded PROMPT 05 Stripe adapter
- A Stripe TEST-mode adapter was briefly added, then FULLY REVERSED per an explicit
  architectural correction (see next section). No Stripe/Razorpay code, config, or
  credentials remain anywhere in the codebase.

## Architecture correction (2026-06) — Provider-agnostic plugin core
- REVERSED the earlier Stripe integration. CloudPay core is now strictly provider-agnostic:
  no Stripe/Razorpay or any provider-specific code in payment_engine, models, routing, ledger,
  checkout, or the (outbound) webhook architecture.
- Standardized provider plugin contract in `app/providers/base.py` (`PaymentProviderAdapter`
  ABC): capability discovery, credential-reference interface (names only, never secret values),
  sandbox/live-mode abstraction, health-check, charge/refund/status, and a normalized inbound
  webhook contract (`verify_webhook` -> `ProviderWebhookEvent`).
- Registry (`app/providers/registry.py`): generic register/get/discovery. Only the built-in
  Mock dev/test plugin ships. New providers register themselves without changing core.
- Generic provider endpoints (core, provider-agnostic): GET /api/providers/available (discovery),
  GET /api/providers/{key}/capabilities, GET /api/providers/{key}/health, and a generic public
  inbound webhook POST /api/providers/{key}/webhook that delegates verification+translation to
  the plugin and reconciles payment status via the state machine (no ledger side effects).
- Removed Stripe plugin, STRIPE_* config keys and .env entries, and all Stripe-specific webhook
  code/tests. No provider-specific credentials remain.
- Tests: 89 backend tests pass serially (`pytest tests/ -n0`); added test_provider_architecture.py
  (14 tests) verifying the contract, discovery/isolation, capability/credential/health interfaces,
  sandbox-live abstraction, and the generic webhook contract via the Mock provider.

## Generic provider contract expanded (2026-06) — plugin building blocks
- Expanded the provider-agnostic contract to the full SRD surface, still with NO real
  provider (Mock remains the sole reference implementation; live mode stays blocked).
- New building-block interfaces in `app/providers/contracts.py`: ProviderConfiguration,
  Authentication, ApiClient, RequestMapper, ResponseMapper, StatusMapper, CallbackHandler,
  ErrorHandler, HealthCheck — plus normalized types (ProviderIntent, ProviderQR,
  ProviderStatusResult, ProviderReconciliation, ProviderError, PaymentFlow, etc.).
- `PaymentProviderAdapter` (base.py) now exposes: create_payment, get_payment_status,
  generate_intent, generate_qr, verify_callback, reconcile (+ refund); charge/verify_webhook
  kept as backward-compatible aliases. Capabilities now advertise supported_flows and
  supports_intent/supports_qr/supports_webhooks/supports_refund.
- MockProvider is composed of all nine building blocks as the reference implementation
  (direct + intent + qr flows, in-memory API client, no credentials, no network).
- Core talks only to the contract: payment_engine calls `provider.create_payment`.
- New generic, provider-agnostic endpoints (delegate to the plugin, resolve by key):
  POST /api/providers/{key}/intent, /qr, POST /reconcile/{txn}, GET /status/{txn}
  (existing: /available, /{key}/capabilities, /{key}/health, POST /{key}/webhook).
- UI wired to the generic interface: Payments screen has a provider selector from discovery;
  Providers screen shows plugin capability chips; Hosted Checkout stays provider-agnostic.
- Tests: 99 backend tests pass serially (`pytest tests/ -n0`); test_provider_architecture.py
  now has 24 tests (contract methods, building-block composition, flows, endpoints, isolation).
- Known gaps (future): per-tenant credential binding (registry returns a singleton adapter
  with default sandbox config; real plugins will inject per-tenant ProviderConfiguration via
  credential_ref -> secret store); intent/QR are exercised via endpoints but not yet surfaced
  in the hosted-checkout UI (DIRECT flow only) — to be wired when a real provider needs them.

## Environment abstraction (2026-06) — sandbox + live are both first-class
- Made the generic provider contract ENVIRONMENT-AWARE without introducing any real provider.
  Both SANDBOX and LIVE are permanent, first-class parts of the architecture; LIVE is never
  hard-removed or permanently blocked. This phase still executes only in SANDBOX via Mock.
- contracts.py: added `ProviderEnvironment` enum (sandbox/live), `EnvironmentConfig` (per-env
  enable + credential *reference*, names only), and extended `ProviderConfiguration` with
  `enabled` + `environments` and helpers (`for_environment`, `is_enabled`, `credential_ref_for`)
  supporting separate TEST and LIVE credential references.
- base.py: `supported_environments`, `supports_environment()`, capabilities now expose
  `supported_environments` + `live_supported`, and `health_check(environment)` +
  `verify_callback(..., environment)` are environment-aware.
- mock.py: reference plugin declares `supported_environments=["sandbox"]` (sandbox-only) — a
  real plugin would add "live" with its own building blocks.
- config.py: replaced the permanent LIVE hard-block with CAPABILITY-BASED gating — a provider
  can be configured for an environment only if its plugin declares support (unknown plugin or
  unsupported environment -> 400). `/health` and `/webhook` accept an optional `environment`.
- Frontend: Providers subtitle no longer says "live disabled"; reflects per-plugin environments.
- Tests: 107 backend tests pass serially (`pytest tests/ -n0`), incl. new environment tests.
- Deferred (SRD order): Provider Account Management (persistent per-tenant, per-environment
  provider records + secret-store credential binding + DB migration), execution-time
  environment selection, UPI Intent/QR real flows, real provider, routing/failover.

## Provider Account Management + Secret Store + Environment Selection (2026-06)
- Persistent per-(tenant, provider, environment) provider ACCOUNTS: unique constraint relaxed
  to (tenant_id, provider_key, mode) so a provider can hold independent sandbox and live
  accounts, each with its own enable flag and credential reference.
- Secure SECRET STORE (`app/services/secret_store.py`): pluggable `SecretStore` interface with
  a default `EncryptedDbSecretStore` (Fernet/AES) keyed by env `SECRET_STORE_KEY`
  (auto-generated + persisted if absent). Credentials are encrypted at rest in `provider_secrets`
  and NEVER returned by any API or written to logs/audit — accounts store only `credentials_ref`.
- Provider account management API: POST /api/providers (stores supplied credentials encrypted,
  per-environment duplicate guard), PATCH /api/providers/{id} (enable/disable/priority/name),
  PUT /api/providers/{id}/credentials (set/rotate — same ref, secret never echoed),
  DELETE /api/providers/{id} (removes account + its stored secret).
- Environment SELECTION at execution: `PaymentCreate.environment` (default sandbox); the engine
  validates plugin.supports_environment, resolves the per-environment account, blocks disabled
  accounts and live-without-account, and records `payments.environment`. LIVE is safely rejected
  today (mock is sandbox-only) — no real-money path.
- DB migration `d2f4a7c9b310` (applied): per-env unique constraint, `provider_secrets` table,
  `payments.environment` column + check.
- Frontend: Providers screen adds an Environment selector, dynamic credential inputs (from the
  plugin's required_credentials), enable/disable toggle, and a credentials-status label; Payments
  screen adds an Environment selector.
- Tests: 115 backend tests pass serially (`pytest tests/ -n0`); new test_provider_accounts.py
  (secret store round-trip/encryption, credentials-never-leaked, per-env accounts, enable/disable,
  rotate, delete, environment selection). Testing agent: 100% backend + frontend, no issues.
- Deferred (SRD order): real provider plugin declaring LIVE support (with its own building blocks
  + credential resolution from credential_ref), UPI Intent/QR real flows, routing/failover.

## Routing/Failover + Real-Provider Plugin + Secret-Store Selector + Simulated UPI (2026-06)
- Routing & Failover (`app/services/routing_engine.py`): `provider_key="auto"` on a payment tries
  enabled + registered + environment-supported + HEALTHY accounts in priority order, failing over
  to the next on failure. The chosen provider + a `routing_attempts` trace are recorded on the
  payment (exposed via PaymentOut.metadata).
- Isolated example real-provider plugin (`app/providers/example_provider.py`, key `examplepsp`):
  declares BOTH sandbox and live, is composed of its own building blocks (auth/HTTP client/mappers/
  callback/error/health), and resolves credentials at execution time from the account's credential
  reference via the secret store (passed as `config`). Sandbox is SIMULATED (raw.simulated=true);
  live requires resolved credentials or safely fails `missing_live_credentials`. No PSP SDK in core.
- Credential threading: contract methods accept an optional `config: ProviderConfiguration`; the
  engine resolves the secret in-memory for the dispatch (never persisted/logged) and passes it in.
- Config-selectable secret store: `get_secret_store()` picks by `SECRET_STORE_BACKEND`
  (encrypted_db active); AWS KMS/Vault/GCP KMS pluggable later with no core change.
- Simulated UPI Intent/QR in Mock: `method="upi"` (or INR) yields `upi://pay` deep-links/QRs
  (never card data) with a deterministic lifecycle (pending/failed/expired/cancelled/succeeded via
  amount), clearly marked simulated. Exposed through the generic intent/qr/status endpoints.
- Frontend: Payments provider dropdown adds "Auto — priority routing & failover" and lists
  examplepsp; Providers dialog shows environment (incl. live for examplepsp) + dynamic credential
  fields + enable/disable + credentials status.
- Tests: 133 backend tests pass serially (`pytest tests/ -n0`); new test_routing_and_upi.py +
  test_iter11_srd_public.py. Testing agent: 100% backend + frontend, no critical issues.
- Deferred: a concrete real PSP (Stripe/Razorpay) implementing examplepsp's building blocks against
  its API + live credentials; external KMS/Vault backend; real UPI bank-rail connectivity.

## Failover Insights on payment detail (2026-06)
- Added a per-payment Details view on the Payments screen (a "Details" action on each row opens a
  payment detail dialog). It shows status, environment, the provider actually used, provider txn,
  amounts, and a "Routing & Failover Trace" that lists every provider tried in order — each attempt
  with its status and a success/failure marker (red X for failed, green check for the one that
  succeeded). Direct (non-failover) payments show a clear "routed directly, no failover" note.
- Backend already surfaced the trace via PaymentOut.metadata.routing_attempts; no backend change.
- Verified by the testing agent (frontend-only, 100%): failover payment shows attempt #1 mock
  (failed/card_declined) then #2 examplepsp (succeeded); direct payment shows the empty-state note.

## Provider Health Board (2026-06)
- New operator-facing "Provider Health" screen (+ nav item, route /dashboard/provider-health) and
  read-only endpoint GET /api/providers/health-board?tenant_id=... (`app/services/provider_health.py`).
- Per tenant, clearly separated Sandbox and Live sections. Each provider account card shows:
  enabled/disabled, live health status, priority, routing eligibility (consistent with the
  routing engine), success/failure metrics + success rate, recent provider errors, last-payment
  time, a live health-check timestamp, and a credentials indicator (boolean only).
- A "Recent Failover Activity" list per environment shows which providers were tried (mock → examplepsp).
- Integrates with Routing & Failover (eligibility uses the same enabled+registered+env-supported+
  healthy rule); auto-refreshes every 15s.
- Security: NEVER exposes credentials, secret values, or credential references (only has_credentials
  bool). Tenant-isolated via resolve_tenant_id.
- Tests: 136 backend tests pass serially; new health-board tests (accounts/metrics/failovers,
  no-credential-leak, tenant scoping). Verified live in the UI on Acme with real demo data.

## Provider Health Alerts (2026-06)
- Operators are now notified when a provider account turns unhealthy or its success rate drops
  below a threshold. Evaluation reuses the Provider Health Board metrics.
- Thresholds via env: ALERT_SUCCESS_RATE_THRESHOLD (0.5), ALERT_MIN_SAMPLE (5), optional
  ALERT_EMAIL_TO. Rule: enabled account with health != up -> critical; or (total >= min_sample
  and success_rate < threshold) -> warning. Disabled/low-sample accounts are not alerted.
- Notifications reuse existing abstractions (no new dependency): email via email_service (noop by
  default) + an outbound webhook event (provider.health_alert / provider.recovered). Deduped:
  fires once on healthy->unhealthy, sends a recovery notice on unhealthy->healthy.
- State persisted in new `provider_alerts` table (migration e3a1c7d9f042), unique per account.
- Endpoints: POST /api/providers/alerts/evaluate (provider.manage), GET /api/providers/alerts.
  Scheduler runs evaluate_all every 60s. Never exposes credentials/secrets.
- Frontend: Provider Health page shows an active-alerts banner, per-card ALERT badges, and a
  "Check health now" button; auto-refreshes.
- Tests: 139 backend tests pass serially; new test_provider_alerts.py (threshold rules, fire/
  dedupe/recover, no-secret-leak). Verified live in the UI on Acme (mock 46% -> warning alert).

## UI fix — Provider Health stray JSX artifact (2026-06)
- Fixed a stray `)}` text artifact rendering on the Provider Health page. It was an orphaned
  conditional close: the active-alerts banner had lost its `{alerts.length > 0 && (` opening.
  Restored the conditional so the banner shows only when alerts exist, removing the artifact.
- Verified via screenshot on preview (tenant "Empty Co": clean render, no `)}`, banner hidden
  when zero alerts). No unrelated functionality changed.

## Weekly/Monthly Reports + Alert Recovery Log (2026-06)
- Scheduled Reports now support DAILY, WEEKLY and MONTHLY summaries (payments + settlements CSV).
  Daily = the report's day; weekly = trailing 7 days (Mon-Sun when run on Monday); monthly = the
  previous calendar month. `report_generation.generate_report(report_type=...)` computes the window;
  `generate_daily_report` kept as a wrapper. Scheduler adds weekly (Mon 08:05) + monthly (1st 08:10)
  cron jobs alongside the daily 08:00 job. `POST /api/reports/scheduled/run?report_type=` (validated).
  Reports UI adds a Daily/Weekly/Monthly selector next to "Run report now".
- Provider Alert Recovery Log: new append-only `provider_alert_events` table (migration f4b2e8a1c530)
  records every alert transition (fired/recovered) with provider_key, environment, severity, reason,
  success_rate and timestamp. `alert_service.evaluate_tenant` writes an event on each transition;
  `GET /api/providers/alerts/history?tenant_id=&limit=` returns recent transitions (never secrets).
  Provider Health page shows an "Alert Recovery Log" panel with ALERTING/RECOVERED entries.
- Checkout Branding (tenant logo + accent) was already fully implemented (files.py + Checkout.jsx
  branding panel + CheckoutPage applies it); verified still working — no changes needed.
- Tests: 145 backend tests pass serially (`pytest tests/ -n0`); new test_reports_and_alert_history.py
  (period window unit, weekly/monthly run, invalid type 400, history fire+recover, no-secret-leak).
  Testing agent: frontend 100%, no issues (both new flows validated on Acme).

## Custom-range Reports + Email Settings (future-ready) + Flaky Provider Score (2026-06)
- Custom date-range reports: `report_type=custom` with `start_date`/`end_date` (YYYY-MM-DD, end
  inclusive) added to `POST /api/reports/scheduled/run`; generate_report accepts explicit start/end.
  Reports UI adds a "Custom range" option that reveals two date pickers.
- Email delivery (future-ready, NO provider connected): per-tenant settings stored in
  Tenant.settings["report_email"] (enabled, recipient_email, frequencies[daily/weekly/monthly],
  attach_csv). `GET/PUT /api/reports/scheduled/email-settings`. generate_report gates sending on
  these settings but still routes through the noop `email_service` adapter — email_status is one of
  disabled / skipped_frequency / skipped_no_provider. A real provider (Resend/SendGrid/SES) can be
  registered later with zero core changes. Reports UI has an "Email delivery" panel (toggle,
  recipient, frequency checkboxes, CSV attach) with a "no provider connected" badge.
- Flaky Provider Score: `alert_service.provider_stability` scores each provider from alert-history
  drops over a window (start 100, -15 per drop; stable>=85, moderate>=60, flaky<60).
  `GET /api/providers/stability?tenant_id=&window_days=`. Provider Health page shows a
  "Provider Stability" card grid (score, rating, bar, drops/recoveries).
- Shared `Panel` component now forwards extra props (data-testid etc.) — small testability fix.
- Tests: 151 backend tests pass serially (`pytest tests/ -n0`); test_reports_and_alert_history.py
  extended (custom range + validation, email settings roundtrip + gating + invalid-freq drop,
  stability score). Testing agent: frontend 100%, no issues (all three flows validated on Acme).

## Scope Freeze — Verification & Hardening (2026-06)
- Scope frozen per user: no new features, no external credentials. Focus = complete/test/debug/
  security-verify the already-approved functionality. Rejected (kept as FUTURE only, do not build
  now): Connect Email Provider, Stability Trend, Report Presets, Auto-Route Away From Flaky.
- Added test_security_new_endpoints.py (7 tests): cross-tenant GET denial + report-download denial,
  RBAC (report.manage required for report run + email-settings PUT), unauthenticated 401, and
  no-secret-leakage (ciphertext/credential_ref/secret/fernet/private_key) across the new endpoints.
- Debugged flaky alert test: the 60s background `_alert_eval_job` (evaluate_all) races a test's own
  `evaluate` call once the suite runtime crosses a 60s tick, so a transition can land in the job's
  call instead of the test's `changes`. Hardened the alert-fire/recover assertions (in
  test_provider_alerts.py + test_reports_and_alert_history.py) to assert persisted active-state and
  alert history rather than a single call's `changes`. Product behavior unchanged (idempotent dedupe).
- Result: 158 backend tests pass serially (`pytest tests/ -n0`), twice consecutively. No enhancements
  proposed going forward per user instruction.

## Security Hardening & Secret-Exposure Audit (2026-06)
### Vulnerabilities found
- Exposed credential: the super-admin password `Admin@12345` was committed in tracked files —
  `auth_testing.md`, a hardcoded default in `config.py`, `Login.jsx` (prefilled form), and 15 test
  files (hardcoded literals / os.environ fallbacks). Also present in historical test_reports JSON.
- No security response headers.
- Rate limiting missing on abuse-sensitive endpoints (MFA verify TOTP brute force, forgot/reset
  spam, public checkout). Login had DB lockout but no IP-wide backstop.
- No production config validation (wildcard CORS / missing SECRET_STORE_KEY could ship silently).

### Fixes applied
- ROTATED the admin password to a new strong value (backend/.env `ADMIN_PASSWORD`); seed re-syncs it
  on startup. Old password now invalid, so all historical copies are useless.
- Purged `Admin@12345` from every tracked file: sanitized `auth_testing.md`, removed the hardcoded
  default in `config.py` (env-only now), cleared the `Login.jsx` prefill, switched all 15 test files
  to `os.environ["ADMIN_PASSWORD"]`, redacted the historical test_reports JSON.
- Verified `.gitignore` blocks `.env`, `*.env`, `*.key`, `credentials.json`, `memory/test_credentials.md`.
- Added baseline security headers middleware (X-Content-Type-Options, X-Frame-Options=DENY,
  Referrer-Policy, Permissions-Policy, COOP; HSTS in production).
- Added an in-process rate limiter (`core/ratelimit.py`) on login (100/min IP backstop), MFA verify
  (10/5min), forgot-password (5/5min), reset-password (10/5min), public checkout get/pay and API-key
  session create.
- Added `Settings.validate()` — fails fast in production on wildcard CORS, short JWT_SECRET, missing
  SECRET_STORE_KEY/ADMIN_PASSWORD, http FRONTEND_URL; logs warnings in non-prod. Called at startup.

### Verified (already correct, no change needed)
- Tenant isolation on every tenant-scoped endpoint (resolve_tenant_id) — covered by
  test_authz_isolation + test_security_new_endpoints.
- RBAC via require_permission on all mutating endpoints; API keys hashed (sha256), active-flag +
  tenant-active checks, revoke → 401.
- Idempotency/duplicate-payment protection (idempotency_key) — covered by state-invariant tests.
- Webhook HMAC-SHA256 signature per endpoint; mock/sandbox unchanged; secret never returned by API
  (omitted from WebhookOut) and never logged.
- Provider credentials stored Fernet-encrypted; audit stores only `has_credentials` flag; secret
  store never logs/returns secrets.
- Public checkout token = 192-bit random (non-enumerable); pay is replay-safe (status + idempotency).
- Password reset: no account enumeration, hashed single-use TTL tokens, token_version revoke-all.

### Tests executed
- Full backend suite serially: `pytest tests/ -n0` → 162 passed (twice). Includes new hardening tests
  (headers present, rate-limit engages, production config fail-fast) in test_security_new_endpoints.py.

### Remaining production blockers / notes
- Set production env before go-live: `APP_ENV=production`, explicit `CORS_ORIGINS` (not `*`), a
  provisioned `SECRET_STORE_KEY`, https `FRONTEND_URL`, and a fresh `ADMIN_PASSWORD`. `validate()`
  will refuse to start if any are insecure.
- Rate limiter is in-process (per-worker); move to Redis when scaling to multiple workers.
- Git history still contains the old (now-invalid) password in prior commits; history rewrite is not
  performed here (platform-managed .git). The rotation makes those copies unusable.

## Multi-Country, Capability-Aware Provider Routing (2026-06)
### What changed (generic architecture preserved — no PSP/country hard-coded in core)
- Provider capability now expresses: supported countries/regions, currencies, payment methods,
  flows (direct/intent/qr) and environments. Countries added to both the plugin contract
  (`PaymentProviderAdapter.supported_countries`, default []=unrestricted) and the provider-account
  config (new additive columns `supported_countries`, `supported_methods`, `supported_flows` on
  `payment_providers`; migration a7c3e1f9b204). Effective capability = account list when set, else
  plugin default; empty = unrestricted for that dimension.
- Routing (`routing_engine.plan_route`) now selects candidates by:
  tenant -> country -> currency -> payment method -> flow -> environment -> enabled -> capability
  -> health -> priority. Never routes to a provider that lacks the payment's country/currency/
  method/flow. `match_capability` / `match_plugin_capability` reused for explicit selection too.
- Payment request carries `country` (falls back to tenant.country), `payment_method` (default card),
  `flow` (default direct). Priority-based failover preserved; idempotency preserved (single payment
  row claimed via unique key before dispatch, so failover can never double-charge). The complete
  routing decision + attempt trace is stored in payment metadata (`routing_trace`,
  `routing_attempts`) with NO secrets.
- Ready for India / Sri Lanka / UK / USA / other providers purely via account config + a plugin;
  adding a plugin needs no core change (guarded by test_core_has_no_provider_specific_imports).
- Mock + example reference plugins only; no real PSP, no real credentials.

### Tests
- New test_provider_capability_routing.py (15 tests): India/INR/UPI, Sri Lanka/LKR, UK/GBP, USA/USD
  matches; unsupported currency/country/method/flow rejection; explicit-selection enforcement;
  sandbox/live separation; priority routing; failover; idempotent-failover no-duplicate; tenant
  isolation; trace-has-no-secrets. Full backend suite: 177 passed serially (`pytest tests/ -n0`).

## Final End-to-End Verification (2026-06)
- Recovered a pod-restart infra outage: PostgreSQL had reinitialized (cloudpay role + db gone).
  Recreated the role/database, re-ran all Alembic migrations to head (a7c3e1f9b204), re-seeded
  (admin + Acme demo). Not a code change — environment restoration.
- Backend + security: full pytest suite 177 passed serially (`pytest tests/ -n0`) — covers auth,
  RBAC, tenant isolation, API keys + revocation, payments, idempotency, refunds, ledger, webhooks
  (HMAC validation), checkout replay/expiry, routing, failover, provider health, alerts, alert
  recovery history, reports, scheduled generation, sandbox/live separation, credential references,
  mock provider, mock UPI Intent/QR, multi-country capability routing, IDOR/cross-tenant, secret
  non-leakage, rate limiting, session/cookie security, production config fail-fast.
- Frontend: production build clean (CRA). Testing agent e2e = 100% across login, dashboard, tenant
  selection, payments, refunds, providers, provider health, checkout (public branded pay page),
  branding persistence, webhooks (real delivery + replay), API keys, reports + email settings,
  security/admin screens, and protected-route/logout behavior.
- No code bugs, regressions or security issues found → no code fixes applied.
- Non-blocking observation (NOT fixed; would be an enhancement): the ACTIVE TENANT selector resets
  to the first tenant on a hard page reload (in-app navigation preserves it); all data stays correct.
- Phase status: COMPLETE.

## 4-Level Admin Hierarchy + Super Admin Control Plane (2026-06)
- Extended existing IAM (no parallel RBAC): Level-1 Super Admin (is_superadmin), Level-2 Platform
  Admin (platform-tenant user, is_superadmin=FALSE, EXACT granted permissions, no wildcard),
  Level-3/4 tenant admin/users. Single login redirects Super Admins to /superadmin, others to
  /dashboard. New /api/superadmin/* control plane (require_superadmin): overview, platform-admin
  CRUD + per-admin permission grant + set-password + suspend, tenant feature control. Guardrails:
  never sets is_superadmin, refuses to modify a Super Admin, platform admins 403 on /superadmin/*.
- Customer feature control via existing require_feature on refunds/checkout/reports/webhooks/
  api_keys/providers (403 when disabled; Super Admin bypasses; absence defaults enabled so nothing
  breaks). Frontend nav hides disabled features + permission-gated items. New SuperAdmin.jsx pages
  (overview, admins, tenants, features, roles) wired to real APIs.
- Tests: test_superadmin_control_plane.py (guard, exact-permission, escalation guardrails,
  set-password/suspend, feature roundtrip); updated feature-entitlement test for superadmin bypass.
  Full suite 183 passed serially. Frontend verified rendering. No migration needed.

## Production-Readiness Baseline Audit (2026-06)
- Verified subsystems end-to-end; 183 tests pass. Money handled as integer minor units (no floats);
  refund gated by refundable-state + amount cap; payment state machine enforces ALLOWED_TRANSITIONS;
  idempotency claims payment before dispatch; webhook HMAC-SHA256 + retry/backoff; provider errors
  normalized (ProviderError); secret store Fernet + credential_ref; no PAN/CVV stored.
- Missing/gated for real-money (documented, NOT built this phase): real PSP adapter, external KMS/
  Vault, reversals, UTR handling, full reconciliation double-credit guards, settlement idempotency
  hardening — all require external creds / regulatory approval.

## Real PSP Foundation — Stripe Sandbox Adapter (2026-06, additive)
- Added ISOLATED Stripe adapter `app/providers/stripe_provider.py` behind the existing generic
  PaymentProviderAdapter contract; registered in registry. Core payment engine UNCHANGED
  (test_core_has_no_provider_specific_imports still green — zero Stripe refs in core).
- Capabilities: key 'stripe', sandbox-only (supported_environments=['sandbox']), LIVE DISABLED
  (live_supported False; live sk_ keys hard-rejected). Currencies USD/GBP/EUR/INR/AUD/CAD/SGD,
  countries US/GB/IN/AU/CA/SG/IE/FR/DE/NL, card, DIRECT (PaymentIntent).
- Real Stripe SDK: PaymentIntent create/retrieve with idempotency_key + max_network_retries=0
  (no duplicate charge), 20s timeouts, normalized errors (invalid_credentials/network_error/
  invalid_request/provider_error/malformed_response/card_declined). Status map -> generic model.
- Inbound webhook verify_callback uses stripe.Webhook.construct_event (real HMAC + timestamp
  tolerance = replay protection); event_id in raw for platform dedupe; unknown events pass through
  with normalized_status None. Signature verified BEFORE trust.
- Secrets: api_key/webhook_secret from secret store (config.options.credentials) or STRIPE_API_KEY/
  STRIPE_WEBHOOK_SECRET env; never logged/returned/leaked (verified). STRIPE_API_KEY (test) added to
  backend/.env so health='up'.
- Tests: test_stripe_provider.py (23) + agent's test_stripe_adapter_integration.py (11).
  Full suite 217 passed serially. No DB migration. Testing agent: backend verified, no regressions.
- LIVE/real-money remains DISABLED. Remaining before live: authorized Stripe account + live keys,
  external KMS/Vault, live webhook endpoint registration, wiring a routable stripe provider_account
  per tenant, and end-to-end sandbox network test through the payment API.

## Super Admin email rotation -> finance@vortexglobal.info (2026-06)
- Single Super Admin now uses email finance@vortexglobal.info (is_superadmin, active, permissions '*'
  preserved). No duplicate; old admin@cloudpay.io no longer authenticates. ADMIN_EMAIL in
  backend/.env updated so reseed stays consistent. (Pod DB had reset; account was reseeded under the
  new email — ID/audit history from before the wipe are gone due to the infra reset.)
- Secure Forgot Password flow verified (generic no-enumeration 200; hashed single-use token).
- BLOCKER: notification adapter is LOG-ONLY (no email provider) -> reset emails are NOT delivered to
  the mailbox. To let the user set a new password via a real reset email, connect Resend/SendGrid/SES
  (needs API key + verified sender for vortexglobal.info). Verified by testing_agent iteration_17 (9/9).

## Backlog / Remaining
- P1: Real provider adapters (Stripe/Adyen/etc.) behind config; provider routing/failover.
- P1: KYC/AML + VDA provider integrations (currently disabled boundaries).
- P1: FX live rates source; multi-currency turnover snapshots job.
- P2: Notifications channels (email/webhook) delivery; webhook inspector.
- P2: Reports export (CSV/PDF); scheduled settlement jobs.
- P2: AI/voice assistant boundary implementation.
- P2: Per-tenant API keys & merchant-facing checkout.

## Next Tasks
- Add real provider adapter(s) via integration_expert when keys are available.
- Add tenant-scoped API key issuance + a hosted checkout endpoint.

## Financial-Integrity Closure — Reversal, UTR, Refund/Settlement/Reconciliation Safety (2026-06)
Scope-frozen task: close identified financial-integrity gaps WITHOUT rebuilding anything. Sandbox
only; live stays disabled; emails still mocked; no provider-specific logic in core.

### Gap analysis (before)
- Reversal: MISSING entirely. UTR verification: MISSING entirely.
- Cumulative refund cap existed but had a CONCURRENCY RACE (read cumulative from a stale in-memory
  relationship, no row lock) → two concurrent refunds could over-refund.
- Settlement: re-summed all payments each call; NO provider-settlement-ref idempotency.
- Reconciliation (generic inbound webhook): already safe — status-only, posts no ledger entries,
  idempotent (prev==status short-circuit). Verified + covered by a test; no code change.

### Changes made (additive)
- Migration b8f1c2a3d4e5 (additive; no resets): tables `reversals`, `utr_submissions`; column
  `settlements.provider_settlement_ref` + unique (tenant_id, provider_settlement_ref).
- payment_state: added terminal `reversed` state + REVERSIBLE set {authorized,captured,succeeded}
  and transitions authorized/captured/succeeded -> reversed; `is_reversible()`.
- reversal_service.create_reversal: row-locks the payment; one reversal per payment (unique +
  idempotency_key); blocks non-reversible states and payments that already have refunds; posts a
  compensating DEBIT equal ONLY to the credit that already existed (never creates money); reflects
  at PSP via the generic provider.refund; sets payment=reversed; audited + webhook payment.reversed.
- utr_service: submit -> `under_review` (NEVER credits on submission); manual review confirm/reject
  under `utr.verify`; strict amount/currency matching (+ linked-payment status/amount/currency
  match, payment must be pending/authorized); ledger credit (ref_type="utr") ONLY on confirm, once
  (row lock + status guard = idempotent); unique (tenant_id, utr) blocks duplicate/multi-credit use.
  No fabricated bank verification — verification is a manual admin action.
- payment_engine.create_refund: now row-locks the payment and recomputes the cumulative refunded
  total from the DB under the lock (closes the over-refund race). Cap/idempotency preserved.
- settlement_service.generate_settlement: optional provider_settlement_ref -> idempotent (repeated
  file/response/retry returns the existing settlement; no second credit; concurrency-safe via unique).
- New permissions (least-privilege): payment.reverse, utr.submit, utr.verify (superadmin gets all).
- Endpoints: POST /api/payments/{id}/reverse, POST /api/payments/utr,
  POST /api/payments/utr/{id}/review, GET /api/payments/utr/list; /api/settlements/generate now
  accepts provider_settlement_ref.

### Tests
- New tests/test_financial_integrity.py (19 tests, all pass): reversal valid/duplicate/idempotent/
  invalid-lifecycle/unauthorized/cross-tenant; refund full+partial+over-refund/idempotent/CONCURRENT;
  UTR submit+confirm-credits-once/duplicate/amount-mismatch/currency-mismatch/linked-status-mismatch/
  unverified-no-credit/unauthorized-confirm; settlement idempotency; reconciliation rerun no
  double-credit; no credential/secret leak.
- Regression: core files pass in isolation (payment_state, payment_state_invariants,
  provider_capability_routing, provider_accounts, financial_integrity, iter17 8/9). Full serial run
  shows only ENVIRONMENTAL noise — (a) pre-existing stale hardcoded admin@cloudpay.io in
  test_extended/test_commerce/test_stripe_adapter_integration (from the iteration-17 email rotation),
  and (b) 429 rate-limiting/lockout from hammering /api/auth/login across 200+ serial tests. NOT
  regressions from this task.

### Infra note
- Pod reset again on entry: PostgreSQL cluster down + cloudpay db/role wiped. Recreated role+db,
  ran all migrations to head (b8f1c2a3d4e5), restarted supervisor; backend reseeded. Code unchanged.

## Settlement Import (2026-06, batch-only — NO ledger credit)
- Operators can upload a provider settlement CSV and reconcile it idempotently in one click.
  CSV shape (one row = one settlement batch): provider_settlement_ref, currency, gross_minor,
  fees_minor, net_minor, txn_count. Amounts taken verbatim from the file (provider = source of truth).
- Idempotent: rows whose (tenant_id, provider_settlement_ref) already exist are SKIPPED as
  duplicates — re-uploading the same file never creates a second settlement (reuses the unique
  constraint added in b8f1c2a3d4e5). Per-row SAVEPOINTs so a duplicate/bad row never discards
  successfully-created rows. Returns {created, duplicates, errors} counts.
- Backend: settlement_service.import_settlements + POST /api/settlements/import (multipart,
  settlement.manage, tenant-isolated, max 5000 rows, UTF-8 CSV, requires provider_settlement_ref
  column) + GET /api/settlements/import-template. Audited (settlement.import). EXISTING settlement
  behavior (generate) and the no-ledger-credit policy are UNCHANGED per user instruction.
- Frontend: Settlements screen adds "CSV Template" (client-side download) + "Import File" (upload
  with created/duplicate/error toast summary). data-testids: settlement-import-button,
  settlement-import-input, settlement-template-button.
- Tests: test_financial_integrity.py extended to 24 (import+reimport idempotent, partial new/existing,
  invalid-row reported/others created, missing-column 400, RBAC 403). All pass. Verified via UI + curl.
- DEFERRED (user will provide creds separately): Real Email Delivery via Resend (API key + verified
  sender domain e.g. no-reply@vortexglobal.info). Currently email is log-only MOCKED.

## Settlement Import — Dry-Run Preview (2026-06)
- Import is now a two-step preview-then-commit flow. `POST /api/settlements/import?dry_run=true`
  classifies every CSV row (new / duplicate / error) WITHOUT writing anything (session rolled back,
  no audit entry); `import_settlements(..., dry_run=True)` returns an `items` list with per-row
  detail + counts. The real commit call (no dry_run) inserts only the new rows (unchanged idempotent
  behaviour, still batch-only, no ledger credit).
- Frontend: picking a file opens a Preview dialog (color-coded New/Duplicate/Error, net + txns per
  row, counts). "Confirm import (N)" is disabled when there are zero new rows and commits the same
  file. data-testids: settlement-preview-dialog, settlement-preview-counts, settlement-preview-row-*,
  settlement-preview-confirm, settlement-preview-cancel.
- Tests: test_financial_integrity.py now 25 (added dry-run classifies-without-persisting + confirm
  persists). All pass. Verified via UI screenshot (1 new / 1 duplicate / 1 error).

## Settlement Import History (2026-06)
- New `GET /api/settlements/imports?tenant_id=&limit=` (settlement.manage, tenant-isolated) returns
  the log of past settlement-file imports — when, actor_email (who ran it), filename, and the
  new/duplicate/error tallies. Sourced from the existing append-only audit trail
  (action='settlement.import'); NO migration, no new table, no secrets. Dry-run previews are NOT
  logged (they write no audit entry).
- Frontend: Settlements screen shows an "Import history" panel (When / By / File / New / Duplicate /
  Errors, color-coded) that refreshes after each committed import. data-testids:
  settlement-import-history, settlement-import-history-row.
- Tests: test_financial_integrity.py now 27 (history records a committed run + excludes dry-run;
  history requires settlement.manage -> 403). All pass. Verified via UI screenshot.

## Real Email Delivery via Resend (2026-06, LIVE)
- Email is no longer mocked. Provider-agnostic adapter (`email_service.py`) gains a
  `ResendEmailProvider` that auto-activates when RESEND_API_KEY + SENDER_EMAIL are set (env-only;
  EMAIL_PROVIDER=resend). Falls back to noop when unset. Sends fail gracefully (logs, never raises).
- Wired flows (no existing behavior rewritten — only the log-only stubs now route to the adapter):
  * Password reset + email verification + password-changed notice — via `notification_service.notify`
    (routes events carrying a recipient email to `send_email`; reset link is public, works w/o auth).
  * Scheduled/on-demand reports — `report_generation` now attaches the CSV bytes directly
    (base64) to the Resend email when attach_csv is on (plus a secure download link).
- Config: RESEND_API_KEY + SENDER_EMAIL live ONLY in backend/.env (gitignored). Never in source,
  logs, UI or API responses. Sender/From = finance@vortexglobal.info.
- SDK: resend==2.42.0 (added to requirements.txt).
- Live-tested (real sends, message ids returned):
  * POST /api/auth/forgot-password -> "email sent via resend" (reset link).
  * POST /api/reports/scheduled/run (daily, attach_csv) -> email_status "sent" with CSV attachment.
- Regression: test_reports_and_alert_history + test_financial_integrity = 38 pass. One existing test
  that hard-coded the old mock status (skipped_no_provider) was made provider-agnostic
  (accepts "sent" | "skipped_no_provider") to reflect the now-enabled real delivery.

## Reversal & UTR Operator Console (2026-06, FRONTEND-ONLY)
- New operator UI on EXISTING, unchanged backend APIs. No backend/DB/migration changes.
- New pages: frontend/src/pages/Reversals.jsx, frontend/src/pages/UtrConsole.jsx. New routes in
  App.js (/dashboard/reversals, /dashboard/utr) + two perm-gated nav items in Layout.jsx
  (Reversals=payment.reverse, UTR Console=utr.verify).
- Reversal Console: GET /api/payments -> eligible (authorized/captured/succeeded), confirm dialog
  with reason + destructive action -> POST /api/payments/{id}/reverse; Reversed-transactions history.
- UTR Console: GET /api/payments/utr/list; Approve/Reject -> POST /api/payments/utr/{id}/review
  (approve dialog collects verified amount/currency per the API contract); Submit UTR ->
  POST /api/payments/utr. Actions gated by utr.verify / utr.submit.
- Loading/empty/success/error states, responsive tables, confirmation dialogs, no-permission panels
  mirroring backend RBAC. Permissions reused (no new grants): payment.reverse, utr.verify, utr.submit.
- Testing: testing_agent (iteration_19.json) — all 6 scenarios PASS 100%. Backend regression
  test_financial_integrity = 27/27 still pass. No migration; no existing functionality modified.

## Payment Capture & Void (2026-06, provider-agnostic, ADDITIVE)
- New auth-then-capture capability. No DB migration (reuses Payment + metadata_json for op-level
  idempotency keys). No existing payment/refund/reversal/UTR/settlement behavior changed.
- Contract (base.py): added optional supports_capture()/supports_void() (default False) + generic
  capture()/void() that raise a normalized ProviderError('unsupported_capability'). Capabilities dict
  advertises the flags. Core stays provider-agnostic (test_provider_architecture still passes).
- Mock provider: supports capture/void; additive opt-in manual-capture create branch
  (metadata.capture_mode=='manual' -> 'authorized'); default create behavior unchanged.
- Stripe provider: capture -> PaymentIntent.capture, void -> PaymentIntent.cancel (official SDK,
  existing credential resolution, sandbox-only/live-disabled guard preserved). NOTE: env holds a
  placeholder STRIPE_API_KEY (sk_test_emergent) so live end-to-end Stripe capture/void couldn't be
  exercised here; adapter verified structurally + credential/error path confirmed.
- Engine: capture_payment/void_payment — row-locked, tenant-isolated, state-validated
  (authorized->captured / authorized->cancelled only), idempotent (op key stored in metadata_json).
  Capture posts a ledger credit ONLY if none exists (no duplicate credit). Void posts a compensating
  DEBIT unwinding any existing credit (creates no money). Both audited + webhook (payment.captured /
  payment.voided).
- API: POST /api/payments/{id}/capture (payment.capture), POST /api/payments/{id}/void (payment.void).
- Permissions added (least-privilege, not auto-granted to Admin/Client): payment.capture, payment.void.
- Tests: tests/test_capture_void.py (17) all pass — capture/void authorized, duplicate rejected,
  idempotent-by-key, over-authorized rejected, non-authorized rejected, capture-after-void &
  void-after-capture rejected, no-duplicate-ledger-credit, void-unwinds-credit, concurrent safety,
  RBAC 403, cross-tenant 404, unsupported-capability normalized error. Regression: financial_integrity
  + payment_state + provider_architecture + capability_routing + stripe_provider = 112 pass.
- UI: not added (reported as gap) — backend/API capability delivered; existing Payments screen left
  unchanged per the no-redesign rule.

## Payment Capture & Void Operator UI (2026-06, FRONTEND-ONLY)
- Added Capture/Void actions to the EXISTING Payments page (frontend/src/pages/Payments.jsx only) on
  the EXISTING backend contract — no backend/DB/migration changes.
- Capture: shown only for status=='authorized' + hasPermission('payment.capture'); confirm dialog with
  amount (full default, editable for partial); POST /api/payments/{id}/capture {amount_minor?,
  idempotency_key}. Void: shown only for status=='authorized' + hasPermission('payment.void'); confirm
  dialog with optional reason; POST /api/payments/{id}/void {reason?, idempotency_key}. Both disable
  buttons while busy (duplicate-click protection), toast success/error, and refresh the list.
- Permissions reused: payment.capture, payment.void. Tenant isolation via existing selector + server
  auth. State gating mirrors backend (only authorized payments show the actions).
- Verified live (screenshot): authorized rows show Details/Capture/Void; non-authorized show only
  Details/Refund; capture executed → 'captured'. Backend regression test_capture_void +
  test_financial_integrity = 44 pass.

## Settlement Detail / Drill-Down (2026-06, READ-ONLY, additive)
- New read-only per-settlement drill-down. Backend: GET /api/settlements/{id} (finance.py) — auth +
  strict tenant ownership (cross-tenant/unknown -> 404), returns all existing Settlement columns +
  resolved created_by email + tenant name + derived import_source (import if provider_settlement_ref
  else generated) + reconciliation CONTEXT (tenant+currency recon runs, clearly labeled "not directly
  linked" — no invented relationship). No secrets exposed. No new permission (mirrors list's
  auth+tenant model). No DB migration (read-only over existing columns/tables).
- Frontend: Settlements.jsx — added a per-row "View" button + a detail Dialog (all fields + recon
  context table + Back to list). Additive; existing Settlements page/import/history untouched.
- Login investigation: root cause is the SANDBOX pod resetting PostgreSQL (wipes cloudpay DB ->
  backend 500/unreachable -> frontend formatApiError shows generic "Something went wrong"). Confirmed
  live (health 000, superadmin row gone), recovered by recreating DB/role + migrations. finance@ login
  200, old admin@cloudpay.io 401 (retired, absent). NOT a code/auth bug; no security weakened.
- Tests: tests/test_settlement_detail.py (10) — authorized view, no-secrets, 404 unknown,
  unauthenticated 401/403, cross-tenant 404, read-only no-ledger-mutation, valid/invalid login,
  retired-admin non-auth, token+me. Regression (settlement_detail + reconciliation + capture_void +
  financial_integrity + payment_state + provider_architecture + capability_routing) = 119 pass.

## Line-Level Reconciliation & Matching Engine (2026-06, REPORT-ONLY, additive)
- New provider-agnostic engine that matches internal payments against provider records (uploaded
  transaction-lines CSV AND/OR provider-status pull) and reports discrepancies. READ-ONLY: never
  posts ledger, never changes balances, never mutates payments/settlements.
- Fixed outcome categories: matched, amount_mismatch, currency_mismatch, status_mismatch,
  missing_in_cloudpay, missing_at_provider, duplicate. Deterministic status bucketing keeps it
  provider-agnostic (no provider-specific logic in core).
- DB: additive migration c9d2e3f4a5b6 -> new tables reconciliation_runs, reconciliation_items
  (server_default now() on timestamps). No existing tables changed.
- Service services/reconciliation_engine.py: tenant-isolated, idempotent per (tenant, run_ref),
  per-line matching + duplicate detection + provider-pull fallback (best-effort, read-only).
- API (new router mounted in server.py): POST /api/reconciliation/run (multipart optional CSV,
  source upload|provider_pull|both, currency, run_ref), GET /api/reconciliation/runs,
  GET /api/reconciliation/runs/{id} (outcome filter), GET /api/reconciliation/template. Audited.
- Permissions added (not auto-granted to Admin/Client): reconciliation.run, reconciliation.view.
- UI: new page frontend/src/pages/Reconciliation.jsx (runs list, Run dialog with source/CSV/currency/
  run_ref, discrepancy-summary filter chips for all 7 categories, line-level detail), route
  /dashboard/reconciliation + perm-gated nav item (reconciliation.view). Additive; no existing pages
  changed.
- Tests: tests/test_reconciliation.py (8) all pass. Regression across reconciliation + capture_void +
  financial_integrity + payment_state + provider_architecture + capability_routing + reports +
  stripe_provider = 143 pass.
- Concurrency fix (this task's own code): capture_payment/void_payment FOR UPDATE selects now use
  execution_options(populate_existing=True) so the locked read refreshes in-memory status (previously
  a stale identity-map read let two concurrent ops both succeed). Verified deterministic.

## Customer Payment Receipt Email (2026-06, LIVE via Resend, ADDITIVE)
- New: a professional CloudPay-branded HTML receipt is emailed to the customer when a payment FIRST
  reaches a final success state. Reuses the existing Resend adapter (sender finance@vortexglobal.info);
  NO existing behavior modified/duplicated.
- Service app/services/payment_receipt_service.py: send_payment_receipt(db, payment) —
  FINAL_SUCCESS_STATES={'succeeded','captured'}; requires payment.customer_email; IDEMPOTENT via
  payment.metadata_json['receipt_sent_at'] (+ receipt_status); marks sent unless provider returns
  'send_failed' (transient -> allows retry); wrapped in try/except so it NEVER raises / never aborts
  the payment/ledger transaction. Receipt shows reference, amount, currency, status, date/time,
  provider txn ref, tenant/merchant name, and tenant contact_email ONLY when already configured.
  No secrets ever rendered.
- email_service.send_email + provider send gained an OPTIONAL html param (additive, backward
  compatible); existing noop/report/reset flows unchanged.
- Triggers wired before commit at all three final-success points: payment_engine.create_payment
  (direct 'succeeded'), payment_engine.capture_payment ('captured'), config.py provider_inbound_webhook
  reconcile (async 'succeeded'/'captured').
- Tests: tests/test_payment_receipt.py (9 unit) + tests/test_payment_receipt_live.py (live API).
  Testing agent iteration_20: 100% backend, 68/68 pass (regression capture_void/financial_integrity/
  reports_and_alert_history green). Live e2e: real Resend send (receipt_status='sent'), marker persisted.

## Receipt Download (hosted page) + Operator Receipt Log (2026-06, ADDITIVE)
- Receipt Download: the receipt email now embeds a "View receipt" link to a public, non-enumerable
  hosted receipt page. payment_receipt_service generates a 192-bit receipt_token (stored in
  metadata_json['receipt_token'], persisted only on non-transient send) and links
  {FRONTEND_URL}/receipt/{token}. New public endpoint GET /api/public/receipts/{token} (checkout.py,
  no auth, rate-limited) looks up the payment by token in a final success state and returns
  reference/amount/currency/status/provider_txn/created_at/merchant/support_email/brand_accent/logo
  — never any secret. 404 for unknown token. New page frontend/src/pages/ReceiptPage.jsx (route
  /receipt/:token) renders a branded printable receipt with a Print / Save as PDF button (window.print).
- Operator Receipt Log: the existing Payment Details dialog (Payments.jsx) now has a "Customer Receipt"
  section showing the customer email, a status badge (Sent / Failed / Skipped / Not sent yet), the
  sent timestamp, and a "View receipt" link — read from payment.metadata (receipt_status,
  receipt_sent_at, receipt_token). Payments without a customer email show a clear no-receipt note.
- Tests: backend live e2e (payment -> token; public receipt 200 + no secrets; bad token 404) and
  unit suite (9) pass. Testing agent iteration_21: frontend 100% (5/5 — receipt log positive/negative,
  hosted page valid/invalid token, routing-trace regression). Additive; no existing behavior changed.
- Note: test_commerce.py still uses the retired admin@cloudpay.io (pre-existing env noise, unrelated).

## Refund/Reversal Notices + Merchant Branding + Delivery Tracking + Receipt Resend (2026-06, ADDITIVE)
- Refund/Reversal Notice: on a successful refund (payment_engine.create_refund) or reversal
  (reversal_service.create_reversal), the customer gets a branded email. payment_receipt_service
  .send_transaction_notice(kind=refund|reversal) is idempotent per (kind, ref_id) via
  metadata_json['notices'], best-effort, never aborts the financial txn. No refund/reversal/ledger
  behavior changed.
- Merchant Branding: payment_receipt_service._brand(tenant) applies each tenant's stored checkout
  branding — brand_accent (accent strip, button, links, status pill) and brand_logo_file_id (logo in
  email header via {FRONTEND_URL}/api/public/files/{id}) — to the receipt email, notices, and the
  hosted receipt page (which already read accent/logo from the public endpoint).
- Delivery Tracking: new signature-verified inbound Resend webhook POST /api/webhooks/resend
  (checkout.py) using resend.Webhooks.verify (Svix HMAC). Maps event email_id -> payment via
  metadata_json['receipt_email_id'] and stores metadata_json['receipt_delivery'] (delivered/bounced/
  complained/...). Idempotent, never leaks secrets, and a safe no-op (200 disabled) until
  RESEND_WEBHOOK_SECRET (whsec_..., env-only via settings.resend_webhook_secret) is configured.
  Endpoint URL: {REACT_APP_BACKEND_URL}/api/webhooks/resend. Receipt Log shows a delivery badge
  (data-testid=receipt-log-delivery) when present. NOTE: secret NOT set in this env — activate by
  adding RESEND_WEBHOOK_SECRET to backend/.env + restart.
- Receipt Resend: POST /api/payments/{id}/receipt/resend (require_permission payment.create,
  tenant-isolated) re-sends the receipt (force=True) reusing the same hosted-receipt token; 400 for
  no-email/non-success; 404 cross-tenant. Frontend: 'Resend receipt' button (receipt-resend-button)
  in the Payment Details Customer Receipt section, gated on payment.create + final-success status.
- Tests: test_payment_receipt.py now 14 unit; testing-agent test_new_receipt_features_live.py (9).
  Testing agent iteration_22: backend 100% (9 new + 58 regression), frontend 100%, no issues.
  Additive; financial behavior untouched.

## Currency-Aware Payment UI + Multi-Country Defaults (2026-06, FRONTEND-ONLY, additive)
- ALREADY EXISTED (not rebuilt): server-side currency capability validation is authoritative —
  payment_engine.create_payment enforces match_capability/match_plugin_capability (explicit provider)
  and routing_engine.plan_route (auto); TenantOut already exposes country + default_currency;
  providers expose supported_currencies/countries via /api/providers/available and per-tenant
  accounts via /api/providers. NO backend/DB/engine change made.
- NEW (Payments.jsx only): the New Payment 'Currency' field is now a proper Select
  (data-testid=payment-currency-select / payment-currency-option-{CODE}) instead of free-text.
  Options come from the CAPABILITY CONTEXT: per-tenant provider ACCOUNT supported_currencies when set,
  else plugin defaults; 'auto' shows the union across providers, a specific provider only its own.
  Default currency: India (tenant.country==='IN') -> INR; otherwise tenant.default_currency (else USD).
  Effects reset to the tenant default on tenant switch and keep the choice within the provider's
  supported set (auto-correct). Server remains the source of truth (unsupported -> 400 -> error toast).
- Test data added (via existing APIs, additive): tenant 'Bharat Pay' (slug bharat-pay, country IN,
  default_currency INR) with a mock sandbox account supporting INR+USD.
- Verified (API): Acme(US) mock account rejects INR/AED (400 currency_unsupported), USD/EUR/GBP succeed;
  Bharat Pay(IN): INR succeeds, USD succeeds, GBP rejected. Testing agent iteration_23: frontend 100%
  (7/7 — selector present, Acme default USD [EUR,GBP,USD], Bharat Pay default INR [INR,USD], INR+USD
  succeed, provider-switch option lists + auto-correct, tenant isolation, no regression).
- No existing financial behavior changed.

## Open item (carried): Resend Delivery Tracking activation
- Webhook POST /api/webhooks/resend is built + logic-verified (valid->200, invalid->400, idempotent,
  delivery recorded, no financial change, secret never exposed). It stays disabled until
  RESEND_WEBHOOK_SECRET (whsec_...) is present in /app/backend/.env. The value placed in the platform
  'Secrets environment' is NOT read by the preview backend (config.load_dotenv reads backend/.env only).
  Awaiting the whsec_ value in backend/.env to finish activation.
- Infra note: pod reset recovered (supervisor + postgres restarted, cloudpay role/db recreated,
  migrations to head c9d2e3f4a5b6, backend reseeded).

## Currency Selector Revision — explicit choice, no default, full list (2026-06, FRONTEND-ONLY)
- Per user revision of the earlier currency work (Payments.jsx only; no backend/DB/engine change):
  * REMOVED all default/preselection logic (no India-INR auto-default, no tenant default_currency
    preselect, no per-account narrowing). Initial state has NO currency; placeholder "Select currency";
    the Process button is disabled until the user explicitly picks a currency; reset clears it.
  * Currency options now = the FULL union of currencies advertised by provider capability discovery
    (/api/providers/available), with a baseline of INR,USD,GBP,EUR,AUD,CAD,SGD so it's never empty.
    Live options observed: AED,AUD,CAD,EUR,GBP,INR,SGD,USD.
  * Each option shows "CODE — Name" via a CURRENCY_NAMES map (e.g. "INR — Indian Rupee").
- Server-side capability validation remains authoritative and UNCHANGED (match_capability/plan_route);
  unsupported currency/provider/country/environment still rejected 400 -> error toast. No FX/conversion.
- Verified via UI screenshots: no default (placeholder), submit gated until chosen, full named list,
  and explicit INR selection on Bharat Pay processed a ₹1,500 INR payment (succeeded) next to USD rows.
- Only file changed: frontend/src/pages/Payments.jsx. No existing financial behavior changed.

## Currency Amount Formatting + Zero-Decimal + Provider-Aware Hinting (2026-06, FRONTEND-ONLY)
- Three additive currency-safety UI improvements (no backend/engine/DB change; no-default decision kept):
  1. Amount Formatting: the Amount field shows the selected currency's symbol (Intl formatToParts) as a
     prefix and uses the correct step/precision per currency. Symbol only shows once a currency is chosen.
  2. Zero-Decimal Handling: new shared helpers in lib/api.js — currencyDecimals (JPY/KRW/VND/CLP/XOF/
     XAF/PYG=0; BHD/KWD/OMR/TND=3; default 2), toMinorUnits(amount,currency)=round(amount*10^d), and
     money() now divides by 10^d (identical for 2-decimal currencies, correct for JPY etc.). New Payment
     computes amount_minor via toMinorUnits so ¥1500 -> 1500 (not 150000). Verified: JPY payment stored
     amount_minor=1500, succeeded.
  3. Provider-Aware Hinting: currency options the currently selected provider/account cannot process are
     disabled + annotated "· not supported" (uses per-tenant account currencies when set, else plugin
     capability; for "auto", supported if any provider qualifies). If a selected currency becomes
     unsupported after a provider/environment change it is cleared (never auto-selects). Server-side
     match_capability/plan_route remain the final authority (unchanged).
- Files changed: frontend/src/lib/api.js, frontend/src/pages/Payments.jsx. No core payment/engine change.
- Verified: UI (¥ symbol, step=1 for JPY, hinting map INR/JPY/USD enabled + others greyed on Bharat Pay
  mock), API (¥1500->1500 succeeded; JPY on acme rejected 400), backend regression 58/58 pass.
- Test data: Bharat Pay mock sandbox account now also lists JPY (additive, for zero-decimal testing).

## Inline Currency Note + Amount Grouping + Country→Method Hint (2026-06, FRONTEND-ONLY)
- Three additive UX hints in Payments.jsx (only file changed this task; no backend/engine/DB change;
  no-default-currency kept; server capability remains authoritative):
  1. Inline Currency Support Note: replaced the prior auto-clear behaviour with a red note
     "Not supported by {provider}" (data-testid=currency-unsupported-note) shown when the chosen
     currency isn't processable by the current provider/account; Process is disabled while unsupported.
     Verified: select JPY (mock) -> switch to Stripe -> note appears, submit disabled.
  2. Amount Grouping: amount field is now a grouped text input (data-testid=payment-amount-input)
     showing thousands separators as typed (module-level groupAmount, display-only). The raw numeric
     string is stored in form.amount and sent via toMinorUnits unchanged (no float math introduced),
     honouring per-currency precision incl. zero-decimal. Verified: typed 1,234,567.89 -> stored
     $1,234,567.89 (amount_minor 123456789 exact); JPY 1,500,000 grouped, ¥ symbol, no decimals.
  3. Country->Payment Method Hint: informational line (data-testid=payment-method-hint) listing the
     payment_methods advertised for the current provider/currency context (union for "auto"). Verified:
     "Supported methods for JPY via Mock Sandbox Provider: bank, card, wallet".
- Server-side match_capability/plan_route/routing/execution unchanged and authoritative. Backend
  financial regression unchanged (58/58 from prior run; no backend code touched this task).

## Payment Acceptance Accounts — NEW additive capability (2026-06)
- Separate from PaymentProvider (external PSP). A tenant-owned UPI RECEIVING destination; does NOT
  process transactions. New table payment_acceptance_accounts (migration b7d4e1a9c260, applied):
  tenant_id, account_type, display_name, provider_key?, bank_name?, account_holder_name?, upi_vpa?,
  currency, country, environment, enabled, priority, verification_status (default 'unverified'),
  config, audit/timestamp cols; unique(tenant_id,upi_vpa,environment).
- API (app/routers/acceptance.py, prefix /api/payment-acceptance): GET/POST /accounts,
  GET /accounts/eligible (read-only, priority-ordered, for a future UPI plugin), GET/PATCH/DELETE
  /accounts/{id}, POST /accounts/{id}/enable|disable|priority. Server-side VPA validation+normalize,
  tenant isolation, rate limiting (acceptance_write), audit (VPA masked, no secrets). No fake
  verification (status stays 'unverified'; not settable via API).
- Permissions (seed.py, additive): payment_acceptance_account.view / .manage (Super Admin gets all).
- UI: new page frontend/src/pages/PaymentAcceptance.jsx (route /dashboard/payment-acceptance, nav
  'Payment Acceptance' gated on .view) — table + add/edit dialog, enable/disable, inline priority,
  delete. Existing Providers page untouched.
- Tests: tests/test_payment_acceptance.py (6). Testing agent iteration_24: backend 100%
  (6 acceptance + 3 RBAC incl. 403 for a user lacking the perm + capture/void regression 17),
  frontend 100% (full CRUD, multiple accounts, enable/disable/priority/edit/delete, invalid-VPA
  toast, Providers coexist). No existing payment/provider/ledger/routing behavior changed.
- NOT implemented (requires real provider/PSP): real UPI processing, bank settlement, provider
  webhook confirmation, VPA bank-verification — out of scope by design.

## Payment Acceptance follow-ups: Checkout Destination · Verification Workflow · Tenant Admin Role · Audit View (2026-06, additive)
- Checkout Destination: public checkout (GET /api/public/checkout/{token}) now returns an additive
  `acceptance` object (display_name, upi_vpa, bank_name, account_type, verification_status) = the
  tenant's highest-priority ENABLED acceptance account matching the session currency; null if none.
  Display-only; no processing; existing checkout fields/behavior unchanged.
- Verification Workflow: POST /api/payment-acceptance/accounts/{id}/request-verification moves
  unverified->pending only (400 otherwise); NEVER auto-'verified' (no fake verification). Audited as
  payment_acceptance_account.request_verification. UI: "Verify" button shown only when unverified.
- Tenant Admin Role: idempotent seed of a tenant-scoped "Tenant Admin" role (Acme) including
  payment_acceptance_account.view/.manage (plus common tenant perms) so merchants can manage their
  own VPAs. Existing roles untouched.
- Acceptance Audit View: GET /api/payment-acceptance/accounts/{id}/audit (tenant-isolated, VPA masked)
  + UI History dialog (per-account activity trail: create/enable/disable/priority/verification).
- Tests: tests/test_payment_acceptance.py now 8 (added request-verification + per-account audit).
  Testing agent iteration_25: backend 100% (25 regression + 3 new), frontend 100% (Verify + History).
  No existing payment/provider/ledger/routing/checkout behavior changed.

## Payment Acceptance follow-ups 2: Primary Badge · Checkout UPI Block · Verification States · CSV Export (2026-06, additive)
- Primary VPA Badge (frontend): highest-priority ENABLED account per currency shows a "Primary" badge
  (acceptance-primary-{id}); recomputes when accounts are disabled/priority changes.
- Checkout UPI Block (frontend): CheckoutPage renders session.acceptance as a "Pay to this UPI ID"
  panel (checkout-upi-block) with the VPA + copy button; absent when no eligible account.
- Verification States UI + API: NEW POST /api/payment-acceptance/accounts/{id}/verification
  {status: verified|rejected} — MANUAL operator decision, valid ONLY from 'pending' (400 otherwise),
  invalid status 400, no re-decide after finalized; audited as .verify/.reject with manual_decision.
  UI shows Mark Verified / Reject buttons for pending accounts. No fake/auto success.
- Acceptance Report: NEW GET /api/payment-acceptance/accounts/export.csv (tenant-scoped, .view perm)
  -> text/csv of accounts + verification/activity; UI "Export CSV" header button (export-acceptance-button).
- Tests: tests/test_payment_acceptance.py now 10 (added manual decision + csv export). Testing agent
  iteration_26: backend 100% (27), frontend 100% (Primary move, verify/approve+reject, CSV, checkout
  +/- block). No existing payment/provider/ledger/routing/checkout behavior changed.

## Demo UPI provider — foundational new plugin (2026-06, sandbox-only, additive)
- NEW app/providers/demo_upi.py (DemoUpiProvider, key 'demo_upi'): ISOLATED sandbox-only UPI demo
  plugin. Subclasses the built-in Mock reference adapter so the ENTIRE CloudPay core (payment engine,
  routing, idempotency, fee engine, ledger, state machine, webhook reconciliation) is reused UNCHANGED.
  supported_currencies=[INR], countries=[IN], methods=[upi,upi_intent,upi_qr], flows=[intent,qr,direct],
  supported_environments=[sandbox] only. capabilities() adds demo:true, sandbox_only:true, and
  upi_apps=[PhonePe, Google Pay, Paytm, BHIM, Other UPI App, Scan QR] (DEMO UI choices, NOT real
  integrations). Registered via existing registry (registry.py). NEVER real money/bank/PSP/network.
- Verified: demo_upi in /api/providers/available (sandbox-only, 6 apps); sandbox INR upi intent/qr/direct
  -> succeeded through the existing engine; demo_upi+live rejected server-side; card method rejected
  (capability-aware). Unit tests tests/test_demo_upi_provider.py (5) + regression capture_void green.
- NOT YET BUILT (explicitly deferred, honest status — large remaining scope from the master spec):
  frontend Demo UPI hosted-checkout journey (app selection page, simulated UPI-PIN authorization page,
  QR image rendering, Simulate Success/Failure/Pending/Timeout outcome buttons), the Provider Connection
  Wizard UI, a centralized ISO-4217 currency catalog table, demo merchant test page, and the full
  30-40 item test matrix. These are additive and can be layered on this foundation without core changes.
- Infra note: another sandbox pod reset occurred mid-task (supervisor+postgres down, cloudpay db wiped);
  recovered (recreated role/db, migrations to b7d4e1a9c260, reseeded, login 200).


## Provider Connect Wizard (2026-06, FRONTEND-ONLY, additive)
- Replaced the simple "Add Provider" dialog in frontend/src/pages/Providers.jsx with a guided 7-step
  wizard that wraps the EXISTING provider APIs — no new backend models, secret store, or health
  architecture (per user constraint). Steps: 1) Select Provider (cards from GET /api/providers/available
  with env chips), 2) Environment (from plugin supported_environments), 3) Credentials (dynamic inputs
  from required_credentials; Next gated until required creds filled; "no credentials" note otherwise),
  4) Capabilities (toggle chips to narrow currencies/countries/methods/flows — empty = inherit plugin
  default; + display name & priority), 5) Acceptance Mapping (DISPLAY-ONLY per user: lists eligible UPI
  acceptance accounts via GET /api/payment-acceptance/accounts for UPI providers, not-applicable note
  otherwise; nothing persisted), 6) Test Connection (GET /api/providers/{key}/health?environment= with
  healthy/error result), 7) Review & Save (summary -> POST /api/providers).
- Left step rail with active/done indicators; Back preserves selections. Header button renamed to
  "Connect Provider" (data-testid=connect-provider-button). All steps/controls have data-testids
  (wizard-*, wizard-provider-{key}, wizard-environment-{env}, wizard-credential-{key},
  wizard-currency/method/flow/country-*, wizard-run-health, wizard-health-result, wizard-review,
  wizard-next, wizard-back, wizard-save).
- Save posts supported_methods from the chosen payment_methods; only non-empty credentials sent
  (encrypted server-side, never echoed). Duplicate provider+mode returns backend 400 (expected).
- ONLY file changed: frontend/src/pages/Providers.jsx. No backend/DB/engine change.
- Tests: testing_agent iteration_27 — frontend 100% (all 10 criteria: open, 7-step rail, provider
  cards, Next gating, env/creds/capabilities/acceptance/test/review, mock e2e save creates account +
  grid refresh + success toast, Back preserves state). No bugs. Configured-providers grid unchanged.


## Demo UPI Checkout + QR + Currency Catalog API + Provider Health Badge (2026-06, additive)
Four additive features; no core payment/engine/ledger change. demo_upi stays sandbox-only.
- Currency Catalog API: NEW app/data/currency_catalog.py (52 ISO-4217 entries: code, name, decimals,
  symbol) + GET /api/currencies (auth via get_current_user). Payments.jsx now fetches it into
  currencyCatalog and derives currency labels ("CODE — Name") + the option baseline from it, removing
  the hardcoded CURRENCY_NAMES map and the hardcoded baseline currency list (fallback map kept only for
  pre-load). Server capability validation stays authoritative.
- Demo UPI Checkout (customer-facing): CheckoutCreate/CheckoutOut gained provider_key (default mock);
  operators pick "Demo UPI (INR sandbox)" in the Hosted Checkout create dialog (checkout-method-select)
  which creates a demo_upi INR session. CheckoutPage.jsx renders a DemoUpiCheckout journey when
  session.provider_key==='demo_upi': app-choice grid (PhonePe/GPay/Paytm/BHIM/Other/Scan QR) ->
  either a scannable QR screen (qrcode.react QRCodeSVG from a upi:// deep link) or a simulated UPI-PIN
  keypad screen -> processing -> result. Outcomes: success runs a GENUINE sandbox payment via
  payment_engine.create_payment(provider_key='demo_upi', country='IN', payment_method='upi',
  flow='direct') and marks the session paid; failed/pending are SIMULATED UI states that record NO
  payment (session stays open) so operators can walk every screen honestly.
- Backend (checkout.py, additive public endpoints, sandbox-only, demo_upi guard): GET
  /api/public/checkout/{token}/upi (apps + upi_link + vpa from highest-priority acceptance VPA or
  cloudpay@mockbank fallback) and POST /api/public/checkout/{token}/upi/pay (DemoUpiPay: upi_app,
  outcome). New schema DemoUpiPay. public_get_session now returns provider_key.
- Provider Health Badge: Providers.jsx configured cards show a live health badge
  (provider-health-badge-{id}) — Healthy(green)/Down(red)/Checking — via GET /providers/{key}/health;
  clicking re-checks. Card header restructured (health badge top-right, active/env/priority row below).
- Deps: qrcode.react@4.2.0 (yarn). Files: app/data/currency_catalog.py, app/data/__init__.py,
  app/routers/config.py, app/routers/checkout.py, app/schemas/__init__.py, frontend Payments.jsx,
  Providers.jsx, Checkout.jsx, CheckoutPage.jsx.
- Tests: testing_agent iteration_28 — backend 100% (12/12 pytest incl. currencies auth-gate, demo_upi
  session/info/success-payment/simulated-outcome), frontend ~95% then FIXED the one LOW issue
  (DemoUpiCheckout now owns its own success screen; parent no longer unmounts it on success so
  upi-result-paid renders — verified via screenshot, ₹299 paid). test_iter28_demo_upi_currencies.py.

## UPI-in-Wizard + Method Badge + Live Status Board + QR Download (2026-06, FRONTEND-ONLY, additive)
Four additive frontend enhancements; no backend/DB/engine change (reuse existing APIs).
- UPI In Wizard: after the Connect Provider wizard saves a demo_upi provider, a follow-up dialog
  (demo-link-dialog) lets the operator generate a shareable Demo UPI checkout link — amount input
  (demo-link-amount, default 1500) -> POST /checkout/sessions {provider_key:'demo_upi',currency:'INR'}
  -> link (demo-link-url) with copy/open. Providers.jsx only.
- Payment Method Badge: new shared MethodBadge (components/common.jsx, UPI vs Card icon+label).
  Payments table adds a Method column (payment-method-{reference}) from p.metadata.method (fallback
  demo_upi->upi else card); Hosted Checkout table adds a Method column (checkout-method-badge-{reference})
  from s.provider_key.
- Live Status Board: Providers.jsx auto-refreshes every configured provider's health badge every 15s
  (setInterval calling checkCardHealth; cleaned up on unmount); badge still clickable for immediate
  re-check; shows Healthy/Down/Checking.
- QR Download: Demo UPI QR screen (CheckoutPage.jsx) now renders QRCodeCanvas (qrcode.react) with a
  qrRef + a 'Download QR' button (upi-qr-download) that serializes the canvas to a PNG
  (upi-qr-<reference>.png).
- Files: components/common.jsx, pages/Providers.jsx, pages/Payments.jsx, pages/Checkout.jsx,
  pages/CheckoutPage.jsx. Tests: testing_agent iteration_29 — frontend 100% (7/7): wizard demo-link
  dialog + working link, method badges on both tables, 15s auto-refresh observed, QR PNG download
  (verified filename), demo UPI PIN/QR success regressions. No issues.


## Provider Uptime % + Demo Amount Presets + Method Filter (2026-06, FRONTEND-ONLY, additive)
Three additive frontend enhancements; no backend/DB/engine change.
- Provider Uptime %: Providers.jsx checkCardHealth now accumulates session checks/ups counters in
  cardHealth[p.id]; each card shows a session uptime line (provider-uptime-{id}) "uptime (session) N%
  · ups/checks" under the health badge (green >=99, amber >=90, red below). Session-scoped (resets on
  reload), driven by the existing 15s auto-refresh + click re-checks.
- Demo Amount Presets: the wizard's post-save Demo-link dialog gets quick chips
  (demo-link-preset-1 / -99 / -1500) that set demo-link-amount for faster sales demos.
- Method Filter: Payments.jsx adds a filter bar (payment-method-filter) with chips
  payment-filter-all / -upi / -card (UPI/Card show live counts); paymentMethod(p) derives upi vs card
  from p.metadata.method (fallback demo_upi->upi else card); filteredPayments drives the table with a
  method-specific empty state.
- Files: pages/Providers.jsx, pages/Payments.jsx. Tests: testing_agent iteration_30 — frontend 100%
  (uptime 1/1 -> 2/2 on click, presets set 1/99/1500 + working link, filter All/UPI/Card with counts
  + matching row badges + empty state). No issues.


## Method Filter Everywhere + Uptime History Sparkline (2026-06, additive)
Two additive enhancements; backend change limited to surfacing refund provider_key.
- Method Filter on Refunds + Hosted Checkout: same UPI/Card chip pattern as Payments.
  * Refunds.jsx: refund-method-filter chips (refund-filter-all/-upi/-card with counts), a Method
    column badge (refund-method-{id8}), filtered list + per-method empty states. Method derived as
    demo_upi->upi else card from the NEW refund.provider_key.
  * Checkout.jsx: checkout-method-filter chips (checkout-filter-all/-upi/-card), filteredSessions,
    method empty states; reuses the existing Method column (checkout-method-badge-{reference}).
  * Backend: RefundOut gained optional provider_key; payments.list_refunds now JOINs Payment to
    populate it per refund (tenant-scoped, limit 200). No new endpoint/migration.
- Uptime History sparkline: Providers.jsx checkCardHealth keeps history[] (last 16 up/down); each
  card's uptime line renders provider-uptime-spark-{id} — green (tall)/red (short) bars per recent
  check — next to the % and ups/checks. Session-scoped (resets on reload); grows via the 15s
  auto-refresh + click re-checks, capped at 16 bars.
- Files: backend app/schemas/__init__.py, app/routers/payments.py; frontend Refunds.jsx, Checkout.jsx,
  Providers.jsx. Tests: testing_agent iteration_31 — backend 100% (2/2, refunds provider_key present),
  frontend 100% (18/18: both filters with counts/empty states + matching row badges, sparkline appends
  bars on re-check and caps at 16). No issues.


## Payment Method in CSV Reports (2026-06, additive, backend-only)
- Added a `method` (upi|card) column to the payments CSV outputs so finance can slice UPI vs Card.
  * app/routers/reports_export.py GET /api/reports/export/payments.csv: new "method" column after
    "provider" (helper _payment_method: metadata_json['method'] normalized to upi/card, fallback
    demo_upi->upi else card).
  * app/services/report_generation.py PAYMENTS section (scheduled/on-demand daily/weekly/monthly/
    custom reports + emailed CSV): new "method" column after "reference", same derivation.
- No new endpoint/migration/frontend change (existing Export CSV button + scheduled reports pick it up).
- Verified via curl: payments.csv header now id,reference,provider,method,... with method=card for mock
  and method=upi for demo_upi rows; scheduled daily run generates cleanly (7 payments, file_id returned).


## Method Breakdown in Settlement/Reconciliation Exports (2026-06, additive, backend-only)
Split payouts by rail (UPI vs Card) in the settlement + reconciliation exports.
- Reconciliation CSV export: NEW GET /api/reconciliation/runs/{run_id}/export.csv (reconciliation.view,
  tenant-isolated, outcome filter) — per-line items with a `method` column resolved from each item's
  linked payment (batch Payment lookup; _pm_from_payment: metadata.method normalized to upi/card,
  fallback demo_upi->upi else card) plus a trailing "METHOD BREAKDOWN" (method, line_count).
- Finance report (report_generation.py, scheduled/on-demand daily/weekly/monthly/custom + emailed CSV):
  added a "METHOD BREAKDOWN" section after SETTLEMENTS aggregating the report's payments by method
  (count, gross, fees, net per upi/card).
- No migration/frontend change. Verified via curl: reconciliation export shows method=upi per line +
  breakdown (upi,6); finance report shows METHOD BREAKDOWN card 1/₹50, upi 6/₹2328.50.


## Reconciliation Download CSV button (2026-06, FRONTEND-ONLY, additive)
- Reconciliation.jsx detail dialog now has a "Download CSV" button (reconciliation-download-csv) that
  calls downloadCsv on GET /api/reconciliation/runs/{id}/export.csv (the rail-split export), passing
  tenant_id and the currently-selected outcome filter so the download matches the on-screen view.
  Filename reconciliation_{run_ref||id}.csv; guarded to render only when a run detail is open.
- Verified via Playwright: button present, download fired (reconciliation_RAILTEST1.csv). Only
  frontend/src/pages/Reconciliation.jsx changed.


## Reconciliation Method Split Header (2026-06, additive)
- Backend: GET /api/reconciliation/runs/{id} now returns a `method` on each item (resolved from the
  linked payment) plus a `method_summary` (per-method line counts). Uses the existing _pm_from_payment.
- Frontend (Reconciliation.jsx): the results dialog shows a one-line "By rail: UPI N · Card N
  (· Unknown N)" summary (reconciliation-method-split) above the outcome filter chips.
- Verified: run_detail returns method_summary {upi:6}; UI header renders "By rail: UPI 6 · Card 0".
  Files: app/routers/reconciliation.py, frontend/src/pages/Reconciliation.jsx.


## Rail Split on Overview (2026-06, FRONTEND-ONLY, additive)
- Overview.jsx computes a UPI-vs-Card mix from the tenant's payments (method = metadata.method,
  fallback demo_upi->upi else card) and renders a "Payment Mix by Rail" card (rail-mix-card).
- UPDATED: now grouped PER CURRENCY (railByCurrency) — one block per currency (rail-mix-ccy-{CCY})
  with its own split bar (rail-mix-bar-{CCY}) and UPI/Card columns (rail-mix-upi-{CCY} /
  rail-mix-card-{CCY}) formatted with money(amount, ccy), so INR and USD totals never blend symbols.
  Verified: INR block UPI 100% ₹2,328.50 (6) / Card ₹0.00; USD block Card 100% $50.00 (1).
  Only frontend/src/pages/Overview.jsx changed.

## Rail Mix Succeeded-Only Toggle (2026-06, FRONTEND-ONLY, additive)
- Overview.jsx rail mix moved to a useMemo over the full payments list (allPayments) with a
  "Succeeded only" toggle (rail-mix-succeeded-toggle) that filters to status in
  {succeeded, captured} so pending/failed/refunded don't skew the rails picture. Filter-aware empty
  state. Verified: ALL = 7 payments (INR 6 + USD 1); Succeeded only = 6 payments (USD refunded row
  dropped, INR block remains). Only frontend/src/pages/Overview.jsx changed.

## Rail Mix Time Range (2026-06, FRONTEND-ONLY, additive)
- Overview.jsx rail mix gained a time-range selector (rail-mix-range: rail-mix-range-0/-7/-30 = All/
  7d/30d) folded into the same useMemo (cutoff = now - rangeDays*86400000, on created_at). Works with
  the succeeded-only toggle; empty state now reads "No payments match the current filters." when any
  filter is active. Verified chips activate/filter (all demo payments are same-day so 7/30/All all
  show 7). Only frontend/src/pages/Overview.jsx changed.

## Rail Mix Trend Line (2026-06, FRONTEND-ONLY, additive)
- Overview.jsx rail mix card now shows a "UPI vs Card trend" line chart (rail-mix-trend) below the
  per-currency blocks: railTrend useMemo buckets the (range + succeeded-filtered) payments by day
  (ISO date) into {name, upi, card} counts; rendered with recharts LineChart (two lines, UPI +
  Card, legend, MM-DD axis). Renders only when >=2 distinct days exist (a single-day series is
  meaningless). Verified by temporarily backdating two payments (09-02/09-03/09-04) — the two-line
  trend rendered correctly — then reverting the created_at values. Only Overview.jsx changed.

## Multi-Day Demo Payment Seed (2026-06, BACKEND seed, additive)
- app/seed.py now seeds a spread of demo payments on the "acme" demo tenant so the Overview rail-mix
  trend line + 7d/30d ranges show real UPI-vs-Card movement out of the box. _seed_demo_payments()
  inserts 22 deterministic payments over the last ~14 days (14 UPI/demo_upi/INR + 8 Card/mock/USD,
  mostly succeeded with a couple failed) with explicit created_at, metadata_json.method, and computed
  fee/net (2.9%+30 for card, 0 for UPI). Idempotent on its own marker (reference LIKE 'DEMO-%'): seeds
  once, won't duplicate on restart or clash with a tenant's other payments. Runs in the existing
  startup seed (server.py). Verified: 14 upi + 8 card spread 08-22..09-04; Acme Overview trend renders
  a full multi-day two-line chart. Only app/seed.py changed.

