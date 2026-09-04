"""explicit payment_method + flow columns on payments

Revision ID: c1a2b3d4e5f6
Revises: b7d4e1a9c260
Create Date: 2026-06 real-provider readiness

Additive only: adds nullable payment_method + flow columns and backfills them from the existing
metadata_json (method/flow already recorded at create-time). Legacy rows lacking metadata.method
are backfilled once with a provider heuristic (demo_upi -> upi, else card) so downstream logic
never needs to infer the rail from provider_key again.
"""
from alembic import op
import sqlalchemy as sa

revision = "c1a2b3d4e5f6"
down_revision = "b7d4e1a9c260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("payment_method", sa.String(length=30), nullable=True))
    op.add_column("payments", sa.Column("flow", sa.String(length=20), nullable=True))
    # Backfill from existing metadata first.
    op.execute("UPDATE payments SET payment_method = metadata_json->>'method' "
               "WHERE payment_method IS NULL AND metadata_json ? 'method'")
    op.execute("UPDATE payments SET flow = metadata_json->>'flow' "
               "WHERE flow IS NULL AND metadata_json ? 'flow'")
    # One-time heuristic only for legacy rows with no recorded method.
    op.execute("UPDATE payments SET payment_method = "
               "CASE WHEN provider_key = 'demo_upi' THEN 'upi' ELSE 'card' END "
               "WHERE payment_method IS NULL")
    op.execute("UPDATE payments SET flow = 'direct' WHERE flow IS NULL")


def downgrade() -> None:
    op.drop_column("payments", "flow")
    op.drop_column("payments", "payment_method")
