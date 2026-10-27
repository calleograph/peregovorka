"""Журнал событий безопасности

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-27 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0046'
down_revision: Union[str, None] = '0045'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'security_events',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True, autoincrement=True),
        sa.Column('at', TS, nullable=False),
        sa.Column('ip', sa.String(64)),
        sa.Column('account', sa.String(200)),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('result', sa.String(16), nullable=False, server_default=''),
        sa.Column('reason', sa.String(300)),
        sa.Column('rule', sa.String(80)),
        sa.Column('until', TS),
        sa.Column('component', sa.String(24), nullable=False, server_default=''),
        sa.Column('details', JSON),
    )
    op.create_index('ix_security_events_at', 'security_events', ['at'])
    op.create_index('ix_security_events_ip_at', 'security_events', ['ip', 'at'])
    op.create_index('ix_security_events_kind_at', 'security_events', ['kind', 'at'])


def downgrade() -> None:
    op.drop_table('security_events')
