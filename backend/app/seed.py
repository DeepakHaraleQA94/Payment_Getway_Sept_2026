"""Idempotent seeding: permissions, roles, platform admin, and a demo tenant."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.feature import FeatureFlag
from app.models.finance import FeeRule
from app.models.iam import Permission, Role, User
from app.models.payment import PaymentProvider
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
    ("refund.create", "Create refunds", "refunds"),
    ("utr.submit", "Submit bank UTR references", "payments"),
    ("utr.verify", "Confirm/reject bank UTR references", "payments"),
    ("settlement.manage", "Generate settlements", "settlements"),
    ("audit.view", "View audit logs", "audit"),
    ("config.manage", "Manage system configuration", "config"),
    ("apikey.manage", "Manage API keys", "api_keys"),
    ("webhook.manage", "Manage webhooks", "webhooks"),
    ("checkout.manage", "Manage checkout sessions", "checkout"),
    ("report.manage", "Generate and manage reports", "reports"),
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

    # Platform admin user
    res = await db.execute(select(User).where(User.email == settings.admin_email))
    admin = res.scalar_one_or_none()
    if admin is None:
        admin = User(email=settings.admin_email, name="Platform Admin",
                     password_hash=hash_password(settings.admin_password), tenant_id=platform.id,
                     role_id=super_role.id, is_superadmin=True, auth_provider="password", status="active",
                     email_verified=True)
        db.add(admin)
    else:
        if not admin.password_hash or not verify_password(settings.admin_password, admin.password_hash):
            admin.password_hash = hash_password(settings.admin_password)
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
    if ops_admin is None:
        ops_admin = User(email="ops-admin@cloudpay.io", name="Ops Admin",
                         password_hash=hash_password(settings.admin_password), tenant_id=platform.id,
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

    # FX reference rates (mock)
    res = await db.execute(select(FxRate).limit(1))
    if res.scalar_one_or_none() is None:
        now = datetime.now(timezone.utc)
        for base, quote, rate in [("USD", "EUR", 0.92), ("USD", "GBP", 0.79), ("EUR", "USD", 1.09)]:
            db.add(FxRate(base_currency=base, quote_currency=quote, rate=rate, source="mock", as_of=now))

    await db.commit()
