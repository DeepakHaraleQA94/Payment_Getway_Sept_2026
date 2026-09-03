"""payment acceptance accounts

Revision ID: b7d4e1a9c260
Revises: c9d2e3f4a5b6
Create Date: 2026-06-03 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d4e1a9c260'
down_revision: Union[str, None] = 'c9d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payment_acceptance_accounts',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('account_type', sa.String(length=20), nullable=False),
        sa.Column('display_name', sa.String(length=120), nullable=False),
        sa.Column('provider_key', sa.String(length=60), nullable=True),
        sa.Column('bank_name', sa.String(length=120), nullable=True),
        sa.Column('account_holder_name', sa.String(length=160), nullable=True),
        sa.Column('upi_vpa', sa.String(length=256), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('environment', sa.String(length=20), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('verification_status', sa.String(length=20), nullable=False),
        sa.Column('config', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'upi_vpa', 'environment', name='uq_acceptance_tenant_vpa_env'),
        sa.CheckConstraint("environment in ('sandbox','live')", name='ck_acceptance_environment'),
        sa.CheckConstraint(
            "verification_status in ('unverified','pending','verified','rejected')",
            name='ck_acceptance_verification'),
    )
    op.create_index(op.f('ix_payment_acceptance_accounts_tenant_id'),
                    'payment_acceptance_accounts', ['tenant_id'], unique=False)
    op.create_index('ix_acceptance_tenant_priority', 'payment_acceptance_accounts',
                    ['tenant_id', 'priority'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_acceptance_tenant_priority', table_name='payment_acceptance_accounts')
    op.drop_index(op.f('ix_payment_acceptance_accounts_tenant_id'), table_name='payment_acceptance_accounts')
    op.drop_table('payment_acceptance_accounts')
