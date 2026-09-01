"""Provider-agnostic email adapter. Noop by default; Resend/SendGrid drop in later.

No email provider key is required or stored. When a provider is configured via
EMAIL_PROVIDER (+ its own credentials) a real adapter can be registered here without
changing callers.
"""
import logging
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger("cloudpay.email")


class EmailProvider(Protocol):
    name: str

    def send(self, *, to: str, subject: str, body: str, attachment_url: str | None) -> dict: ...


class NoopEmailProvider:
    """Records intent without sending. Used until a real provider is configured."""
    name = "noop"

    def send(self, *, to: str, subject: str, body: str, attachment_url: str | None) -> dict:
        logger.info("email skipped (noop) to=%s subject=%s attachment=%s", to, subject, attachment_url)
        return {"provider": "noop", "delivered": False, "status": "skipped_no_provider"}


_REGISTRY: dict[str, EmailProvider] = {"noop": NoopEmailProvider()}


def register_provider(provider: EmailProvider) -> None:
    _REGISTRY[provider.name] = provider


def get_email_provider() -> EmailProvider:
    return _REGISTRY.get(settings.email_provider, _REGISTRY["noop"])


def send_email(*, to: str | None, subject: str, body: str, attachment_url: str | None = None) -> dict:
    if not to:
        return {"provider": get_email_provider().name, "delivered": False, "status": "no_recipient"}
    return get_email_provider().send(to=to, subject=subject, body=body, attachment_url=attachment_url)
