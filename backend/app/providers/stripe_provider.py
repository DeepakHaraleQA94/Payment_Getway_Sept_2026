"""Stripe provider adapter — ISOLATED real-PSP plugin (SANDBOX/TEST mode only).

Talks to Stripe's real TEST API via the official SDK, entirely behind the generic
PaymentProviderAdapter contract. The core payment engine is NOT modified and imports nothing
Stripe-specific. LIVE/real-money is intentionally DISABLED (sandbox is the only supported
environment here); a live capability boundary exists but is not enabled in this phase.

Security: the Stripe secret key is read at call time from resolved credentials (secret store) or
the STRIPE_API_KEY env fallback — never hard-coded, never logged, never returned. Payment
creation uses an idempotency key and never blind-retries (max_network_retries=0) so a transient
failure cannot create a duplicate charge. Inbound webhooks are verified with Stripe's signed
construct_event (HMAC + timestamp tolerance = replay protection) BEFORE the event is trusted.
"""
import os

from app.providers.base import PaymentProviderAdapter
from app.providers.contracts import (
    ChargeRequest,
    PaymentFlow,
    ProviderConfiguration,
    ProviderCredentialField,
    ProviderEnvironment,
    ProviderError,
    ProviderResult,
    ProviderStatusResult,
    ProviderWebhookEvent,
)

_REQUEST_TIMEOUT = 20  # seconds (connect + read) for every Stripe API call

# Stripe PaymentIntent status -> generic CloudPay payment status.
_STATUS_MAP = {
    "succeeded": "succeeded",
    "processing": "pending",
    "requires_payment_method": "pending",
    "requires_confirmation": "pending",
    "requires_action": "pending",
    "requires_capture": "authorized",
    "canceled": "cancelled",
}


class StripeProvider(PaymentProviderAdapter):
    key = "stripe"
    display_name = "Stripe"
    mode = "sandbox"
    supported_currencies = ["USD", "GBP", "EUR", "INR", "AUD", "CAD", "SGD"]
    supported_countries = ["US", "GB", "IN", "AU", "CA", "SG", "IE", "FR", "DE", "NL"]
    payment_methods = ["card"]
    supported_flows = [PaymentFlow.DIRECT]
    # SANDBOX only — LIVE is deliberately excluded until a future authorized phase.
    supported_environments = [ProviderEnvironment.SANDBOX.value]

    def required_credentials(self) -> list[ProviderCredentialField]:
        return [
            ProviderCredentialField(key="api_key", label="Stripe Secret Key (test)", secret=True, required=True),
            ProviderCredentialField(key="webhook_secret", label="Webhook Signing Secret", secret=True, required=False),
        ]

    # ---- credential resolution (no secrets logged / stored in code) ----
    def _api_key(self, config: ProviderConfiguration | None) -> str | None:
        creds = (config.options or {}).get("credentials") if config and config.options else None
        return (creds or {}).get("api_key") or os.environ.get("STRIPE_API_KEY")

    def _webhook_secret(self, config: ProviderConfiguration | None) -> str | None:
        creds = (config.options or {}).get("credentials") if config and config.options else None
        return (creds or {}).get("webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET")

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("STRIPE_API_KEY"))

    def _client(self, config: ProviderConfiguration | None):
        import stripe  # lazy import: keeps the registry safe even if the SDK is absent
        key = self._api_key(config)
        if not key:
            raise ProviderError("unconfigured", "Stripe API key is not configured")
        if key.startswith("sk_live_"):
            # Hard guard: this adapter must never touch a live key in this phase.
            raise ProviderError("live_disabled", "Live Stripe key rejected: live mode is disabled")
        stripe.api_key = key
        stripe.max_network_retries = 0  # never blind-retry a charge -> no duplicate payments
        return stripe

    def _map_error(self, stripe, exc) -> ProviderError:
        if isinstance(exc, stripe.error.AuthenticationError):
            return ProviderError("invalid_credentials", "Invalid Stripe credentials")
        if isinstance(exc, (stripe.error.APIConnectionError,)):
            return ProviderError("network_error", "Could not reach Stripe", retryable=True)
        if isinstance(exc, stripe.error.RateLimitError):
            return ProviderError("rate_limited", "Stripe rate limit", retryable=True)
        if isinstance(exc, stripe.error.APIError):
            return ProviderError("provider_error", "Stripe server error", retryable=True)
        if isinstance(exc, stripe.error.InvalidRequestError):
            return ProviderError("invalid_request", str(getattr(exc, "user_message", "") or "Invalid request"))
        return ProviderError("provider_error", "Unexpected Stripe error")

    # ---- payment creation (DIRECT / PaymentIntent) ----
    def create_payment(self, req: ChargeRequest, config: ProviderConfiguration | None = None) -> ProviderResult:
        stripe = self._client(config)
        try:
            intent = stripe.PaymentIntent.create(
                amount=req.amount_minor,
                currency=req.currency.lower(),
                payment_method_types=["card"],
                description=req.description or req.reference,
                metadata={"reference": req.reference, **{k: str(v) for k, v in (req.metadata or {}).items()}},
                idempotency_key=req.idempotency_key,  # dedupe at Stripe -> no duplicate charge
                timeout=_REQUEST_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - normalized below
            if isinstance(exc, stripe.error.CardError):  # declined
                return ProviderResult(success=False, provider_txn_id=getattr(exc, "payment_intent", {}).get("id") if isinstance(getattr(exc, "payment_intent", None), dict) else None,
                                      status="failed", error="card_declined", raw={"code": getattr(exc, "code", None)})
            raise self._map_error(stripe, exc)
        return self._result_from_intent(intent)

    def get_payment_status(self, provider_txn_id: str, config: ProviderConfiguration | None = None) -> ProviderStatusResult:
        stripe = self._client(config)
        try:
            intent = stripe.PaymentIntent.retrieve(provider_txn_id, timeout=_REQUEST_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(stripe, exc)
        pid = intent.get("id") if isinstance(intent, dict) else getattr(intent, "id", provider_txn_id)
        st = intent.get("status") if isinstance(intent, dict) else getattr(intent, "status", "")
        return ProviderStatusResult(provider_txn_id=pid, normalized_status=_STATUS_MAP.get(st, "unknown"),
                                    raw={"provider_status": st})

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str,
               config: ProviderConfiguration | None = None) -> ProviderResult:
        stripe = self._client(config)
        try:
            r = stripe.Refund.create(payment_intent=provider_txn_id, amount=amount_minor,
                                     timeout=_REQUEST_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(stripe, exc)
        rid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
        return ProviderResult(success=True, provider_txn_id=rid, status="refunded", raw={"refund_id": rid})

    def _result_from_intent(self, intent) -> ProviderResult:
        d = intent if isinstance(intent, dict) else intent
        pid = d.get("id") if isinstance(d, dict) else getattr(d, "id", None)
        raw_status = d.get("status") if isinstance(d, dict) else getattr(d, "status", None)
        if not pid or not raw_status:
            raise ProviderError("malformed_response", "Stripe returned an unrecognized PaymentIntent")
        status = _STATUS_MAP.get(raw_status, "unknown")
        return ProviderResult(success=status in ("succeeded", "pending", "authorized"),
                              provider_txn_id=pid, status=status,
                              raw={"provider_status": raw_status})

    # ---- inbound webhook: verify signature BEFORE trusting the event ----
    def verify_callback(self, payload: bytes, headers: dict, environment: str | None = None) -> ProviderWebhookEvent:
        import stripe
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        # header lookup is case-insensitive
        sig = None
        for k, v in (headers or {}).items():
            if k.lower() == "stripe-signature":
                sig = v
                break
        if not secret:
            raise ProviderError("unconfigured", "Webhook signing secret not configured")
        try:
            event = stripe.Webhook.construct_event(payload, sig, secret)  # raises on bad sig / stale ts
        except Exception as exc:  # noqa: BLE001 - includes SignatureVerificationError (replay/tamper)
            raise ProviderError("invalid_signature", "Webhook signature verification failed")
        etype = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        # Map the event object to a PaymentIntent id + normalized status.
        pid = obj.get("id") if obj.get("object") == "payment_intent" else obj.get("payment_intent")
        raw_status = obj.get("status")
        normalized = _STATUS_MAP.get(raw_status) if raw_status else None
        if etype.startswith("charge.refunded"):
            normalized = "refunded"
        # event id carried in raw so the platform can dedupe replays/duplicates idempotently
        return ProviderWebhookEvent(event_type=etype, provider_txn_id=pid, normalized_status=normalized,
                                    raw={"event_id": event.get("id"), "provider_status": raw_status})
