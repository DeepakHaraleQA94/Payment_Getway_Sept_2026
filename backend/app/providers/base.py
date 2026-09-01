"""Generic payment-provider plugin/adapter contract.

CloudPay core is provider-agnostic: it depends ONLY on this contract, never on any
specific provider (no provider-specific logic of any kind in the core). A provider is added as
an independent plugin that implements this interface and translates between the external
provider's API and these standardized CloudPay types. Plugins register themselves in
`app.providers.registry`; the payment engine resolves them by `key`.

Contract surface (all generic):
 * identity + capability discovery (`capabilities`)
 * credential-reference interface (`required_credentials`) — describes needed secrets by
   name only; the core NEVER stores or handles raw provider secrets here
 * sandbox/live mode abstraction (`mode`, `is_live`, `configured`)
 * health-check (`health_check`)
 * payment (`charge`), refund (`refund`), status (`get_status`)
 * inbound webhook (`supports_webhooks`, `verify_webhook`) returning a normalized event
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChargeRequest:
    amount_minor: int
    currency: str
    reference: str
    description: str | None = None
    customer_email: str | None = None
    idempotency_key: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    success: bool
    provider_txn_id: str | None
    status: str  # maps to PaymentStatus values (normalized by the plugin)
    raw: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ProviderCredentialField:
    """Describes a credential a plugin needs, by name only — never an actual secret value."""
    key: str
    label: str
    secret: bool = True
    required: bool = True


@dataclass
class ProviderWebhookEvent:
    """Normalized inbound webhook event produced by a plugin from a raw provider payload."""
    event_type: str
    provider_txn_id: str | None = None
    normalized_status: str | None = None  # a PaymentStatus value, or None to ignore
    raw: dict = field(default_factory=dict)


class PaymentProviderAdapter(ABC):
    """Standardized contract every provider plugin implements. Core depends only on this."""

    key: str = "base"
    display_name: str = "Base Provider"
    supported_currencies: list[str] = []
    payment_methods: list[str] = ["card"]

    # ---- sandbox/live abstraction ----
    @property
    def mode(self) -> str:
        """'sandbox' or 'live'. Default sandbox; plugins override based on their config."""
        return "sandbox"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def configured(self) -> bool:
        """Whether the plugin has the credentials/config it needs to operate."""
        return True

    # ---- capability / credential / health discovery ----
    def required_credentials(self) -> list[ProviderCredentialField]:
        return []

    def supports_refund(self) -> bool:
        return True

    def supports_webhooks(self) -> bool:
        return False

    def capabilities(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "mode": self.mode,
            "configured": self.configured,
            "supported_currencies": self.supported_currencies,
            "payment_methods": self.payment_methods,
            "supports_refund": self.supports_refund(),
            "supports_webhooks": self.supports_webhooks(),
            "test_mode": not self.is_live,
            "required_credentials": [
                {"key": c.key, "label": c.label, "secret": c.secret, "required": c.required}
                for c in self.required_credentials()
            ],
        }

    def health_check(self) -> dict:
        return {"status": "up" if self.configured else "unconfigured", "mode": self.mode}

    # ---- payment interface ----
    @abstractmethod
    def charge(self, req: ChargeRequest) -> ProviderResult:
        ...

    @abstractmethod
    def refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> ProviderResult:
        ...

    def get_status(self, provider_txn_id: str) -> dict:
        return {"status": "unknown", "provider_txn_id": provider_txn_id}

    # ---- inbound webhook interface ----
    def verify_webhook(self, payload: bytes, headers: dict) -> ProviderWebhookEvent:
        """Verify signature and translate a raw provider payload into a normalized event.

        Plugins that support webhooks override this. Must raise on invalid signatures.
        """
        raise NotImplementedError("This provider does not implement inbound webhooks")
