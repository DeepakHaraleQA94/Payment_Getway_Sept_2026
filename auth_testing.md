# CloudPay Auth Testing Notes

## Accounts
- Super admin (JWT): admin@cloudpay.io / Admin@12345
- Google login: Emergent-managed OAuth via "Continue with Google" button.

## Cookie behavior
- Login/register set httpOnly cookies: access_token, refresh_token (Secure, SameSite=None).
- Over HTTPS preview URL, cookies work in the browser and are sent automatically (axios withCredentials=true).
- Over http://localhost:8001, Secure cookies are NOT stored by clients — authenticate with
  `Authorization: Bearer <access_token>` where the token is read from the login response Set-Cookie header.

## Quick API check (localhost, Bearer flow)
1. POST /api/auth/login {"email":"admin@cloudpay.io","password":"Admin@12345"} -> read access_token from Set-Cookie
2. GET  /api/auth/me with header Authorization: Bearer <access_token> -> returns user with permissions ["*"]
3. GET  /api/tenants -> list; find slug "acme" tenant_id
4. POST /api/payments?tenant_id=<acme> {"reference":"T","amount_minor":10000,"currency":"USD","provider_key":"mock"} -> succeeded, fee 320

## Browser check
- Go to /login, sign in with the super admin, expect redirect to /dashboard with KPI cards.
- Tenant selector in topbar switches active tenant (default = Acme Commerce).
