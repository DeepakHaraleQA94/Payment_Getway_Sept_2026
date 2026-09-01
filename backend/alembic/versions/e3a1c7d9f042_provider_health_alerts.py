"""provider health alerts

Revision ID: e3a1c7d9f042
Revises: d2f4a7c9b310
Create Date: 2026-06-01 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3a1c7d9f042'
down_revision: Union[str, None] = 'd2f4a7c9b310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'provider_alerts',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('provider_account_id', sa.UUID(), nullable=False),
        sa.Column('provider_key', sa.String(length=60), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('success_rate', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_alert_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_account_id', name='uq_provider_alert_account'),
    )
    op.create_index(op.f('ix_provider_alerts_tenant_id'), 'provider_alerts', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_provider_alerts_tenant_id'), table_name='provider_alerts')
    op.drop_table('provider_alerts')
