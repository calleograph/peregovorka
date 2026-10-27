"""Доверенные подсети: разрешённые клиентские адреса и подсети

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-27 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0045'
down_revision: Union[str, None] = '0044'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'allowed_networks',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('cidr', sa.String(64), nullable=False, unique=True),
        sa.Column('comment', sa.String(300), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('updated_at', TS, nullable=False),
        sa.Column('updated_by', sa.String(200), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_table('allowed_networks')
