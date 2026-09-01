"""Shared model mixins and enums for tenant-aware, audited, timestamped tables."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=utcnow
    )


class AuditMixin:
    """Who created / last modified the row (nullable for system actions)."""
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UUIDPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TenantMixin:
    """Adds tenant_id FK for tenant-scoped isolation."""
    @staticmethod
    def tenant_fk() -> Mapped[uuid.UUID]:  # pragma: no cover - helper doc only
        ...


# ---- Enumerations (stored as strings for migration portability) ----
class UserStatus(str, enum.Enum):
    active = "active"
    invited = "invited"
    suspended = "suspended"


class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    pending = "pending"


class PaymentStatus(str, enum.Enum):
    created = "created"
    pending = "pending"
    authorized = "authorized"
    captured = "captured"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"
    partially_refunded = "partially_refunded"
    cancelled = "cancelled"


class RefundStatus(str, enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"


class SettlementStatus(str, enum.Enum):
    open = "open"
    processing = "processing"
    settled = "settled"
    failed = "failed"


class LedgerDirection(str, enum.Enum):
    credit = "credit"
    debit = "debit"


class KycStatus(str, enum.Enum):
    not_started = "not_started"
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
