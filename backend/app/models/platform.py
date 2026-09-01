"""Compliance & platform: KYC/AML, FX rates, audit log, system config."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import AuditMixin, TimestampMixin, UUIDPkMixin


class KycRecord(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """KYC/AML boundary record. Regulated capability, behind feature flags."""
    __tablename__ = "kyc_records"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False, default="business")  # business|individual
    subject_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started", index=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class FxRate(UUIDPkMixin, TimestampMixin, Base):
    """FX engine reference rates (base -> quote)."""
    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="mock")
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "as_of", name="uq_fx_pair_asof"),
        Index("ix_fx_pair", "base_currency", "quote_currency"),
    )


class AuditLog(UUIDPkMixin, Base):
    """Append-only audit trail for all financial and admin mutations."""
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)


class SystemConfig(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """Configuration key/value store (platform-wide or tenant-scoped)."""
    __tablename__ = "system_configs"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_secret_ref: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_config_tenant_key"),)
