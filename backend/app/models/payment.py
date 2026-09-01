"""Payment engine: providers, payments/transactions and refunds."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import AuditMixin, TimestampMixin, UUIDPkMixin


class PaymentProvider(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """Provider/plugin adapter configuration, per tenant."""
    __tablename__ = "payment_providers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_key: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. "mock" or any registered plugin
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="sandbox")  # sandbox|live
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    supported_currencies: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    credentials_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # ref, never raw secret
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_key", name="uq_provider_tenant_key"),
        CheckConstraint("mode in ('sandbox','live')", name="ck_provider_mode"),
    )


class Payment(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "payments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(64), nullable=False)  # human/merchant reference
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_key: Mapped[str] = mapped_column(String(60), nullable=False, default="mock")
    provider_txn_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)  # amount in minor units
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    fee_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="created", index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    refunds = relationship("Refund", back_populates="payment", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_tenant_idem"),
        Index("ix_payments_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("amount_minor >= 0", name="ck_payment_amount_nonneg"),
    )


class Refund(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "refunds"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_refund_id: Mapped[str | None] = mapped_column(String(160), nullable=True)

    payment = relationship("Payment", back_populates="refunds")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_refund_tenant_idem"),
        CheckConstraint("amount_minor > 0", name="ck_refund_amount_pos"),
    )
