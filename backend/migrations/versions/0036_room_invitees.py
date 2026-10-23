"""Приглашённые на запланированные конференции (из события календаря Outlook)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-23 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0036'
down_revision: Union[str, None] = '0035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'room_invitees',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True, autoincrement=True),
        sa.Column('room_id', sa.Uuid(), sa.ForeignKey('rooms.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(254), nullable=False),
        sa.Column('name', sa.String(300), nullable=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('optional', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('source', sa.String(10), nullable=False, server_default='addin'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('room_id', 'email', name='uq_room_invitee_email'),
    )
    op.create_index('ix_room_invitees_room_id', 'room_invitees', ['room_id'])
    op.create_index('ix_room_invitees_email', 'room_invitees', ['email'])
    op.create_index('ix_room_invitees_user_id', 'room_invitees', ['user_id'])


def downgrade() -> None:
    op.drop_table('room_invitees')
