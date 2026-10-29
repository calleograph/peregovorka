"""Трансляции: просмотр записи эфира зрителями (разрешает владелец)

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-29 07:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0051'
down_revision: Union[str, None] = '0050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('live_replay', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.drop_column('live_replay')
