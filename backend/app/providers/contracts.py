"""Generic provider plugin building blocks + normalized data contracts.

These are the standardized, provider-agnostic pieces every payment-provider plugin is
composed of. CloudPay core depends ONLY on these abstractions and on
`PaymentProviderAdapter` (see base.py) — never on any specific provider.

Plugin building blocks (each plugin supplies concrete implementations):
  * ProviderConfiguration  — mode + credential *reference* (names only, never raw secrets) + options
  * ProviderAuthentication — turns configuration into request auth context (headers/signing)
  * ProviderApiClient      — executes calls against the external provider API
  * RequestMapper          — CloudPay request  -> provider-native request
  * ResponseMapper         — provider response  -> normalized ProviderResult
  * StatusMapper           — provider status    -> CloudPay PaymentStatus
  * CallbackHandler        — verify + parse an inbound provider callback -> ProviderWebhookEvent
  * ErrorHandler           — provider/transport error -> normalized ProviderError
  * HealthCheck            — connectivity/credential probe -> status dict
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PaymentFlow(str, Enum):
    """Provider-agnostic payment initiation flows the contract supports."""
    DIRECT = "direct"   # immediate server-side charge
    INTENT = "intent"   # client-confirmed intent (client token / redirect)
    QR = "qr"           # QR / push payment (e.g. bank/UPI-style), no card data


# --------------------------- normalized request/result types ---------------------------
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
    status: str  # normalized PaymentStatus value
    raw: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ProviderIntent:
    """Result of an INTENT-flow initiation. Carries no card credentials."""
    intent_id: str
    client_token: str | None = None
    redirect_url: str | None = None
    expires_at: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderQR:
    """Result of a QR-flow initiation. `qr_payload` is scannable data, never card data."""
    qr_id: str
    qr_payload: str
    image_data_url: str | None = None
    expires_at: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderStatusResult:
    provider_txn_id: str
    normalized_status: str
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderReconciliation:
    provider_txn_id: str
    normalized_status: str
    amount_minor: int | None = None
    currency: str | None = None
    matched: bool = True
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderWebhookEvent:
    """Normalized inbound callback produced by a plugin from a raw provider payload."""
    event_type: str
    provider_txn_id: str | None = None
    normalized_status: str | None = None  # a PaymentStatus value, or None to ignore
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderCredentialField:
    """Describes a credential a plugin needs, by name only — never an actual secret value."""
    key: str
    label: str
    secret: bool = True
    required: bool = True


class ProviderError(Exception):
    """Normalized provider/transport error surfaced by a plugin's ErrorHandler."""
    def __init__(self, code: str, message: str, *, retryable: bool = False, raw: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.raw = raw or {}


@dataclass
class ProviderConfiguration:
    """Per-provider configuration. Holds a credential *reference*, never raw secret values."""
    provider_key: str
    mode: str = "sandbox"                 # sandbox | live
    credential_ref: str | None = None     # opaque reference/name to a secret store
    options: dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


# --------------------------- building-block interfaces ---------------------------
class ProviderAuthentication(ABC):
    @abstractmethod
    def prepare(self, config: ProviderConfiguration) -> dict:
        """Return an auth context (e.g. headers/signing material) for outbound requests."""
        ...


class ProviderApiClient(ABC):
    @abstractmethod
    def request(self, method: str, path: str, *, payload: dict | None = None,
                auth: dict | None = None) -> dict:
        """Execute a call against the external provider API and return the raw response."""
        ...


class RequestMapper(ABC):
    @abstractmethod
    def to_create_payment(self, req: ChargeRequest) -> dict: ...

    @abstractmethod
    def to_refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> dict: ...

    def to_intent(self, req: ChargeRequest) -> dict:
        return self.to_create_payment(req)

    def to_qr(self, req: ChargeRequest) -> dict:
        return self.to_create_payment(req)


class ResponseMapper(ABC):
    @abstractmethod
    def to_result(self, raw: dict) -> ProviderResult: ...


class StatusMapper(ABC):
    @abstractmethod
    def to_cloudpay_status(self, provider_status: str) -> str:
        """Map a provider-native status string to a CloudPay PaymentStatus value."""
        ...


class CallbackHandler(ABC):
    @abstractmethod
    def verify_and_parse(self, payload: bytes, headers: dict) -> ProviderWebhookEvent:
        """Verify the callback signature and translate it into a normalized event."""
        ...


class ErrorHandler(ABC):
    @abstractmethod
    def to_provider_error(self, exc: Exception) -> ProviderError: ...


class HealthCheck(ABC):
    @abstractmethod
    def check(self, config: ProviderConfiguration) -> dict: ...
