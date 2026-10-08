"""локальный администратор; профили LDAP; CA-сертификаты; почта (профили, очередь, доставка комнаты); сверка хранилищ

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('auth_source', sa.String(length=8), server_default='ad', nullable=False))
        batch_op.add_column(sa.Column('password_hash', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mail_delivery', sa.JSON(), nullable=True))

    for table in ('recordings', 'protocols', 'meeting_chat_attachments'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('file_state', sa.String(length=12), server_default='ok', nullable=False))
            batch_op.add_column(sa.Column('file_checked_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table('ldap_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('host', sa.String(length=253), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('protocol', sa.String(length=10), server_default='ldaps', nullable=False),
    sa.Column('base_dn', sa.String(length=500), nullable=False),
    sa.Column('upn_suffix', sa.String(length=253), server_default='', nullable=False),
    sa.Column('netbios_domain', sa.String(length=64), server_default='', nullable=False),
    sa.Column('timeout_s', sa.Integer(), nullable=False),
    sa.Column('bind_dn', sa.String(length=500), nullable=False),
    sa.Column('secret_enc', sa.Text(), server_default='', nullable=False),
    sa.Column('login_attribute', sa.String(length=64), server_default='sAMAccountName', nullable=False),
    sa.Column('display_name_attribute', sa.String(length=64), server_default='displayName', nullable=False),
    sa.Column('email_attribute', sa.String(length=64), server_default='mail', nullable=False),
    sa.Column('use_for_users', sa.Boolean(), nullable=False),
    sa.Column('use_for_admins', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    op.create_table('ca_certificates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('subject', sa.String(length=1000), nullable=False),
    sa.Column('issuer', sa.String(length=1000), nullable=False),
    sa.Column('serial', sa.String(length=100), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('not_before', sa.DateTime(timezone=True), nullable=False),
    sa.Column('not_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_ca', sa.Boolean(), nullable=False),
    sa.Column('self_signed', sa.Boolean(), nullable=False),
    sa.Column('pem', sa.Text(), nullable=False),
    sa.Column('added_by', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256')
    )

    op.create_table('mail_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('host', sa.String(length=253), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('security', sa.String(length=10), server_default='starttls', nullable=False),
    sa.Column('auth_type', sa.String(length=10), server_default='none', nullable=False),
    sa.Column('username', sa.String(length=320), server_default='', nullable=False),
    sa.Column('secret_enc', sa.Text(), server_default='', nullable=False),
    sa.Column('from_address', sa.String(length=320), nullable=False),
    sa.Column('from_name', sa.String(length=200), server_default='', nullable=False),
    sa.Column('timeout_s', sa.Integer(), nullable=False),
    sa.Column('verify_cert', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    op.create_table('mail_messages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=True),
    sa.Column('meeting_id', sa.Uuid(), nullable=True),
    sa.Column('room_name', sa.String(length=200), nullable=True),
    sa.Column('recipient', sa.String(length=320), nullable=False),
    sa.Column('recipient_name', sa.String(length=300), nullable=True),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('kinds', sa.JSON(), nullable=True),
    sa.Column('trigger', sa.String(length=10), nullable=False),
    sa.Column('requested_by', sa.String(length=300), nullable=True),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_error', sa.String(length=600), nullable=True),
    sa.Column('delivery', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sending_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mail_messages_batch_id'), ['batch_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mail_messages_meeting_id'), ['meeting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_mail_messages_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_mail_messages_next_attempt_at'), ['next_attempt_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_mail_messages_created_at'), ['created_at'], unique=False)

    op.create_table('storage_sync_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trigger', sa.String(length=10), nullable=False),
    sa.Column('actor', sa.String(length=300), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checked', sa.Integer(), nullable=False),
    sa.Column('missing', sa.Integer(), nullable=False),
    sa.Column('restored', sa.Integer(), nullable=False),
    sa.Column('orphans', sa.Integer(), nullable=False),
    sa.Column('unavailable', sa.Integer(), nullable=False),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('storage_sync_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_storage_sync_runs_started_at'), ['started_at'], unique=False)


def downgrade() -> None:
    op.drop_table('storage_sync_runs')
    op.drop_table('mail_messages')
    op.drop_table('mail_profiles')
    op.drop_table('ca_certificates')
    op.drop_table('ldap_profiles')
    for table in ('meeting_chat_attachments', 'protocols', 'recordings'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('file_checked_at')
            batch_op.drop_column('file_state')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('mail_delivery')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_changed_at')
        batch_op.drop_column('must_change_password')
        batch_op.drop_column('password_hash')
        batch_op.drop_column('auth_source')
