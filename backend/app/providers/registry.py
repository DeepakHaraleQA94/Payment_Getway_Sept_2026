"""Provider registry. Register adapters here; the payment engine resolves by key.

Stripe is registered ONLY when a TEST key is configured. A live key (sk_live_)
is never registered, so the platform can never dispatch real-money charges.
"""
import logging

from app.providers.base import PaymentProviderAdapter
from app.providers.mock import MockProvider
from app.providers.stripe_provider import StripeProvider

logger = logging.getLogger("cloudpay.providers.registry")

_REGISTRY: dict[str, PaymentProviderAdapter] = {}


def register(adapter: PaymentProviderAdapter) -> None:
    _REGISTRY[adapter.key] = adapter


def get_provider(key: str) -> PaymentProviderAdapter:
    if key not in _REGISTRY:
        # Safe default: sandbox mock. Never silently use a live provider.
        return _REGISTRY["mock"]
    return _REGISTRY[key]


def has_provider(key: str) -> bool:
    return key in _REGISTRY


def _capabilities(a: PaymentProviderAdapter) -> dict:
    if hasattr(a, "capabilities"):
        return a.capabilities()
    return {
        "key": a.key,
        "display_name": a.display_name,
        "mode": "sandbox",
        "configured": True,
        "supported_currencies": a.supported_currencies,
        "payment_methods": ["card"],
        "supports_refund": True,
        "supports_webhooks": False,
        "test_mode": True,
    }


def list_providers() -> list[dict]:
    # Preserves key/display_name/supported_currencies for existing callers and
    # adds capability metadata (mode, test_mode, supports_*) for discovery.
    return [_capabilities(a) for a in _REGISTRY.values()]


# Register built-in adapters.
register(MockProvider())

# Register Stripe only in TEST mode when configured; never a live key.
_stripe = StripeProvider()
if _stripe.configured and not _stripe.is_live:
    register(_stripe)
    logger.info("Stripe provider registered (TEST/sandbox mode)")
elif _stripe.is_live:
    logger.warning("Stripe live key detected; adapter NOT registered (live mode disabled)")
