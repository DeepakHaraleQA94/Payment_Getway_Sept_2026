"""Tenant / client management model."""
import uuid

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import AuditMixin, TimestampMixin, UUIDPkMixin


class Tenant(UUIDPkMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_platform: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    brand_accent: Mapped[str] = mapped_column(String(9), nullable=False, default="#3B82F6")
    brand_logo_file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_tenants_status", "status"),)
