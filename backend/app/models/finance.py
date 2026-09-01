"""Fee engine, turnover, balance & ledger, settlements."""
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import AuditMixin, TimestampMixin, UUIDPkMixin


class FeeRule(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """Fee engine rule: percentage + fixed component per provider/currency."""
    __tablename__ = "fee_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    percent_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # basis points
    fixed_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_fee_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    __table_args__ = (
        Index("ix_fee_rules_tenant_active", "tenant_id", "active"),
        CheckConstraint("percent_bps >= 0", name="ck_fee_percent_nonneg"),
    )


class LedgerAccount(UUIDPkMixin, TimestampMixin, Base):
    """Balance account per tenant + currency (double-entry balances)."""
    __tablename__ = "ledger_accounts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_type: Mapped[str] = mapped_column(String(30), nullable=False, default="available")  # available|reserved|fees
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    balance_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "account_type", "currency", name="uq_ledger_account"),
    )


class LedgerEntry(UUIDPkMixin, TimestampMixin, Base):
    """Immutable ledger entry (append-only)."""
    __tablename__ = "ledger_entries"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ledger_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(6), nullable=False)  # credit|debit
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    balance_after_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # payment|refund|fee|settlement
    ref_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        Index("ix_ledger_entries_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("direction in ('credit','debit')", name="ck_ledger_direction"),
    )


class TurnoverSnapshot(UUIDPkMixin, TimestampMixin, Base):
    """Turnover engine: aggregated daily volume per tenant + currency."""
    __tablename__ = "turnover_snapshots"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    gross_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fees_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunds_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "period_date", "currency", name="uq_turnover_period"),
    )


class Settlement(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    """Settlement / reconciliation batch."""
    __tablename__ = "settlements"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    gross_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fees_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_settlements_tenant_status", "tenant_id", "status"),)
