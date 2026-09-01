"""Provider registry. Register adapters here; the payment engine resolves by key."""
from app.providers.base import PaymentProviderAdapter
from app.providers.mock import MockProvider

_REGISTRY: dict[str, PaymentProviderAdapter] = {}


def register(adapter: PaymentProviderAdapter) -> None:
    _REGISTRY[adapter.key] = adapter


def get_provider(key: str) -> PaymentProviderAdapter:
    if key not in _REGISTRY:
        # Safe default: sandbox mock. Never silently use a live provider.
        return _REGISTRY["mock"]
    return _REGISTRY[key]


def list_providers() -> list[dict]:
    return [
        {
            "key": a.key,
            "display_name": a.display_name,
            "supported_currencies": a.supported_currencies,
        }
        for a in _REGISTRY.values()
    ]


# Register built-in adapters.
register(MockProvider())
