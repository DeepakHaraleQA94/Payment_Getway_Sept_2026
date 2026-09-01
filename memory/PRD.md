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
