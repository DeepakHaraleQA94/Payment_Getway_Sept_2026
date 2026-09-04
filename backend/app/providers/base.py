"""Generic payment-provider plugin/adapter contract.

CloudPay core is provider-agnostic: it depends ONLY on this contract, never on any
specific provider (no provider-specific logic of any kind in the core). A provider is added
as an independent plugin that implements this interface and translates between the external
provider's API and the standardized CloudPay types (see contracts.py). Plugins register
themselves in `app.providers.registry`; the core resolves them by `key`.

Standardized contract methods (SRD):
  create_payment, get_payment_status, generate_intent, generate_qr, verify_callback, reconcile
plus refund. Each plugin is composed of the building blocks in contracts.py (configuration,
authentication, api client, request/response/status mappers, callback + error handlers,
health check).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

# Re-export normalized types + building blocks so callers can import from either module.
from app.providers.contracts import (  # noqa: F401
    CallbackHandler,
    ChargeRequest,
    EnvironmentConfig,
    ErrorHandler,
    HealthCheck,
    PaymentFlow,
    ProviderApiClient,
    ProviderAuthentication,
    ProviderConfiguration,
    ProviderCredentialField,
    ProviderEnvironment,
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


class PaymentProviderAdapter(ABC):
    """Standardized contract every provider plugin implements. Core depends only on this."""

    key: str = "base"
    display_name: str = "Base Provider"
    supported_currencies: list[str] = []
    payment_methods: list[str] = ["card"]
    supported_flows: list[PaymentFlow] = [PaymentFlow.DIRECT]
    # Countries/regions (ISO-3166 alpha-2) a plugin can serve. Empty = unrestricted (all).
    # This is a capability the account config may further constrain; the core stays generic
    # and never hard-codes a country.
    supported_countries: list[str] = []
    # Environments this plugin can operate in. Both sandbox and live are permanently part of
    # the architecture; a plugin opts into live by including it here (with proper safeguards).
    supported_environments: list[str] = [ProviderEnvironment.SANDBOX.value]

    # ---- configuration + sandbox/live abstraction ----
    def configuration(self, environment: str | None = None) -> ProviderConfiguration:
        return ProviderConfiguration(provider_key=self.key, mode=environment or self.mode)

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

    def supports_environment(self, environment: str) -> bool:
        return environment in self.supported_environments

    # ---- capability / credential / health discovery ----
    def required_credentials(self) -> list[ProviderCredentialField]:
        return []

    def supports_refund(self) -> bool:
        return True

    def supports_capture(self) -> bool:
        """Whether this plugin can capture a previously authorized payment. Default: no."""
        return False

    def supports_void(self) -> bool:
        """Whether this plugin can void/cancel a previously authorized payment. Default: no."""
        return False

    def supports_webhooks(self) -> bool:
        return False

    def supports_intent(self) -> bool:
        return PaymentFlow.INTENT in self.supported_flows

    def supports_qr(self) -> bool:
        return PaymentFlow.QR in self.supported_flows

    def capabilities(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "mode": self.mode,
            "configured": self.configured,
            "supported_currencies": self.supported_currencies,
            "supported_countries": list(self.supported_countries),
            "payment_methods": self.payment_methods,
            "supported_flows": [f.value for f in self.supported_flows],
            "supported_environments": list(self.supported_environments),
            "live_supported": ProviderEnvironment.LIVE.value in self.supported_environments,
            "supports_refund": self.supports_refund(),
            "supports_capture": self.supports_capture(),
            "supports_void": self.supports_void(),
            "supports_webhooks": self.supports_webhooks(),
            "supports_intent": self.supports_intent(),
            "supports_qr": self.supports_qr(),
            "test_mode": not self.is_live,
            "required_credentials": [
                {"key": c.key, "label": c.label, "secret": c.secret, "required": c.required}
                for c in self.required_credentials()
            ],
        }

    def health_check(self, environment: str | None = None) -> dict:
        env = environment or self.mode
        status = "up" if self.configured else "unconfigured"
        if not self.supports_environment(env):
            status = "unsupported_environment"
        return {"status": status, "environment": env}

    def test_connection(self, environment: str | None = None,
                        config: "ProviderConfiguration | None" = None) -> dict:
        """Validate a provider/environment (optionally with UNSAVED, in-memory credentials).

        Additive to the health contract: onboarding calls this to test the credentials an operator
        just entered in the wizard BEFORE persisting them. The default implementation checks
        environment support and — when the plugin declares required credentials — that they were
        supplied in config.options['credentials']. Real plugins override this to actually ping their
        API. Must never return or log raw secret values.
        """
        env = environment or self.mode
        if not self.supports_environment(env):
            return {"status": "unsupported_environment", "environment": env,
                    "detail": f"Provider does not support the '{env}' environment"}
        required = self.required_credentials()
        if required:
            creds = (config.options.get("credentials") if config and config.options else None) or {}
            missing = [c.key for c in required if c.required and not str(creds.get(c.key, "")).strip()]
            if missing:
                return {"status": "invalid_credentials", "environment": env,
                        "detail": f"Missing required credential(s): {', '.join(missing)}"}
        return {"status": "up", "environment": env, "detail": "Connection parameters accepted"}

    # ---- standardized payment contract ----
    # `config` (optional) carries the resolved per-account environment + credentials for this
    # call. The core resolves it from the account's credential reference via the secret store and
    # passes it in; plugins that need credentials read `config.options["credentials"]`. The Mock
    # ignores it. This keeps the core provider-agnostic and secrets out of the provider records.
    @abstractmethod
    def create_payment(self, req: ChargeRequest,
                       config: "ProviderConfiguration | None" = None) -> ProviderResult:
        ...

    @abstractmethod
    def refund(self, provider_txn_id: str, amount_minor: int, currency: str,
               config: "ProviderConfiguration | None" = None) -> ProviderResult:
        ...

    def get_payment_status(self, provider_txn_id: str,
                           config: "ProviderConfiguration | None" = None) -> ProviderStatusResult:
        return ProviderStatusResult(provider_txn_id=provider_txn_id, normalized_status="unknown")

    def capture(self, provider_txn_id: str, amount_minor: int, currency: str,
                config: "ProviderConfiguration | None" = None) -> ProviderResult:
        """Capture a previously AUTHORIZED payment. Plugins that support it override this.

        Providers without the capability MUST surface a normalized unsupported-capability error.
        """
        raise ProviderError("unsupported_capability", "capture is not supported by this provider")

    def void(self, provider_txn_id: str,
             config: "ProviderConfiguration | None" = None) -> ProviderResult:
        """Void/cancel a previously AUTHORIZED payment before capture. Plugins override this."""
        raise ProviderError("unsupported_capability", "void is not supported by this provider")

    def generate_intent(self, req: ChargeRequest,
                        config: "ProviderConfiguration | None" = None) -> ProviderIntent:
        raise ProviderError("unsupported_flow", "intent flow not supported by this provider")

    def generate_qr(self, req: ChargeRequest,
                    config: "ProviderConfiguration | None" = None) -> ProviderQR:
        raise ProviderError("unsupported_flow", "qr flow not supported by this provider")

    def verify_callback(self, payload: bytes, headers: dict,
                        environment: str | None = None) -> ProviderWebhookEvent:
        """Verify signature and translate a raw provider payload into a normalized event.

        Plugins that support callbacks override this. `environment` allows environment-specific
        callback handling (e.g. distinct sandbox/live signing secrets). Must raise on invalid
        signatures.
        """
        raise NotImplementedError("This provider does not implement inbound callbacks")

    def reconcile(self, provider_txn_id: str,
                  config: "ProviderConfiguration | None" = None) -> ProviderReconciliation:
        """Fetch the provider's source-of-truth status for reconciliation. Defaults to status."""
        st = self.get_payment_status(provider_txn_id, config)
        return ProviderReconciliation(provider_txn_id=provider_txn_id,
                                      normalized_status=st.normalized_status, raw=st.raw)

    # ---- backward-compatible aliases (legacy names) ----
    def charge(self, req: ChargeRequest,
               config: "ProviderConfiguration | None" = None) -> ProviderResult:
        return self.create_payment(req, config)

    def verify_webhook(self, payload: bytes, headers: dict,
                       environment: str | None = None) -> ProviderWebhookEvent:
        return self.verify_callback(payload, headers, environment)
