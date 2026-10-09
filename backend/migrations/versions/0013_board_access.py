"""кто работает с общей доской: уровень доступа комнаты

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-15 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('board_access', sa.String(length=10), server_default='auto', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('board_access')
