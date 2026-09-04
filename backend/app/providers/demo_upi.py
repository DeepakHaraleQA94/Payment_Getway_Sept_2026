"""Demo UPI provider — an ISOLATED sandbox-only plugin for the development UPI journey.

It reuses the generic provider contract (subclassing the built-in Mock reference plugin) so the
CloudPay core — payment engine, routing, idempotency, fees, ledger, state machine, webhook
reconciliation — is used unchanged. It NEVER touches real money, a real bank, a real PSP, or a
real UPI network, and is HARD-restricted to the sandbox environment (demo_upi + live is rejected
by the existing capability checks). The "PhonePe / Google Pay / Paytm / BHIM / QR" choices are
purely demo UI options surfaced via capability metadata; they are not real integrations.
"""
from app.providers.contracts import PaymentFlow
from app.providers.mock import MockProvider

# Demo-only UPI app choices surfaced to the checkout UI. NOT real integrations.
DEMO_UPI_APPS = [
    {"key": "phonepe", "label": "PhonePe"},
    {"key": "gpay", "label": "Google Pay"},
    {"key": "paytm", "label": "Paytm"},
    {"key": "bhim", "label": "BHIM"},
    {"key": "other", "label": "Other UPI App"},
    {"key": "qr", "label": "Scan QR"},
]


class DemoUpiProvider(MockProvider):
    """Sandbox-only UPI demo provider. Inherits the full standardized contract from MockProvider
    (create_payment/intent/QR/status/webhook), so no CloudPay core change is needed."""
    key = "demo_upi"
    display_name = "Demo UPI (Sandbox)"
    supported_currencies = ["INR"]
    payment_methods = ["upi", "upi_intent", "upi_qr"]
    supported_flows = [PaymentFlow.INTENT, PaymentFlow.QR, PaymentFlow.DIRECT]
    # HARD sandbox-only: the existing capability/routing checks reject demo_upi + live.
    supported_environments = ["sandbox"]

    @property
    def mode(self) -> str:
        return "sandbox"

    def capabilities(self) -> dict:
        caps = super().capabilities()
        caps.update({
            "demo": True,
            "sandbox_only": True,
            "supported_countries": ["IN"],
            "upi_apps": DEMO_UPI_APPS,
        })
        return caps

    def health_check(self, environment: str | None = None) -> dict:
        env = environment or "sandbox"
        if not self.supports_environment(env):
            return {"status": "unsupported_environment", "environment": env}
        return {"status": "up", "mode": "sandbox", "test_mode": True, "demo": True}
