"""Реакции на сообщения чата встречи (сотрудники и гости)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-24 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0041'
down_revision: Union[str, None] = '0040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BIG = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'meeting_chat_reactions',
        sa.Column('id', BIG, primary_key=True, autoincrement=True),
        sa.Column('message_id', BIG, sa.ForeignKey('meeting_chat_messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_type', sa.String(8), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=False),
        sa.Column('actor_name', sa.String(300), nullable=False),
        sa.Column('emoji', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('message_id', 'actor_type', 'actor_id', name='uq_meeting_chat_reactions'),
    )
    op.create_index('ix_meeting_chat_reactions_message_id', 'meeting_chat_reactions', ['message_id'])


def downgrade() -> None:
    op.drop_table('meeting_chat_reactions')
