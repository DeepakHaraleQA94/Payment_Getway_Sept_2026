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

## Implemented (2026-06, PROMPT 05 — Stripe TEST/Sandbox adapter)
- Stripe onboarded as an ISOLATED provider adapter (`app/providers/stripe_provider.py`),
  resolved via the registry by `provider_key` — never hard-coded into the payment engine.
- Registered ONLY in TEST mode: a live key (`sk_live_`) is never registered, so real-money
  charges can never be dispatched. Provider discovery + monitoring surface Stripe with
  `mode=sandbox`, `test_mode=true`.
- Idempotency lock claimed (payment row unique constraint on tenant_id+idempotency_key)
  BEFORE dispatching the external charge; the same idempotency_key is forwarded to Stripe.
- Inbound Stripe webhook `POST /api/webhooks/stripe` (public): verifies signature when
  `STRIPE_WEBHOOK_SECRET` is set (skips + parses JSON otherwise), reconciles payment status
  idempotently via the state machine (`payment_intent.succeeded/payment_failed/canceled`),
  audited; never posts ledger (sync charge flow owns financial mutations).
- Graceful degradation: with the env's placeholder `sk_test_` key, real Stripe calls fail
  and the payment resolves to `failed` (no ledger credit) — safe by design.
- Tests: 95 backend tests pass serially (`pytest tests/ -n0`); added test_stripe_provider.py
  (11) + test_prompt05_regressions.py (9).
- NOTE: `STRIPE_API_KEY` in `.env` is a placeholder ("sk_test_emergent"); replace with a real
  Stripe TEST secret key to process actual sandbox charges. `STRIPE_WEBHOOK_SECRET` is empty.

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
