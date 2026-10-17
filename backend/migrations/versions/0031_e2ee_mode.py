"""сквозное шифрование LiveKit: режим комнаты

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-18 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('e2ee_mode', sa.String(8), nullable=False, server_default='off'))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('e2ee_mode')
