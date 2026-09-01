# CloudPay Auth Testing Notes

## Accounts
- Super admin (JWT email/password): the seeded platform admin.
  Credentials are NOT stored in the repository. They come from environment variables
  (`ADMIN_EMAIL` / `ADMIN_PASSWORD`) and, for local testing, are recorded only in the
  git-ignored file `memory/test_credentials.md`.
- Google login: Emergent-managed OAuth via the "Continue with Google" button.

## Cookie behavior
- Login/register set httpOnly cookies: access_token, refresh_token (Secure, SameSite=None).
- Over the HTTPS preview URL, cookies work in the browser and are sent automatically
  (axios withCredentials=true).
- Over http://localhost:8001, Secure cookies are NOT stored by clients — authenticate with
  `Authorization: Bearer <access_token>` where the token is read from the login response
  Set-Cookie header.

## Quick API check (localhost, Bearer flow)
1. POST /api/auth/login with the admin email/password from your environment
   -> read access_token from Set-Cookie
2. GET  /api/auth/me with header `Authorization: Bearer <access_token>`
   -> returns the user with permissions ["*"]
3. GET  /api/tenants -> list; find the "acme" tenant_id
4. POST /api/payments?tenant_id=<acme> {"reference":"T","amount_minor":10000,"currency":"USD","provider_key":"mock"}
   -> succeeded

## Browser check
- Go to /login, sign in with the super admin, expect redirect to /dashboard with KPI cards.
- Tenant selector in the topbar switches the active tenant (default = Acme Commerce).

## Security note
- Never commit real credentials, API keys, tokens or secrets to tracked files.
- `.env`, `*.key`, `credentials.json` and `memory/test_credentials.md` are git-ignored.
