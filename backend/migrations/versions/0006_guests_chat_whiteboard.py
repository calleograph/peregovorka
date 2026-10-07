"""гостевой доступ, чат встречи, общая доска

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Только добавление: существующие комнаты остаются без гостевого доступа, встречи — без чата и доски.
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('guest_access_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('guest_token', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_rooms_guest_token', ['guest_token'])

    op.create_table('guest_participants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('participant_type', sa.String(length=10), server_default='guest', nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('client', sa.String(length=160), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('guest_participants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_guest_participants_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_guest_participants_room_id'), ['room_id'], unique=False)

    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('guest_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_segments_guest', 'guest_participants', ['guest_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_transcript_segments_guest_id'), ['guest_id'], unique=False)

    op.create_table('meeting_chat_messages',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('author_type', sa.String(length=10), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('guest_id', sa.Uuid(), nullable=True),
    sa.Column('author_name', sa.String(length=300), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['guest_id'], ['guest_participants.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('meeting_chat_messages', schema=None) as batch_op:
        batch_op.create_index('ix_chat_meeting_id', ['meeting_id', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_chat_messages_created_at'), ['created_at'], unique=False)

    op.create_table('meeting_whiteboards',
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('xml', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('shapes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('meeting_id')
    )


def downgrade() -> None:
    op.drop_table('meeting_whiteboards')
    with op.batch_alter_table('meeting_chat_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_meeting_chat_messages_created_at'))
        batch_op.drop_index('ix_chat_meeting_id')
    op.drop_table('meeting_chat_messages')
    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transcript_segments_guest_id'))
        batch_op.drop_constraint('fk_segments_guest', type_='foreignkey')
        batch_op.drop_column('guest_id')
    with op.batch_alter_table('guest_participants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_guest_participants_room_id'))
        batch_op.drop_index(batch_op.f('ix_guest_participants_meeting_id'))
    op.drop_table('guest_participants')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_constraint('uq_rooms_guest_token', type_='unique')
        batch_op.drop_column('guest_token')
        batch_op.drop_column('guest_access_enabled')
