"""шифрование хранимых чата и стенограмм: ключи данных и режим комнаты

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-18 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0030'
down_revision: Union[str, None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'storage_keys',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('wrapped', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('retired_at', sa.DateTime(timezone=True)),
    )
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('encrypt_storage', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('encrypt_storage')
    op.drop_table('storage_keys')
