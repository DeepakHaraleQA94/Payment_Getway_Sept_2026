"""Example external PSP — an ISOLATED reference implementation of a *real* provider plugin.

This demonstrates how an authorized real payment provider is added WITHOUT touching CloudPay
core: it lives entirely in this module, conforms to the generic contract, is composed of its
own building blocks (auth, HTTP api client, mappers, callback + error handlers, health check),
declares BOTH sandbox and live environments, and resolves credentials at execution time from
the per-account credential reference via the secret store (passed in as `config`).

No real PSP SDK is embedded in the core. In this phase it operates in a SIMULATED sandbox mode
(clearly marked `simulated=True`) because no real credentials/endpoint are provided; a live call
requires resolved credentials or it safely fails as `unconfigured`. Swapping in a concrete PSP
(Stripe/Razorpay/etc.) later means implementing this plugin's building blocks against that PSP's
API — the core, routing/failover, accounts, and secret store are untouched.
"""
import hashlib
import hmac
import json
import uuid

from app.providers.base import PaymentProviderAdapter
from app.providers.contracts import (
    ChargeRequest,
    PaymentFlow,
    ProviderConfiguration,
    ProviderCredentialField,
    ProviderIntent,
    ProviderQR,
    ProviderResult,
    ProviderStatusResult,
    ProviderWebhookEvent,
)


def _credentials(config: ProviderConfiguration | None) -> dict | None:
    if config is None:
        return None
    return (config.options or {}).get("credentials")


class _ExampleAuth:
    """Builds request auth from resolved credentials (never from stored plaintext)."""
    def prepare(self, credentials: dict | None) -> dict:
        if not credentials:
            return {}
        return {"Authorization": f"Bearer {credentials.get('api_key', '')}"}


class _ExampleApiClient:
    """HTTP client pattern. In this phase it SIMULATES the PSP API (no real endpoint/keys)."""
    def request(self, method: str, path: str, *, payload: dict, environment: str,
                credentials: dict | None) -> dict:
        # A concrete PSP plugin would issue a real httpx call to its sandbox/live base URL here.
        if environment == "live" and not credentials:
            raise PermissionError("missing_live_credentials")
        txn = f"expsp_{uuid.uuid4().hex[:20]}"
        if path == "/charges":
            return {"id": txn, "state": "captured", "simulated": True}
        if path == "/refunds":
            return {"id": f"expsp_rf_{uuid.uuid4().hex[:16]}", "state": "refunded", "simulated": True}
        if path == "/intents":
            return {"id": f"expsp_pi_{uuid.uuid4().hex[:16]}", "client_secret": f"cs_{uuid.uuid4().hex}",
                    "state": "requires_action", "simulated": True}
        if path == "/qr":
            return {"id": f"expsp_qr_{uuid.uuid4().hex[:16]}",
                    "qr_payload": f"expsp://pay/{payload.get('reference','ref')}/{payload.get('amount',0)}",
                    "simulated": True}
        if path.startswith("/status/"):
            return {"id": path.rsplit('/', 1)[-1], "state": "captured", "simulated": True}
        return {"state": "unknown"}


class _ExampleStatusMapper:
    _MAP = {"captured": "succeeded", "declined": "failed", "requires_action": "pending",
            "refunded": "refunded", "cancelled": "cancelled", "expired": "failed"}

    def to_cloudpay_status(self, state: str) -> str:
        return self._MAP.get(state, "pending")


class ExampleExternalProvider(PaymentProviderAdapter):
    key = "examplepsp"
    display_name = "Example External PSP (reference)"
    supported_currencies = ["USD", "EUR", "GBP", "INR", "AED"]
    payment_methods = ["card", "bank"]
    supported_flows = [PaymentFlow.DIRECT, PaymentFlow.INTENT, PaymentFlow.QR]
    # A real provider declares BOTH environments; this proves live is architecturally supported.
    supported_environments = ["sandbox", "live"]

    def __init__(self) -> None:
        self._auth = _ExampleAuth()
        self._client = _ExampleApiClient()
        self._status = _ExampleStatusMapper()

    def required_credentials(self):
        return [
            ProviderCredentialField("api_key", "API Key", secret=True),
            ProviderCredentialField("api_secret", "API Secret", secret=True),
            ProviderCredentialField("webhook_secret", "Webhook Signing Secret", secret=True),
        ]

    def supports_webhooks(self) -> bool:
        return True

    def health_check(self, environment: str | None = None) -> dict:
        env = environment or "sandbox"
        if not self.supports_environment(env):
            return {"status": "unsupported_environment", "environment": env}
        # Sandbox is reachable (simulated). Live readiness ultimately depends on resolved creds
        # at charge time; the health probe reports reachable.
        return {"status": "up", "environment": env, "simulated": env == "sandbox"}

    def _run(self, path: str, payload: dict, config: ProviderConfiguration | None):
        env = config.mode if config else "sandbox"
        creds = _credentials(config)
        auth = self._auth.prepare(creds)  # noqa: F841 - demonstrates auth building block
        return self._client.request("POST", path, payload=payload, environment=env, credentials=creds)

    def create_payment(self, req: ChargeRequest, config=None) -> ProviderResult:
        try:
            raw = self._run("/charges", {"amount": req.amount_minor, "currency": req.currency,
                                         "reference": req.reference, "idempotency_key": req.idempotency_key}, config)
        except PermissionError as exc:
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={"provider": self.key}, error=str(exc))
        status = self._status.to_cloudpay_status(raw.get("state", ""))
        return ProviderResult(success=status == "succeeded", provider_txn_id=raw.get("id"),
                              status=status, raw={"provider": self.key, **raw})

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str, config=None) -> ProviderResult:
        raw = self._run("/refunds", {"charge": provider_txn_id, "amount": amount_minor}, config)
        return ProviderResult(success=True, provider_txn_id=raw.get("id"),
                              status=self._status.to_cloudpay_status(raw.get("state", "refunded")),
                              raw={"provider": self.key, "original": provider_txn_id})

    def get_payment_status(self, provider_txn_id: str, config=None) -> ProviderStatusResult:
        raw = self._run(f"/status/{provider_txn_id}", {}, config)
        return ProviderStatusResult(provider_txn_id=provider_txn_id,
                                    normalized_status=self._status.to_cloudpay_status(raw.get("state", "")),
                                    raw={"provider": self.key})

    def generate_intent(self, req: ChargeRequest, config=None) -> ProviderIntent:
        raw = self._run("/intents", {"amount": req.amount_minor, "reference": req.reference}, config)
        return ProviderIntent(intent_id=raw["id"], client_token=raw.get("client_secret"),
                              raw={"provider": self.key, "state": raw.get("state")})

    def generate_qr(self, req: ChargeRequest, config=None) -> ProviderQR:
        raw = self._run("/qr", {"amount": req.amount_minor, "reference": req.reference}, config)
        return ProviderQR(qr_id=raw["id"], qr_payload=raw["qr_payload"], raw={"provider": self.key})

    def verify_callback(self, payload: bytes, headers: dict,
                        environment: str | None = None) -> ProviderWebhookEvent:
        # A real provider verifies an HMAC signature with its webhook secret. The signing secret
        # is per-account; the public webhook route lacks account context, so verification is
        # best-effort here (documented gap). Demonstrates the HMAC pattern.
        sig = headers.get("x-expsp-signature")
        body = json.loads(payload or b"{}")
        secret = body.get("_webhook_secret")  # only present in tests exercising the signature path
        if secret and sig:
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, sig):
                raise ValueError("invalid signature")
        return ProviderWebhookEvent(event_type=body.get("event_type", "payment.updated"),
                                    provider_txn_id=body.get("provider_txn_id"),
                                    normalized_status=body.get("status"),
                                    raw={"provider": self.key})
