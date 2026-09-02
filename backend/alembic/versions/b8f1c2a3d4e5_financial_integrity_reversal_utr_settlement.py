"""financial integrity: reversals, utr submissions, settlement idempotency ref

Additive-only migration for financial-safety closure:
- reversals: immutable per-payment reversal records (references original txn, one per payment).
- utr_submissions: bank-reference (UTR) verification records; credit only after manual confirm.
- settlements.provider_settlement_ref: stable provider settlement id for idempotent processing.

No existing tables/columns are dropped or altered destructively. No data is reset.

Revision ID: b8f1c2a3d4e5
Revises: a7c3e1f9b204
Create Date: 2026-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8f1c2a3d4e5"
down_revision: Union[str, None] = "a7c3e1f9b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reversals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("provider_ref", sa.String(length=160), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.UniqueConstraint("payment_id", name="uq_reversal_payment"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_reversal_tenant_idem"),
    )
    op.create_index("ix_reversals_tenant_id", "reversals", ["tenant_id"])
    op.create_index("ix_reversals_payment_id", "reversals", ["payment_id"])

    op.create_table(
        "utr_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("utr", sa.String(length=140), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="under_review"),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.UniqueConstraint("tenant_id", "utr", name="uq_utr_tenant_ref"),
        sa.CheckConstraint("amount_minor > 0", name="ck_utr_amount_pos"),
    )
    op.create_index("ix_utr_submissions_tenant_id", "utr_submissions", ["tenant_id"])
    op.create_index("ix_utr_submissions_payment_id", "utr_submissions", ["payment_id"])

    op.add_column("settlements",
                  sa.Column("provider_settlement_ref", sa.String(length=160), nullable=True))
    op.create_unique_constraint(
        "uq_settlement_tenant_provider_ref", "settlements",
        ["tenant_id", "provider_settlement_ref"])


def downgrade() -> None:
    op.drop_constraint("uq_settlement_tenant_provider_ref", "settlements", type_="unique")
    op.drop_column("settlements", "provider_settlement_ref")
    op.drop_index("ix_utr_submissions_payment_id", table_name="utr_submissions")
    op.drop_index("ix_utr_submissions_tenant_id", table_name="utr_submissions")
    op.drop_table("utr_submissions")
    op.drop_index("ix_reversals_payment_id", table_name="reversals")
    op.drop_index("ix_reversals_tenant_id", table_name="reversals")
    op.drop_table("reversals")
