"""provider account management: per-environment accounts, secret store, payment environment

Revision ID: d2f4a7c9b310
Revises: c1e050d6bc48
Create Date: 2026-06-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f4a7c9b310'
down_revision: Union[str, None] = 'c1e050d6bc48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-environment provider accounts: one row per (tenant, provider_key, mode).
    op.drop_constraint('uq_provider_tenant_key', 'payment_providers', type_='unique')
    op.create_unique_constraint(
        'uq_provider_tenant_key_mode', 'payment_providers',
        ['tenant_id', 'provider_key', 'mode'])

    # Secret store: encrypted-at-rest credential blobs, referenced by opaque ref.
    op.create_table(
        'provider_secrets',
        sa.Column('ref', sa.String(length=200), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('ciphertext', sa.Text(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ref', name='uq_provider_secret_ref'),
    )
    op.create_index(op.f('ix_provider_secrets_tenant_id'), 'provider_secrets', ['tenant_id'], unique=False)

    # Payments record which environment they executed in.
    op.add_column('payments', sa.Column('environment', sa.String(length=20), nullable=False,
                                        server_default='sandbox'))
    op.create_check_constraint('ck_payment_environment', 'payments',
                               "environment in ('sandbox','live')")


def downgrade() -> None:
    op.drop_constraint('ck_payment_environment', 'payments', type_='check')
    op.drop_column('payments', 'environment')
    op.drop_index(op.f('ix_provider_secrets_tenant_id'), table_name='provider_secrets')
    op.drop_table('provider_secrets')
    op.drop_constraint('uq_provider_tenant_key_mode', 'payment_providers', type_='unique')
    op.create_unique_constraint('uq_provider_tenant_key', 'payment_providers',
                                ['tenant_id', 'provider_key'])
