"""Provider plugin registry: registration + discovery.

The core resolves providers only by `key` through this registry. Real providers are added
later as independent plugins by calling `register(MyProvider())` at import — no core code
changes required. Only the Mock dev/test provider is built in.
"""
from app.providers.base import PaymentProviderAdapter
from app.providers.example_provider import ExampleExternalProvider
from app.providers.demo_upi import DemoUpiProvider
from app.providers.mock import MockProvider
from app.providers.razorpay_provider import RazorpayProvider
from app.providers.stripe_provider import StripeProvider

_REGISTRY: dict[str, PaymentProviderAdapter] = {}


def register(adapter: PaymentProviderAdapter) -> None:
    _REGISTRY[adapter.key] = adapter


def get_provider(key: str) -> PaymentProviderAdapter:
    if key not in _REGISTRY:
        # Safe default: sandbox mock. Never silently use an unknown/live provider.
        return _REGISTRY["mock"]
    return _REGISTRY[key]


def has_provider(key: str) -> bool:
    return key in _REGISTRY


def list_providers() -> list[dict]:
    """Discovery: standardized capability metadata for every registered plugin."""
    return [a.capabilities() for a in _REGISTRY.values()]


# Register built-in adapters. Only the Mock dev/test provider ships with the core.
register(MockProvider())
# Example external PSP: an ISOLATED reference plugin proving a real provider (with sandbox+live
# and credential resolution) plugs in without any core change. No real PSP SDK is in the core.
register(ExampleExternalProvider())
# Stripe: an ISOLATED real-PSP adapter (SANDBOX/TEST only; live disabled). Talks to Stripe's real
# test API via the SDK, entirely behind the generic contract — the core payment engine is untouched.
register(StripeProvider())
# Razorpay: ISOLATED real-PSP plugin (UPI intent/QR + cards, HMAC webhooks, refund/capture).
# SANDBOX = deterministic simulation; LIVE = real Razorpay REST API when credentials are supplied.
register(RazorpayProvider())
# Demo UPI: ISOLATED sandbox-only plugin for the development UPI journey (INR intent/QR + demo
# app choices). Reuses the generic contract; live mode is rejected by capability checks.
register(DemoUpiProvider())
