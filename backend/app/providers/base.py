"""Payment provider/plugin adapter interface.

All providers implement this interface so the payment engine never hard-codes a
single provider. New providers register themselves in the registry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ChargeRequest:
    amount_minor: int
    currency: str
    reference: str
    description: str | None = None
    customer_email: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    success: bool
    provider_txn_id: str | None
    status: str  # maps to PaymentStatus values
    raw: dict = field(default_factory=dict)
    error: str | None = None


class PaymentProviderAdapter(Protocol):
    key: str
    display_name: str
    supported_currencies: list[str]

    def charge(self, req: ChargeRequest) -> ProviderResult: ...

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> ProviderResult: ...
