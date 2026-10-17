"""равноправные временные комнаты: режим доступа

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-17 23:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('peer_access', sa.String(8), nullable=False, server_default='link'))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('peer_access')
