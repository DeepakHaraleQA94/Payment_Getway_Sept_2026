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
