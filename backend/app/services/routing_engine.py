"""Capability-aware, priority-based provider routing + failover — provider-agnostic.

Candidate selection considers, in order:
  tenant -> country/region -> currency -> payment method -> requested flow -> environment
  -> enabled state -> provider capability -> health -> priority.

The core treats every plugin (Mock or a future real provider for India / Sri Lanka / UK / USA /
elsewhere) identically through the generic contract. No provider or country is hard-coded.

Effective capability = the account's configured lists when non-empty, otherwise the plugin's
declared defaults. An empty effective list means "unrestricted" for that dimension. A full
decision trace is produced for every attempt and contains NO secrets.
"""
from sqlalchemy import select

from app.models.payment import PaymentProvider
from app.providers.registry import get_provider, has_provider


def _norm(values, upper=False) -> list[str]:
    out = []
    for v in (values or []):
        s = str(v).strip()
        out.append(s.upper() if upper else s.lower())
    return out


def _eff_currencies(account, plugin) -> list[str]:
    acct = account.supported_currencies if account is not None else None
    return _norm(acct or plugin.supported_currencies, upper=True)


def _eff_countries(account, plugin) -> list[str]:
    acct = getattr(account, "supported_countries", None) if account is not None else None
    return _norm(acct or getattr(plugin, "supported_countries", []), upper=True)


def _eff_methods(account, plugin) -> list[str]:
    acct = getattr(account, "supported_methods", None) if account is not None else None
    return _norm(acct or plugin.payment_methods)


def _eff_flows(account, plugin) -> list[str]:
    acct = getattr(account, "supported_flows", None) if account is not None else None
    plugin_flows = [f.value for f in plugin.supported_flows]
    return _norm(acct or plugin_flows)


def _match(*, currencies, countries, methods, flows, plugin, environment,
           currency, payment_method, flow, country) -> tuple[bool, str]:
    """Shared capability match returning (ok, reason). Empty list on a dimension = unrestricted."""
    if not plugin.supports_environment(environment):
        return False, "plugin_unsupported_environment"
    if country and countries and country.upper() not in countries:
        return False, "country_unsupported"
    if currency and currencies and currency.upper() not in currencies:
        return False, "currency_unsupported"
    if payment_method and methods and payment_method.lower() not in methods:
        return False, "method_unsupported"
    if flow and flows and flow.lower() not in flows:
        return False, "flow_unsupported"
    return True, "ok"


def match_capability(account, plugin, *, environment, currency=None, payment_method=None,
                     flow=None, country=None) -> tuple[bool, str]:
    """Capability match for a configured provider account."""
    if account.mode != environment:
        return False, "environment_mismatch"
    if not account.enabled:
        return False, "disabled"
    return _match(
        currencies=_eff_currencies(account, plugin), countries=_eff_countries(account, plugin),
        methods=_eff_methods(account, plugin), flows=_eff_flows(account, plugin),
        plugin=plugin, environment=environment, currency=currency,
        payment_method=payment_method, flow=flow, country=country)


def match_plugin_capability(plugin, *, environment, currency=None, payment_method=None,
                            flow=None, country=None) -> tuple[bool, str]:
    """Capability match against plugin defaults only (used for a sandbox call with no account row)."""
    return _match(
        currencies=_eff_currencies(None, plugin), countries=_eff_countries(None, plugin),
        methods=_eff_methods(None, plugin), flows=_eff_flows(None, plugin),
        plugin=plugin, environment=environment, currency=currency,
        payment_method=payment_method, flow=flow, country=country)


async def plan_route(db, tenant_id, *, environment, currency=None, payment_method=None,
                     flow=None, country=None) -> tuple[list[PaymentProvider], list[dict]]:
    """Return (ordered eligible accounts, decision trace). Priority ascending (lower = higher).

    An account is eligible when its plugin is registered, it matches every requested capability
    dimension, and its health check is up. The trace records each considered account with the
    reason it was selected/skipped — never any secret/credential.
    """
    res = await db.execute(
        select(PaymentProvider)
        .where(PaymentProvider.tenant_id == tenant_id, PaymentProvider.mode == environment)
        .order_by(PaymentProvider.priority.asc(), PaymentProvider.created_at.asc()))

    candidates: list[PaymentProvider] = []
    trace: list[dict] = []
    for account in res.scalars().all():
        entry = {"provider_key": account.provider_key, "priority": account.priority,
                 "environment": environment}
        if not has_provider(account.provider_key):
            trace.append({**entry, "selected": False, "reason": "plugin_not_registered"})
            continue
        plugin = get_provider(account.provider_key)
        ok, reason = match_capability(
            account, plugin, environment=environment, currency=currency,
            payment_method=payment_method, flow=flow, country=country)
        if not ok:
            trace.append({**entry, "selected": False, "reason": reason})
            continue
        health = plugin.health_check(environment).get("status")
        entry["health"] = health
        if health != "up":
            trace.append({**entry, "selected": False, "reason": f"unhealthy:{health}"})
            continue
        trace.append({**entry, "selected": True, "reason": "ok"})
        candidates.append(account)
    return candidates, trace


async def candidate_accounts(db, tenant_id, environment: str) -> list[PaymentProvider]:
    """Backward-compatible helper: eligible healthy accounts for an environment (no extra filters)."""
    candidates, _ = await plan_route(db, tenant_id, environment=environment)
    return candidates
