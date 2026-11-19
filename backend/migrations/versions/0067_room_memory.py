"""Память комнаты: сквозные темы, их события по встречам и состояние анализа; настройки комнаты memory_mode и memory_keep

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-19 23:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0067'
down_revision: Union[str, None] = '0066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('memory_mode', sa.String(10), server_default='off', nullable=False))
        b.add_column(sa.Column('memory_keep', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_table('room_memories',
                    sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('status', sa.String(12), nullable=False), sa.Column('error', sa.String(500)),
                    sa.Column('processed', sa.JSON()), sa.Column('overrides', sa.JSON()), sa.Column('feedback', sa.JSON()),
                    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('room_threads',
                    sa.Column('id', sa.Uuid(), primary_key=True),
                    sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('title', sa.String(200), nullable=False), sa.Column('summary', sa.String(600)), sa.Column('category', sa.String(20), nullable=False),
                    sa.Column('user_title', sa.String(200)), sa.Column('user_state', sa.String(20)), sa.Column('user_author', sa.String(200)), sa.Column('note', sa.String(1000)),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_room_threads_room_id', 'room_threads', ['room_id'])
    op.create_table('room_thread_events',
                    sa.Column('id', sa.Uuid(), primary_key=True),
                    sa.Column('thread_id', sa.Uuid(), sa.ForeignKey('room_threads.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('meeting_id', sa.Uuid(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('topic_id', sa.String(40), nullable=False), sa.Column('kind', sa.String(12), nullable=False), sa.Column('text', sa.String(600), nullable=False),
                    sa.Column('at', sa.DateTime(timezone=True), nullable=False), sa.Column('sec', sa.Integer()), sa.Column('speaker', sa.String(200)), sa.Column('quote', sa.String(300)),
                    sa.Column('assignee', sa.String(200)), sa.Column('deadline', sa.String(100)), sa.Column('segment_id', sa.Integer()),
                    sa.Column('source_gone', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index('ix_room_thread_events_thread_id', 'room_thread_events', ['thread_id'])
    op.create_index('ix_room_thread_events_meeting_id', 'room_thread_events', ['meeting_id'])
    op.create_index('ix_thread_events_room_at', 'room_thread_events', ['room_id', 'at'])


def downgrade() -> None:
    op.drop_table('room_thread_events')
    op.drop_table('room_threads')
    op.drop_table('room_memories')
    with op.batch_alter_table('rooms') as b:
        b.drop_column('memory_keep')
        b.drop_column('memory_mode')
