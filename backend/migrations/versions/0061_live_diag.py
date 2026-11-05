"""Диагностика эфиров: журнал, показатели, итог эфира

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-05 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0061'
down_revision: Union[str, None] = '0060'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
T = sa.DateTime(timezone=True)
ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    with op.batch_alter_table('broadcasts') as t:
        t.add_column(sa.Column('diag_summary', J))
    op.create_table(
        'live_diag_events',
        sa.Column('id', ID, primary_key=True, autoincrement=True),
        sa.Column('broadcast_id', sa.Uuid(), sa.ForeignKey('broadcasts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('at', T, nullable=False),
        sa.Column('component', sa.String(16), nullable=False),
        sa.Column('level', sa.String(8), nullable=False, server_default='info'),
        sa.Column('code', sa.String(48), nullable=False),
        sa.Column('message', sa.String(400), nullable=False),
        sa.Column('cause', sa.String(400)),
        sa.Column('cause_kind', sa.String(10)),
        sa.Column('details', J),
    )
    op.create_index('ix_live_diag_events_broadcast_id', 'live_diag_events', ['broadcast_id'])
    op.create_index('ix_live_diag_events_at', 'live_diag_events', ['at'])
    op.create_table(
        'live_diag_samples',
        sa.Column('id', ID, primary_key=True, autoincrement=True),
        sa.Column('broadcast_id', sa.Uuid(), sa.ForeignKey('broadcasts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('at', T, nullable=False),
        sa.Column('data', J, nullable=False),
    )
    op.create_index('ix_live_diag_samples_broadcast_id', 'live_diag_samples', ['broadcast_id'])


def downgrade() -> None:
    op.drop_table('live_diag_samples')
    op.drop_table('live_diag_events')
    with op.batch_alter_table('broadcasts') as t:
        t.drop_column('diag_summary')
