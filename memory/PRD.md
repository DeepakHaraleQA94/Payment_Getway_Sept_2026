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
