"""Mock/sandbox payment provider.

The ONLY built-in plugin: a development/test reference implementation of the generic
`PaymentProviderAdapter` contract. It NEVER represents a real-money charge and requires
no external credentials. Real providers are added later as independent plugins without
changing any CloudPay core code.
"""
import json
import uuid

from app.providers.base import (
    ChargeRequest,
    PaymentProviderAdapter,
    ProviderResult,
    ProviderWebhookEvent,
)


class MockProvider(PaymentProviderAdapter):
    key = "mock"
    display_name = "Mock Sandbox Provider"
    supported_currencies = ["USD", "EUR", "GBP", "INR", "AED"]

    @property
    def mode(self) -> str:
        return "sandbox"

    def supports_webhooks(self) -> bool:
        return True

    def _txn_id(self) -> str:
        return f"mock_{uuid.uuid4().hex[:20]}"

    def charge(self, req: ChargeRequest) -> ProviderResult:
        # Sandbox rule: amounts where minor units end in "13" simulate a decline.
        if req.amount_minor % 100 == 13:
            return ProviderResult(
                success=False,
                provider_txn_id=None,
                status="failed",
                raw={"sandbox": True, "reason": "card_declined"},
                error="Sandbox decline: card_declined",
            )
        return ProviderResult(
            success=True,
            provider_txn_id=self._txn_id(),
            status="succeeded",
            raw={"sandbox": True},
        )

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> ProviderResult:
        return ProviderResult(
            success=True,
            provider_txn_id=f"mock_rf_{uuid.uuid4().hex[:16]}",
            status="succeeded",
            raw={"sandbox": True, "original": provider_txn_id},
        )

    def verify_webhook(self, payload: bytes, headers: dict) -> ProviderWebhookEvent:
        """Reference webhook parser: accepts {provider_txn_id, status, event_type?} JSON.

        A real plugin would verify a provider signature here and map the provider's event
        shape to this normalized form. The mock keeps it credential-free for dev/test.
        """
        try:
            body = json.loads(payload or b"{}")
        except Exception as exc:
            raise ValueError("invalid mock webhook payload") from exc
        return ProviderWebhookEvent(
            event_type=body.get("event_type", "payment.updated"),
            provider_txn_id=body.get("provider_txn_id"),
            normalized_status=body.get("status"),
            raw={"sandbox": True},
        )
