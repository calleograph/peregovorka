"""Трансляции: собственная ссылка, параметры и запись у каждого эфира; скачивание и срок ссылки после эфира

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-31 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0053'
down_revision: Union[str, None] = '0052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('live_download', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('live_link_days', sa.Integer(), nullable=False, server_default='5'))
    with op.batch_alter_table('broadcasts') as b:
        b.add_column(sa.Column('view_token', sa.String(64), nullable=True))
        b.add_column(sa.Column('title', sa.String(200), nullable=True))
        b.add_column(sa.Column('password_hash', sa.String(255), nullable=True))
        b.add_column(sa.Column('replay', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('download', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('link_days', sa.Integer(), nullable=False, server_default='5'))
        b.add_column(sa.Column('access_until', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('recording_id', sa.Uuid(), nullable=True))
        b.create_index('ix_broadcasts_view_token', ['view_token'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('broadcasts') as b:
        b.drop_index('ix_broadcasts_view_token')
        for c in ('recording_id', 'revoked_at', 'access_until', 'link_days', 'download', 'replay', 'password_hash', 'title', 'view_token'):
            b.drop_column(c)
    with op.batch_alter_table('rooms') as b:
        b.drop_column('live_link_days')
        b.drop_column('live_download')
