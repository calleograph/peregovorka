"""Почта и согласие гостя на материалы; зрители эфира и их вопросы; качество эфира новых комнат — 1080p

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-03 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0058'
down_revision: Union[str, None] = '0057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('guest_participants') as t:
        t.add_column(sa.Column('email', sa.String(254), nullable=True))
        t.add_column(sa.Column('materials_consent', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    with op.batch_alter_table('rooms') as t:
        t.alter_column('live_quality', existing_type=sa.String(8), server_default='1080p')      # прежние комнаты сохраняют своё значение
    op.create_table(
        'live_viewers',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('broadcast_id', sa.Uuid(), sa.ForeignKey('broadcasts.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('display_name', sa.String(120), nullable=False),
        sa.Column('email', sa.String(254), nullable=True),
        sa.Column('materials_consent', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'live_questions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('broadcast_id', sa.Uuid(), sa.ForeignKey('broadcasts.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('viewer_id', sa.Uuid(), sa.ForeignKey('live_viewers.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('author_name', sa.String(120), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(8), nullable=False, server_default='new'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('handled_by', sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('live_questions')
    op.drop_table('live_viewers')
    with op.batch_alter_table('rooms') as t:
        t.alter_column('live_quality', existing_type=sa.String(8), server_default='720p')
    with op.batch_alter_table('guest_participants') as t:
        t.drop_column('materials_consent')
        t.drop_column('email')
