"""Provider plugin registry: registration + discovery.

The core resolves providers only by `key` through this registry. Real providers are added
later as independent plugins by calling `register(MyProvider())` at import — no core code
changes required. Only the Mock dev/test provider is built in.
"""
from app.providers.base import PaymentProviderAdapter
from app.providers.mock import MockProvider

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
