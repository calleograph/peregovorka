"""Корпоративные чаты: переписки, участники и их личные настройки, сообщения, вложения, папки; личные настройки чатов у пользователя

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-24 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0039'
down_revision: Union[str, None] = '0038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
BIG = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table('users') as t:
        t.add_column(sa.Column('chat_prefs', JSON, nullable=True))
    op.create_table(
        'chats',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('title', sa.String(200)),
        sa.Column('direct_key', sa.String(80), unique=True),
        sa.Column('avatar_mime', sa.String(20)),
        sa.Column('avatar_updated_at', TS),
        sa.Column('created_by_user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('last_message_id', sa.BigInteger()),
        sa.Column('last_message_at', TS),
    )
    op.create_index('ix_chats_last_message_at', 'chats', ['last_message_at'])
    op.create_table(
        'chat_folders',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(60), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', TS, nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_chat_folders_name'),
    )
    op.create_index('ix_chat_folders_user_id', 'chat_folders', ['user_id'])
    op.create_table(
        'chat_members',
        sa.Column('id', BIG, primary_key=True, autoincrement=True),
        sa.Column('chat_id', sa.Uuid(), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(8), nullable=False, server_default='member'),
        sa.Column('joined_at', TS, nullable=False),
        sa.Column('last_read_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cleared_before_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('muted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('pinned_at', TS),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('folder_id', sa.Uuid(), sa.ForeignKey('chat_folders.id', ondelete='SET NULL')),
        sa.UniqueConstraint('chat_id', 'user_id', name='uq_chat_members'),
    )
    op.create_index('ix_chat_members_user', 'chat_members', ['user_id', 'chat_id'])
    op.create_table(
        'chat_messages',
        sa.Column('id', BIG, primary_key=True, autoincrement=True),
        sa.Column('chat_id', sa.Uuid(), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('author_name', sa.String(300), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False, server_default='text'),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('client_id', sa.String(64)),
        sa.Column('reply_to_id', sa.BigInteger()),
        sa.Column('forwarded', JSON),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('edited_at', TS),
        sa.Column('deleted_at', TS),
        sa.UniqueConstraint('chat_id', 'author_id', 'client_id', name='uq_chat_messages_client'),
    )
    op.create_index('ix_chat_messages_chat', 'chat_messages', ['chat_id', 'id'])
    op.create_index('ix_chat_messages_created_at', 'chat_messages', ['created_at'])
    if op.get_bind().dialect.name == 'postgresql':
        # полнотекстовый поиск по переписке (русская морфология; латиница — английская): индекс GIN по выражению, запросы — в services/chats.py
        op.execute("CREATE INDEX ix_chat_messages_fts ON chat_messages USING gin (to_tsvector('russian', text))")
    op.create_table(
        'chat_files',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('chat_id', sa.Uuid(), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False),
        sa.Column('message_id', BIG, sa.ForeignKey('chat_messages.id', ondelete='CASCADE')),
        sa.Column('uploader_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('mime', sa.String(120), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(8), nullable=False),
        sa.Column('storage_key', sa.String(500), nullable=False),
        sa.Column('profile_id', sa.Uuid()),
        sa.Column('file_state', sa.String(12), nullable=False, server_default='ok'),
        sa.Column('file_checked_at', TS),
        sa.Column('created_at', TS, nullable=False),
    )
    op.create_index('ix_chat_files_chat_id', 'chat_files', ['chat_id'])
    op.create_index('ix_chat_files_message_id', 'chat_files', ['message_id'])
    op.create_index('ix_chat_files_created_at', 'chat_files', ['created_at'])


def downgrade() -> None:
    op.drop_table('chat_files')
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("DROP INDEX IF EXISTS ix_chat_messages_fts")
    op.drop_table('chat_messages')
    op.drop_table('chat_members')
    op.drop_table('chat_folders')
    op.drop_table('chats')
    with op.batch_alter_table('users') as t:
        t.drop_column('chat_prefs')
