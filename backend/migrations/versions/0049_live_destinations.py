"""Трансляции: внешние площадки (RTMP/RTMPS) комнаты

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-29 04:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0049'
down_revision: Union[str, None] = '0048'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'live_destinations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('platform', sa.String(10), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('server_url', sa.String(400), nullable=False),
        sa.Column('key_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('channel', sa.String(160), nullable=True),
        sa.Column('watch_url', sa.String(400), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(12), nullable=False, server_default='idle'),
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_live_destinations_room_id', 'live_destinations', ['room_id'])


def downgrade() -> None:
    op.drop_table('live_destinations')
