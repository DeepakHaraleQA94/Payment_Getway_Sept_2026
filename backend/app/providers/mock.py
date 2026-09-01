"""Mock/sandbox payment provider. NEVER represents a real-money charge.

Deterministic sandbox behaviour: amounts ending in specific minor units simulate
failures so tests and demos can exercise both success and failure paths.
"""
import uuid

from app.providers.base import ChargeRequest, PaymentProviderAdapter, ProviderResult


class MockProvider:
    key = "mock"
    display_name = "Mock Sandbox Provider"
    supported_currencies = ["USD", "EUR", "GBP", "INR", "AED"]

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


_provider: PaymentProviderAdapter = MockProvider()
