"""Mock/sandbox payment provider — the sole built-in plugin.

A development/test REFERENCE implementation of the full generic provider contract,
composed of the standardized building blocks in contracts.py. It NEVER represents a
real-money charge and needs no external credentials. Real providers are added later as
independent plugins that supply their own building blocks — with zero CloudPay core changes.
"""
import json
import uuid

from app.providers.base import PaymentProviderAdapter
from app.providers.contracts import (
    CallbackHandler,
    ChargeRequest,
    ErrorHandler,
    HealthCheck,
    PaymentFlow,
    ProviderApiClient,
    ProviderAuthentication,
    ProviderConfiguration,
    ProviderError,
    ProviderIntent,
    ProviderQR,
    ProviderReconciliation,
    ProviderResult,
    ProviderStatusResult,
    ProviderWebhookEvent,
    RequestMapper,
    ResponseMapper,
    StatusMapper,
)

# Sandbox decline rule shared by the building blocks: minor units ending in "13" decline.
_DECLINE_UNIT = 13

# ---- Simulated UPI lifecycle ----
# UPI intent/QR scenario is chosen from the amount's last two minor-unit digits so tests can
# drive every state deterministically. Each scenario maps to a UPI state + a CloudPay status.
# These are SIMULATED transactions only (raw carries simulated=True); no real bank rails.
_UPI_STATE_BY_CODE = {
    "p": ("pending", "pending"),
    "f": ("failed", "failed"),
    "e": ("expired", "failed"),
    "c": ("cancelled", "cancelled"),
    "s": ("success", "succeeded"),
}
_UPI_SCENARIO_BY_UNIT = {11: "p", 22: "f", 33: "e", 44: "c"}


def _is_upi(req: ChargeRequest) -> bool:
    return (req.metadata or {}).get("method") == "upi" or req.currency.upper() == "INR"


def _upi_scenario(amount_minor: int):
    code = _UPI_SCENARIO_BY_UNIT.get(amount_minor % 100, "s")
    upi_state, normalized = _UPI_STATE_BY_CODE[code]
    return code, upi_state, normalized


def _upi_link(req: ChargeRequest) -> str:
    # Standard UPI deep-link; amount in major units. Contains no card credentials.
    amt = f"{req.amount_minor / 100:.2f}"
    return f"upi://pay?pa=cloudpay@mockbank&pn=CloudPay&am={amt}&cu=INR&tn={req.reference}"


class _MockAuthentication(ProviderAuthentication):
    def prepare(self, config: ProviderConfiguration) -> dict:
        # No credentials required for the sandbox; returns an empty auth context.
        return {"mode": config.mode}


class _MockApiClient(ProviderApiClient):
    """In-memory 'API': simulates provider responses deterministically. No network."""
    def request(self, method: str, path: str, *, payload: dict | None = None,
                auth: dict | None = None) -> dict:
        payload = payload or {}
        if path == "/charges":
            declined = (payload.get("amount", 0) % 100) == _DECLINE_UNIT
            return {"id": f"mock_{uuid.uuid4().hex[:20]}",
                    "status": "declined" if declined else "succeeded",
                    "reason": "card_declined" if declined else None}
        if path == "/refunds":
            return {"id": f"mock_rf_{uuid.uuid4().hex[:16]}", "status": "succeeded"}
        if path == "/intents":
            return {"id": f"mock_pi_{uuid.uuid4().hex[:16]}",
                    "client_token": f"mock_cs_{uuid.uuid4().hex[:24]}", "status": "requires_confirmation"}
        if path == "/qr":
            return {"id": f"mock_qr_{uuid.uuid4().hex[:16]}",
                    "qr_payload": f"mockqr://{payload.get('reference','ref')}/{payload.get('amount',0)}"}
        if path.startswith("/status/"):
            return {"id": path.rsplit("/", 1)[-1], "status": "succeeded"}
        return {"status": "unknown"}


class _MockRequestMapper(RequestMapper):
    def to_create_payment(self, req: ChargeRequest) -> dict:
        return {"amount": req.amount_minor, "currency": req.currency.lower(),
                "reference": req.reference, "idempotency_key": req.idempotency_key}

    def to_refund(self, provider_txn_id: str, amount_minor: int, currency: str) -> dict:
        return {"charge": provider_txn_id, "amount": amount_minor, "currency": currency.lower()}


class _MockStatusMapper(StatusMapper):
    _MAP = {"succeeded": "succeeded", "declined": "failed", "failed": "failed",
            "requires_confirmation": "pending", "refunded": "refunded", "cancelled": "cancelled"}

    def to_cloudpay_status(self, provider_status: str) -> str:
        return self._MAP.get(provider_status, "pending")


class _MockResponseMapper(ResponseMapper):
    def __init__(self, status_mapper: StatusMapper):
        self._status = status_mapper

    def to_result(self, raw: dict) -> ProviderResult:
        status = self._status.to_cloudpay_status(raw.get("status", ""))
        ok = status in ("succeeded", "captured")
        return ProviderResult(success=ok, provider_txn_id=raw.get("id") if ok else raw.get("id"),
                              status=status, raw={"sandbox": True, **raw},
                              error=raw.get("reason") if not ok else None)


class _MockCallbackHandler(CallbackHandler):
    def verify_and_parse(self, payload: bytes, headers: dict) -> ProviderWebhookEvent:
        # A real plugin verifies a signature here; the sandbox accepts unsigned JSON.
        try:
            body = json.loads(payload or b"{}")
        except Exception as exc:
            raise ValueError("invalid mock callback payload") from exc
        return ProviderWebhookEvent(
            event_type=body.get("event_type", "payment.updated"),
            provider_txn_id=body.get("provider_txn_id"),
            normalized_status=body.get("status"),
            raw={"sandbox": True},
        )


class _MockErrorHandler(ErrorHandler):
    def to_provider_error(self, exc: Exception) -> ProviderError:
        return ProviderError(code="mock_error", message=str(exc), retryable=False)


class _MockHealthCheck(HealthCheck):
    def check(self, config: ProviderConfiguration) -> dict:
        return {"status": "up", "mode": config.mode, "test_mode": True}


class MockProvider(PaymentProviderAdapter):
    key = "mock"
    display_name = "Mock Sandbox Provider"
    supported_currencies = ["USD", "EUR", "GBP", "INR", "AED"]
    payment_methods = ["card", "bank", "wallet"]
    supported_flows = [PaymentFlow.DIRECT, PaymentFlow.INTENT, PaymentFlow.QR]
    # Reference/test provider: SANDBOX only — never live (no real money). A real plugin
    # would declare ["sandbox", "live"] and supply its own live building blocks.
    supported_environments = ["sandbox"]

    def __init__(self) -> None:
        self._auth = _MockAuthentication()
        self._client = _MockApiClient()
        self._req = _MockRequestMapper()
        self._status = _MockStatusMapper()
        self._resp = _MockResponseMapper(self._status)
        self._callback = _MockCallbackHandler()
        self._errors = _MockErrorHandler()
        self._health = _MockHealthCheck()

    # ---- configuration / discovery ----
    @property
    def mode(self) -> str:
        return "sandbox"

    def configuration(self, environment: str | None = None) -> ProviderConfiguration:
        return ProviderConfiguration(provider_key=self.key, mode=environment or "sandbox",
                                     options={"sandbox": True})

    def supports_webhooks(self) -> bool:
        return True

    def health_check(self, environment: str | None = None) -> dict:
        env = environment or "sandbox"
        if not self.supports_environment(env):
            return {"status": "unsupported_environment", "environment": env}
        return self._health.check(self.configuration(env))

    # ---- standardized contract ----
    def create_payment(self, req: ChargeRequest, config=None) -> ProviderResult:
        try:
            auth = self._auth.prepare(self.configuration())
            raw = self._client.request("POST", "/charges",
                                       payload=self._req.to_create_payment(req), auth=auth)
            return self._resp.to_result(raw)
        except Exception as exc:  # normalized error handling
            err = self._errors.to_provider_error(exc)
            return ProviderResult(success=False, provider_txn_id=None, status="failed",
                                  raw={"sandbox": True}, error=err.code)

    def refund(self, provider_txn_id: str, amount_minor: int, currency: str, config=None) -> ProviderResult:
        auth = self._auth.prepare(self.configuration())
        raw = self._client.request("POST", "/refunds",
                                   payload=self._req.to_refund(provider_txn_id, amount_minor, currency),
                                   auth=auth)
        return ProviderResult(success=True, provider_txn_id=raw.get("id"),
                              status=self._status.to_cloudpay_status(raw.get("status", "succeeded")),
                              raw={"sandbox": True, "original": provider_txn_id})

    def get_payment_status(self, provider_txn_id: str, config=None) -> ProviderStatusResult:
        # Simulated UPI intents/QRs encode their lifecycle scenario in the id (mock_upi_<code>_...).
        if provider_txn_id.startswith("mock_upi_"):
            code = provider_txn_id.split("_")[2]
            upi_state, normalized = _UPI_STATE_BY_CODE.get(code, ("success", "succeeded"))
            return ProviderStatusResult(provider_txn_id=provider_txn_id, normalized_status=normalized,
                                        raw={"simulated": True, "rail": "upi", "upi_state": upi_state})
        raw = self._client.request("GET", f"/status/{provider_txn_id}")
        return ProviderStatusResult(provider_txn_id=provider_txn_id,
                                    normalized_status=self._status.to_cloudpay_status(raw.get("status", "")),
                                    raw={"sandbox": True})

    def generate_intent(self, req: ChargeRequest, config=None) -> ProviderIntent:
        # UPI Intent (simulated): returns a UPI deep-link/collect intent, never card data.
        if _is_upi(req):
            code, upi_state, _ = _upi_scenario(req.amount_minor)
            intent_id = f"mock_upi_{code}_{uuid.uuid4().hex[:16]}"
            link = _upi_link(req)
            return ProviderIntent(intent_id=intent_id, client_token=link,
                                  raw={"simulated": True, "rail": "upi", "upi_state": upi_state,
                                       "vpa": "cloudpay@mockbank"})
        raw = self._client.request("POST", "/intents", payload=self._req.to_intent(req))
        return ProviderIntent(intent_id=raw["id"], client_token=raw.get("client_token"),
                              raw={"sandbox": True, "status": raw.get("status")})

    def generate_qr(self, req: ChargeRequest, config=None) -> ProviderQR:
        # UPI QR (simulated): scannable UPI payload string, never card data.
        if _is_upi(req):
            code, upi_state, _ = _upi_scenario(req.amount_minor)
            qr_id = f"mock_upi_{code}_{uuid.uuid4().hex[:16]}"
            return ProviderQR(qr_id=qr_id, qr_payload=_upi_link(req),
                              raw={"simulated": True, "rail": "upi", "upi_state": upi_state})
        raw = self._client.request("POST", "/qr", payload=self._req.to_qr(req))
        return ProviderQR(qr_id=raw["id"], qr_payload=raw["qr_payload"], raw={"sandbox": True})

    def verify_callback(self, payload: bytes, headers: dict,
                        environment: str | None = None) -> ProviderWebhookEvent:
        return self._callback.verify_and_parse(payload, headers)

    def reconcile(self, provider_txn_id: str, config=None) -> ProviderReconciliation:
        st = self.get_payment_status(provider_txn_id, config)
        return ProviderReconciliation(provider_txn_id=provider_txn_id,
                                      normalized_status=st.normalized_status,
                                      matched=True, raw=st.raw)
