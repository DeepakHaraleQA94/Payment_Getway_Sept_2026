"""Stripe provider adapter — TEST/SANDBOX only.

Isolated plugin implementing the existing provider interface. Never hard-codes
credentials (reads STRIPE_API_KEY from env), never returns/logs secrets, and refuses
to operate with a live key. Degrades gracefully to a safe error when unconfigured so
the app starts and other tests pass without Stripe credentials.
"""
import logging

from app.core.config import settings
from app.providers.base import ChargeRequest, ProviderResult

logger = logging.getLogger("cloudpay.providers.stripe")


class StripeProvider:
    key = "stripe"
    display_name = "Stripe (TEST/Sandbox)"
    supported_currencies = ["USD", "EUR", "GBP", "INR", "AED"]

    def __init__(self) -> None:
        self._api_key = settings.stripe_api_key or ""

    # ---- capability / health ----
    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @property
    def is_live(self) -> bool:
        return self._api_key.startswith("sk_live_")

    @property
    def mode(self) -> str:
        return "live" if self.is_live else "sandbox"

    def capabilities(self) -> dict:
        return {
            "key": self.key, "display_name": self.display_name, "mode": self.mode,
            "configured": self.configured, "supported_currencies": self.supported_currencies,
            "payment_methods": ["card"], "supports_refund": True, "supports_webhooks": True,
            "test_mode": not self.is_live,
        }

    def health_check(self) -> dict:
        if not self.configured:
            return {"status": "unconfigured", "mode": self.mode}
        if self.is_live:
            return {"status": "disabled_live", "mode": "live"}
        try:
            import stripe
            stripe.api_key = self._api_key
            stripe.Balance.retrieve()
            return {"status": "up", "mode": "sandbox", "test_mode": True}
        except Exception as exc:  # network/credential problems must not crash callers
            return {"status": "down", "mode": self.mode, "error": type(exc).__name__}

    def _guard(self) -> ProviderResult | None:
        if not self.configured:
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={}, error="stripe_not_configured")
        if self.is_live:
            # LIVE processing is disabled in this build.
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={}, error="stripe_live_disabled")
        return None

    # ---- payment interface ----
    def charge(self, req: ChargeRequest) -> ProviderResult:
        guard = self._guard()
        if guard:
            return guard
        import stripe
        stripe.api_key = self._api_key
        try:
            # Tokenized test payment method — CloudPay never handles raw PAN/CVV.
            intent = stripe.PaymentIntent.create(
                amount=req.amount_minor, currency=req.currency.lower(),
                payment_method="pm_card_visa", confirm=True, description=req.description,
                metadata={"reference": req.reference, **(req.metadata or {})},
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
                idempotency_key=(f"charge_{req.idempotency_key}" if req.idempotency_key else None),
            )
            ok = intent.status in ("succeeded", "requires_capture")
            return ProviderResult(success=ok, provider_txn_id=intent.id,
                                  status="succeeded" if ok else "failed",
                                  raw={"stripe_status": intent.status, "test_mode": True})
        except Exception as exc:
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={"test_mode": True}, error=type(exc).__name__)

    def retrieve(self, provider_txn_id: str) -> dict:
        if not self.configured or self.is_live:
            return {"status": "unavailable"}
        import stripe
        stripe.api_key = self._api_key
        try:
            intent = stripe.PaymentIntent.retrieve(provider_txn_id)
            return {"status": intent.status, "id": intent.id}
        except Exception as exc:
            return {"status": "error", "error": type(exc).__name__}

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> ProviderResult:
        guard = self._guard()
        if guard:
            return guard
        import stripe
        stripe.api_key = self._api_key
        try:
            refund = stripe.Refund.create(payment_intent=provider_txn_id, amount=amount_minor)
            ok = refund.status in ("succeeded", "pending")
            return ProviderResult(success=ok, provider_txn_id=refund.id,
                                  status="succeeded" if ok else "failed",
                                  raw={"stripe_status": refund.status, "test_mode": True})
        except Exception as exc:
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={"test_mode": True}, error=type(exc).__name__)

    # ---- webhook signature verification ----
    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        """Verify a Stripe webhook signature and return the parsed event, or raise."""
        import stripe
        return stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
