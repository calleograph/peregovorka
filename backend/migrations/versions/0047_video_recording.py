"""Видеозапись встреч (LiveKit Egress): режим и качество в комнате, признак у встречи, идентификатор задачи Egress у записи

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-28 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0047'
down_revision: Union[str, None] = '0046'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('video_mode', sa.String(8), nullable=False, server_default='off'))
        b.add_column(sa.Column('video_quality', sa.String(8), nullable=False, server_default='720p'))
    with op.batch_alter_table('meetings') as b:
        b.add_column(sa.Column('record_video', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    with op.batch_alter_table('recordings') as b:
        b.add_column(sa.Column('egress_id', sa.String(64), nullable=True))
        b.create_index('ix_recordings_egress_id', ['egress_id'])


def downgrade() -> None:
    with op.batch_alter_table('recordings') as b:
        b.drop_index('ix_recordings_egress_id')
        b.drop_column('egress_id')
    with op.batch_alter_table('meetings') as b:
        b.drop_column('record_video')
    with op.batch_alter_table('rooms') as b:
        b.drop_column('video_quality')
        b.drop_column('video_mode')
