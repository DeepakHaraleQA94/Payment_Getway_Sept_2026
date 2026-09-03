"""line-level reconciliation & matching engine (additive)

Adds two NEW tables for the report-only reconciliation engine. No existing tables are altered.

Revision ID: c9d2e3f4a5b6
Revises: b8f1c2a3d4e5
Create Date: 2026-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d2e3f4a5b6"
down_revision: Union[str, None] = "b8f1c2a3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("updated_by", sa.String(length=120), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("total_lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discrepancy_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("run_ref", sa.String(length=160), nullable=True),
        sa.UniqueConstraint("tenant_id", "run_ref", name="uq_recon_run_tenant_ref"),
    )
    op.create_index("ix_reconciliation_runs_tenant_id", "reconciliation_runs", ["tenant_id"])

    op.create_table(
        "reconciliation_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("provider_txn_id", sa.String(length=160), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider_amount_minor", sa.Integer(), nullable=True),
        sa.Column("internal_amount_minor", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("provider_status", sa.String(length=40), nullable=True),
        sa.Column("internal_status", sa.String(length=40), nullable=True),
        sa.Column("detail", sa.String(length=300), nullable=True),
    )
    op.create_index("ix_reconciliation_items_run_id", "reconciliation_items", ["run_id"])
    op.create_index("ix_reconciliation_items_tenant_id", "reconciliation_items", ["tenant_id"])
    op.create_index("ix_reconciliation_items_outcome", "reconciliation_items", ["outcome"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_items_outcome", table_name="reconciliation_items")
    op.drop_index("ix_reconciliation_items_tenant_id", table_name="reconciliation_items")
    op.drop_index("ix_reconciliation_items_run_id", table_name="reconciliation_items")
    op.drop_table("reconciliation_items")
    op.drop_index("ix_reconciliation_runs_tenant_id", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
