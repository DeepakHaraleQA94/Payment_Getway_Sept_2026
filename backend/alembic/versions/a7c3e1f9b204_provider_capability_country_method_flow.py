"""provider capability: supported countries, methods, flows (additive)

Revision ID: a7c3e1f9b204
Revises: f4b2e8a1c530
Create Date: 2026-06-03 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a7c3e1f9b204'
down_revision: Union[str, None] = 'f4b2e8a1c530'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in ("supported_countries", "supported_methods", "supported_flows"):
        op.add_column(
            "payment_providers",
            sa.Column(col, postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                      server_default=sa.text("'[]'::jsonb")),
        )


def downgrade() -> None:
    for col in ("supported_flows", "supported_methods", "supported_countries"):
        op.drop_column("payment_providers", col)
