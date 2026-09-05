"""Idempotent seeding: permissions, roles, platform admin, and a demo tenant."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.feature import FeatureFlag
from app.models.finance import FeeRule
from app.models.iam import Permission, Role, User
from app.models.payment import Payment, PaymentProvider
from app.models.platform import FxRate
from app.models.tenant import Tenant

PERMISSIONS = [
    ("tenant.manage", "Create/modify tenants", "tenants"),
    ("role.manage", "Manage roles & permissions", "iam"),
    ("user.manage", "Manage users", "iam"),
    ("feature.manage", "Manage feature flags", "features"),
    ("provider.manage", "Configure payment providers", "providers"),
    ("fee.manage", "Manage fee rules", "fees"),
    ("payment.create", "Create payments", "payments"),
    ("payment.reverse", "Reverse payments", "payments"),
    ("payment.capture", "Capture authorized payments", "payments"),
    ("payment.void", "Void authorized payments", "payments"),
    ("refund.create", "Create refunds", "refunds"),
    ("utr.submit", "Submit bank UTR references", "payments"),
    ("utr.verify", "Confirm/reject bank UTR references", "payments"),
    ("settlement.manage", "Generate settlements", "settlements"),
    ("reconciliation.run", "Run reconciliation", "reconciliation"),
    ("reconciliation.view", "View reconciliation runs", "reconciliation"),
    ("audit.view", "View audit logs", "audit"),
    ("config.manage", "Manage system configuration", "config"),
    ("apikey.manage", "Manage API keys", "api_keys"),
    ("webhook.manage", "Manage webhooks", "webhooks"),
    ("checkout.manage", "Manage checkout sessions", "checkout"),
    ("report.manage", "Generate and manage reports", "reports"),
    ("payment_acceptance_account.view", "View payment acceptance accounts", "payment_acceptance"),
    ("payment_acceptance_account.manage", "Manage payment acceptance accounts", "payment_acceptance"),
]


async def seed(db: AsyncSession) -> None:
    # Permissions
    res = await db.execute(select(Permission))
    existing = {p.code: p for p in res.scalars().all()}
    for code, desc, module in PERMISSIONS:
        if code not in existing:
            db.add(Permission(code=code, description=desc, module=module))
    await db.flush()

    # Platform tenant
    res = await db.execute(select(Tenant).where(Tenant.slug == "platform"))
    platform = res.scalar_one_or_none()
    if platform is None:
        platform = Tenant(name="CloudPay Platform", slug="platform", status="active",
                          default_currency="USD", is_platform=True)
        db.add(platform)
        await db.flush()

    # Superadmin role (all permissions)
    res = await db.execute(select(Role).where(Role.name == "Super Admin", Role.is_system.is_(True)))
    super_role = res.scalar_one_or_none()
    res = await db.execute(select(Permission))
    all_perms = list(res.scalars().all())
    if super_role is None:
        super_role = Role(name="Super Admin", description="Full platform access", is_system=True,
                          tenant_id=None, permissions=all_perms)
        db.add(super_role)
        await db.flush()
    else:
        super_role.permissions = all_perms

    # Platform admin user. The canonical Super Admin email (settings.admin_email) is preserved.
    # An empty/missing ADMIN_PASSWORD must NEVER create or overwrite a password (prevents blanking a
    # valid credential when the runtime secret is absent).
    admin_pw = (settings.admin_password or "").strip()
    res = await db.execute(select(User).where(User.email == settings.admin_email))
    admin = res.scalar_one_or_none()
    if admin is None:
        if not admin_pw:
            raise RuntimeError(
                "ADMIN_PASSWORD must be set to seed the initial Super Admin (refusing to create a "
                "blank-password account).")
        admin = User(email=settings.admin_email, name="Platform Admin",
                     password_hash=hash_password(admin_pw), tenant_id=platform.id,
                     role_id=super_role.id, is_superadmin=True, auth_provider="password", status="active",
                     email_verified=True)
        db.add(admin)
    else:
        # Only (re)set the password when an explicit non-empty ADMIN_PASSWORD is supplied AND it
        # differs from the stored one. Never overwrite a valid existing password with a blank.
        if admin_pw and (not admin.password_hash or not verify_password(admin_pw, admin.password_hash)):
            admin.password_hash = hash_password(admin_pw)
        admin.is_superadmin = True
        admin.role_id = super_role.id
        admin.email_verified = True
    await db.flush()

    # Platform "Operations" role (limited, explicit permissions) for Level-2 Platform Admins
    res = await db.execute(select(Role).where(Role.name == "Platform Operations", Role.tenant_id.is_(None)))
    ops_role = res.scalar_one_or_none()
    ops_perms_codes = {"tenant.manage", "audit.view", "report.manage"}
    ops_perms = [p for p in all_perms if p.code in ops_perms_codes]
    if ops_role is None:
        ops_role = Role(name="Platform Operations", description="Limited platform admin (tenants, audit, reports)",
                        is_system=False, tenant_id=None, permissions=ops_perms)
        db.add(ops_role)
        await db.flush()

    # Demo Platform Admin (Level 2): on platform tenant, NOT a super admin, only granted perms.
    res = await db.execute(select(User).where(User.email == "ops-admin@cloudpay.io"))
    ops_admin = res.scalar_one_or_none()
    if ops_admin is None and admin_pw:
        ops_admin = User(email="ops-admin@cloudpay.io", name="Ops Admin",
                         password_hash=hash_password(admin_pw), tenant_id=platform.id,
                         role_id=ops_role.id, is_superadmin=False, auth_provider="password",
                         status="active", email_verified=True)
        db.add(ops_admin)
        await db.flush()

    # Demo tenant with sample config
    res = await db.execute(select(Tenant).where(Tenant.slug == "acme"))
    demo = res.scalar_one_or_none()
    if demo is None:
        demo = Tenant(name="Acme Commerce", slug="acme", status="active", country="US",
                      default_currency="USD", contact_email="ops@acme.test")
        db.add(demo)
        await db.flush()
        db.add(PaymentProvider(tenant_id=demo.id, provider_key="mock", display_name="Mock Sandbox Provider",
                               mode="sandbox", enabled=True, priority=10,
                               supported_currencies=["USD", "EUR", "GBP"]))
        db.add(FeeRule(tenant_id=demo.id, name="Standard Card Fee", provider_key="mock", currency="USD",
                       percent_bps=290, fixed_minor=30, min_fee_minor=0, active=True, priority=10))
        db.add(FeatureFlag(tenant_id=demo.id, key="refunds", name="Refunds", enabled=True,
                           description="Allow refunds for this tenant"))
        # Core customer-facing features, enabled by default (Super Admin can toggle per tenant).
        for key, name in (("checkout", "Checkout"), ("reports", "Reports"), ("webhooks", "Webhooks"),
                          ("api_keys", "API Keys"), ("providers", "Providers")):
            db.add(FeatureFlag(tenant_id=demo.id, key=key, name=name, enabled=True,
                               description=f"{name} capability for this tenant"))
        db.add(FeatureFlag(tenant_id=demo.id, key="kyc_aml", name="KYC / AML", enabled=False,
                           description="Regulated capability, disabled until provider configured"))
        db.add(FeatureFlag(tenant_id=demo.id, key="vda_settlement", name="VDA Settlement", enabled=False,
                           description="Digital-asset boundary, disabled by default"))

    # Tenant Admin role (tenant-scoped) so merchants can manage their own config, including the
    # NEW payment acceptance accounts. Idempotent; kept in sync with the intended permission set.
    res = await db.execute(select(Tenant).where(Tenant.slug == "acme"))
    acme = res.scalar_one_or_none()
    if acme is not None:
        tenant_admin_codes = {
            "user.manage", "provider.manage", "fee.manage", "payment.create", "refund.create",
            "apikey.manage", "webhook.manage", "checkout.manage", "report.manage",
            "payment_acceptance_account.view", "payment_acceptance_account.manage",
        }
        ta_perms = [p for p in all_perms if p.code in tenant_admin_codes]
        res = await db.execute(select(Role).where(Role.name == "Tenant Admin", Role.tenant_id == acme.id))
        ta_role = res.scalar_one_or_none()
        if ta_role is None:
            ta_role = Role(name="Tenant Admin", description="Manage this tenant's configuration",
                           is_system=False, tenant_id=acme.id, permissions=ta_perms)
            db.add(ta_role)
        else:
            # Ensure the acceptance permissions are present on the existing role (additive).
            have = {p.code for p in ta_role.permissions}
            for p in ta_perms:
                if p.code not in have:
                    ta_role.permissions.append(p)
        await db.flush()

    # FX reference rates (mock)
    res = await db.execute(select(FxRate).limit(1))
    if res.scalar_one_or_none() is None:
        now = datetime.now(timezone.utc)
        for base, quote, rate in [("USD", "EUR", 0.92), ("USD", "GBP", 0.79), ("EUR", "USD", 1.09)]:
            db.add(FxRate(base_currency=base, quote_currency=quote, rate=rate, source="mock", as_of=now))

    # Demo payments spread across the last ~14 days so the Overview rail-mix trend line and the
    # 7d/30d ranges show real UPI-vs-Card movement out of the box. Idempotent on its own marker:
    # only seeds when the demo-seed batch is absent (won't duplicate on restart or clash with
    # other payments the tenant may already have).
    if acme is not None:
        existing = await db.execute(
            select(Payment.id).where(Payment.tenant_id == acme.id,
                                     Payment.reference.like("DEMO-%")).limit(1))
        if existing.scalar_one_or_none() is None:
            _seed_demo_payments(db, acme.id)

    await db.commit()


def _seed_demo_payments(db: AsyncSession, tenant_id) -> None:
    """Deterministic demo payments over the last 14 days, mixing UPI (INR) and Card (USD) rails."""
    now = datetime.now(timezone.utc)
    # (days_ago, rail, currency, amount_minor, status)
    plan = [
        (13, "card", "USD", 4500, "succeeded"), (13, "upi", "INR", 29900, "succeeded"),
        (12, "upi", "INR", 15000, "succeeded"), (11, "card", "USD", 9900, "failed"),
        (10, "upi", "INR", 49900, "succeeded"), (10, "card", "USD", 12000, "succeeded"),
        (9, "upi", "INR", 9900, "succeeded"), (8, "upi", "INR", 199900, "succeeded"),
        (7, "card", "USD", 7500, "succeeded"), (6, "upi", "INR", 25000, "succeeded"),
        (6, "upi", "INR", 5000, "failed"), (5, "card", "USD", 15000, "succeeded"),
        (5, "upi", "INR", 89900, "succeeded"), (4, "upi", "INR", 12500, "succeeded"),
        (3, "card", "USD", 25000, "succeeded"), (3, "upi", "INR", 149900, "succeeded"),
        (2, "upi", "INR", 34900, "succeeded"), (2, "upi", "INR", 9900, "succeeded"),
        (1, "card", "USD", 5000, "succeeded"), (1, "upi", "INR", 74900, "succeeded"),
        (0, "upi", "INR", 19900, "succeeded"), (0, "card", "USD", 8900, "succeeded"),
    ]
    for i, (days_ago, rail, currency, amount, status) in enumerate(plan):
        is_upi = rail == "upi"
        provider_key = "demo_upi" if is_upi else "mock"
        fee = 0 if is_upi else round(amount * 0.029) + 30
        net = amount - fee if status in ("succeeded", "captured") else 0
        ts = now - timedelta(days=days_ago, hours=(i % 12))
        db.add(Payment(
            tenant_id=tenant_id, reference=f"DEMO-{i:03d}", provider_key=provider_key,
            provider_txn_id=(f"demo_{provider_key}_{i:03d}" if status == "succeeded" else None),
            environment="sandbox", amount_minor=amount, currency=currency,
            fee_minor=(fee if status == "succeeded" else 0), net_minor=net, status=status,
            description=f"Demo {rail.upper()} payment", customer_email="demo.customer@acme.test",
            risk_score=(10 if status == "succeeded" else 55),
            metadata_json={"method": ("upi" if is_upi else "card"), "source": "demo_seed"},
            created_at=ts, updated_at=ts,
        ))
