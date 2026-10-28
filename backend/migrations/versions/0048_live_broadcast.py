"""Трансляции: настройки комнаты (ссылки зрителя и потока, пароль зрителя, режим и качество) и таблица эфиров

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-29 01:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0048'
down_revision: Union[str, None] = '0047'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('live_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('live_start', sa.String(8), nullable=False, server_default='manual'))
        b.add_column(sa.Column('live_quality', sa.String(8), nullable=False, server_default='720p'))
        b.add_column(sa.Column('live_record', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        b.add_column(sa.Column('live_own_hls', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        b.add_column(sa.Column('live_title', sa.String(200), nullable=True))
        b.add_column(sa.Column('live_starts_at', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('live_token', sa.String(64), nullable=True))
        b.add_column(sa.Column('live_view_access', sa.String(40), nullable=True))
        b.add_column(sa.Column('live_stream_access', sa.String(40), nullable=True))
        b.add_column(sa.Column('live_password_hash', sa.String(255), nullable=True))
        b.add_column(sa.Column('live_password_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.create_unique_constraint('uq_rooms_live_token', ['live_token'])
        b.create_unique_constraint('uq_rooms_live_view_access', ['live_view_access'])
        b.create_unique_constraint('uq_rooms_live_stream_access', ['live_stream_access'])
    op.create_table(
        'broadcasts',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('meeting_id', sa.Uuid(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(10), nullable=False, server_default='starting'),
        sa.Column('egress_id', sa.String(64), nullable=True),
        sa.Column('hls_dir', sa.String(40), nullable=True),
        sa.Column('quality', sa.String(8), nullable=False, server_default='720p'),
        sa.Column('started_by', sa.String(200), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('live_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(300), nullable=True),
        sa.Column('peak_viewers', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hls_cleaned', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.create_index('ix_broadcasts_room_id', 'broadcasts', ['room_id'])
    op.create_index('ix_broadcasts_meeting_id', 'broadcasts', ['meeting_id'])
    op.create_index('ix_broadcasts_status', 'broadcasts', ['status'])
    op.create_index('ix_broadcasts_egress_id', 'broadcasts', ['egress_id'])


def downgrade() -> None:
    op.drop_table('broadcasts')
    with op.batch_alter_table('rooms') as b:
        for name in ('uq_rooms_live_stream_access', 'uq_rooms_live_view_access', 'uq_rooms_live_token'):
            b.drop_constraint(name, type_='unique')
        for col in ('live_password_enabled', 'live_password_hash', 'live_stream_access', 'live_view_access', 'live_token', 'live_starts_at', 'live_title',
                    'live_own_hls', 'live_record', 'live_quality', 'live_start', 'live_enabled'):
            b.drop_column(col)
