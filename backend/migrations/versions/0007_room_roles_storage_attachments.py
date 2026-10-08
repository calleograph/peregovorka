"""тип комнаты, автозапись, доска; профили хранилища; вложения чата

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Только добавление с безопасными значениями: существующие комнаты остаются обычными, без автозаписи, с доской для всех.
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('room_type', sa.String(length=16), server_default='regular', nullable=False))
        batch_op.add_column(sa.Column('auto_record', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('board_allowed', sa.Boolean(), server_default=sa.text('true'), nullable=False))

    op.create_table('storage_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('secret_enc', sa.Text(), server_default='', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    op.create_table('meeting_chat_attachments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('uploader_type', sa.String(length=10), nullable=False),
    sa.Column('uploader_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('mime', sa.String(length=120), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('storage_key', sa.String(length=500), nullable=False),
    sa.Column('profile_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['message_id'], ['meeting_chat_messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('meeting_chat_attachments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meeting_chat_attachments_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_chat_attachments_message_id'), ['message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_meeting_chat_attachments_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('meeting_chat_attachments')
    op.drop_table('storage_profiles')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('board_allowed')
        batch_op.drop_column('auto_record')
        batch_op.drop_column('room_type')
