"""Payment Acceptance Accounts: merchant/tenant-owned payment RECEIVING destinations.

This is intentionally SEPARATE from `PaymentProvider` (an external PSP/provider integration).
An acceptance account is a destination/configuration (e.g. a UPI VPA the merchant collects into);
it does NOT itself process a transaction. A future authorized UPI provider plugin may reference an
eligible acceptance account to route a collection. Never stores provider API secrets.
"""
import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import AuditMixin, TimestampMixin, UUIDPkMixin


class PaymentAcceptanceAccount(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """A tenant-owned payment acceptance destination (e.g. a UPI VPA)."""
    __tablename__ = "payment_acceptance_accounts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, default="upi")  # upi (extensible)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Optional link to a provider plugin key that would collect into this destination (no secret here).
    provider_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_holder_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    upi_vpa: Mapped[str | None] = mapped_column(String(256), nullable=True)  # routing identity, not a secret
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    environment: Mapped[str] = mapped_column(String(20), nullable=False, default="sandbox")  # sandbox|live
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    # Only ever set by a REAL verification mechanism; defaults to unverified. No fake verification.
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # Prevent exact duplicates for the same tenant + VPA + environment.
        UniqueConstraint("tenant_id", "upi_vpa", "environment", name="uq_acceptance_tenant_vpa_env"),
        CheckConstraint("environment in ('sandbox','live')", name="ck_acceptance_environment"),
        CheckConstraint(
            "verification_status in ('unverified','pending','verified','rejected')",
            name="ck_acceptance_verification"),
        Index("ix_acceptance_tenant_priority", "tenant_id", "priority"),
    )
