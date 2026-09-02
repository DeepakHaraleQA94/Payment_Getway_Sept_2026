"""Provider-agnostic email adapter. Noop by default; Resend activates when configured.

No email provider key is stored in code. When RESEND_API_KEY (+ SENDER_EMAIL) is set in the
environment the Resend adapter auto-registers; otherwise the noop adapter records intent only.
Callers use `send_email(...)` and never depend on the concrete provider.
"""
import base64
import logging
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger("cloudpay.email")


class EmailProvider(Protocol):
    name: str

    def send(self, *, to: str, subject: str, body: str,
             attachment_url: str | None, attachment: dict | None) -> dict: ...


class NoopEmailProvider:
    """Records intent without sending. Used until a real provider is configured."""
    name = "noop"

    def send(self, *, to, subject, body, attachment_url=None, attachment=None) -> dict:
        logger.info("email skipped (noop) to=%s subject=%s attachment=%s", to, subject,
                    bool(attachment) or attachment_url)
        return {"provider": "noop", "delivered": False, "status": "skipped_no_provider"}


def _html_wrap(body: str) -> str:
    """Wrap a plain-text body in minimal, email-client-safe inline HTML."""
    safe = body.replace("\n", "<br>")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#111;'
        'line-height:1.6">'
        f"{safe}"
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0">'
        '<div style="font-size:12px;color:#888">CloudPay · Sandbox environment</div>'
        "</div>"
    )


class ResendEmailProvider:
    """Sends real transactional email via Resend. Fails gracefully (logs, never raises)."""
    name = "resend"

    def __init__(self, api_key: str, sender: str):
        import resend
        resend.api_key = api_key
        self._resend = resend
        self._sender = sender

    def send(self, *, to, subject, body, attachment_url=None, attachment=None) -> dict:
        html = _html_wrap(body)
        if attachment_url and not attachment:
            html = _html_wrap(f"{body}\n\nDownload: {attachment_url}")
        params = {"from": self._sender, "to": [to], "subject": subject, "html": html}
        if attachment and attachment.get("content"):
            content = attachment["content"]
            if isinstance(content, (bytes, bytearray)):
                content = base64.b64encode(bytes(content)).decode("ascii")
            params["attachments"] = [{
                "filename": attachment.get("filename", "attachment"),
                "content": content,
            }]
        try:
            result = self._resend.Emails.send(params)
            email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
            logger.info("email sent via resend to=%s subject=%s id=%s", to, subject, email_id)
            return {"provider": "resend", "delivered": True, "status": "sent", "id": email_id}
        except Exception as exc:
            logger.error("resend send failed to=%s subject=%s error=%s", to, subject, exc)
            return {"provider": "resend", "delivered": False, "status": "send_failed"}


_REGISTRY: dict[str, EmailProvider] = {"noop": NoopEmailProvider()}

# Auto-register Resend when credentials are present (env-only, no hardcoding).
if settings.resend_api_key and settings.sender_email:
    try:
        _REGISTRY["resend"] = ResendEmailProvider(settings.resend_api_key, settings.sender_email)
        logger.info("Resend email provider registered (sender=%s)", settings.sender_email)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("failed to initialise Resend provider: %s", exc)


def register_provider(provider: EmailProvider) -> None:
    _REGISTRY[provider.name] = provider


def get_email_provider() -> EmailProvider:
    return _REGISTRY.get(settings.email_provider, _REGISTRY["noop"])


def send_email(*, to: str | None, subject: str, body: str,
               attachment_url: str | None = None, attachment: dict | None = None) -> dict:
    if not to:
        return {"provider": get_email_provider().name, "delivered": False, "status": "no_recipient"}
    return get_email_provider().send(to=to, subject=subject, body=body,
                                     attachment_url=attachment_url, attachment=attachment)
