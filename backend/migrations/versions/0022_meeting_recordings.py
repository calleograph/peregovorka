"""общая запись встречи: тип, формат, статус и время начала записи; режим общей записи комнаты

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-11 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0022'
down_revision: Union[str, None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table('recordings') as b:
        b.add_column(sa.Column('kind', sa.String(12), nullable=False, server_default='participant'))
        b.add_column(sa.Column('mime', sa.String(60)))
        b.add_column(sa.Column('status', sa.String(12), nullable=False, server_default='ready'))
        b.add_column(sa.Column('error', sa.String(300)))
        b.add_column(sa.Column('started_at', _UTC))
        b.add_column(sa.Column('sha256', sa.String(64)))
        b.add_column(sa.Column('has_video', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('recording_mode', sa.String(12), nullable=False, server_default='audio'))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.drop_column('recording_mode')
    with op.batch_alter_table('recordings') as b:
        for c in ('has_video', 'sha256', 'started_at', 'error', 'status', 'mime', 'kind'):
            b.drop_column(c)
