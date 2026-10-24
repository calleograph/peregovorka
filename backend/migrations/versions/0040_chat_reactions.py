"""Реакции на сообщения корпоративных чатов

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-24 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0040'
down_revision: Union[str, None] = '0039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_reactions',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True, autoincrement=True),
        sa.Column('message_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.ForeignKey('chat_messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('emoji', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('message_id', 'user_id', name='uq_chat_reactions'),
    )
    op.create_index('ix_chat_reactions_message_id', 'chat_reactions', ['message_id'])


def downgrade() -> None:
    op.drop_table('chat_reactions')
