"""Priority-based provider routing + failover — provider-agnostic.

Given a tenant + environment, returns the enabled, plugin-registered, environment-supported,
HEALTHY provider accounts ordered by priority (lower number = higher priority). The payment
engine attempts them in order and fails over to the next on failure. Treats every plugin
(Mock, example external PSP, or a future real provider) identically through the generic contract.
"""
from sqlalchemy import select

from app.models.payment import PaymentProvider
from app.providers.registry import get_provider, has_provider


async def candidate_accounts(db, tenant_id, environment: str) -> list[PaymentProvider]:
    res = await db.execute(
        select(PaymentProvider)
        .where(PaymentProvider.tenant_id == tenant_id,
               PaymentProvider.mode == environment,
               PaymentProvider.enabled.is_(True))
        .order_by(PaymentProvider.priority.asc(), PaymentProvider.created_at.asc()))
    healthy: list[PaymentProvider] = []
    for account in res.scalars().all():
        if not has_provider(account.provider_key):
            continue
        plugin = get_provider(account.provider_key)
        if not plugin.supports_environment(environment):
            continue
        if plugin.health_check(environment).get("status") != "up":
            continue
        healthy.append(account)
    return healthy
