"""provider alert events history

Revision ID: f4b2e8a1c530
Revises: e3a1c7d9f042
Create Date: 2026-06-02 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4b2e8a1c530'
down_revision: Union[str, None] = 'e3a1c7d9f042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'provider_alert_events',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('provider_account_id', sa.UUID(), nullable=False),
        sa.Column('provider_key', sa.String(length=60), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('transition', sa.String(length=20), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('reason', sa.String(length=300), nullable=True),
        sa.Column('success_rate', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_provider_alert_events_tenant_id'), 'provider_alert_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_provider_alert_events_provider_account_id'), 'provider_alert_events',
                    ['provider_account_id'], unique=False)
    op.create_index('ix_provider_alert_events_tenant_created', 'provider_alert_events',
                    ['tenant_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_provider_alert_events_tenant_created', table_name='provider_alert_events')
    op.drop_index(op.f('ix_provider_alert_events_provider_account_id'), table_name='provider_alert_events')
    op.drop_index(op.f('ix_provider_alert_events_tenant_id'), table_name='provider_alert_events')
    op.drop_table('provider_alert_events')
