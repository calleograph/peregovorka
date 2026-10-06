"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-06 09:15:57.079073
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Первая версия схемы (создана autogenerate, вычитана вручную; типы приведены к переносимым).
    op.create_table('app_settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('is_secret', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('rooms',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_enabled', sa.Boolean(), nullable=False),
    sa.Column('max_participants', sa.Integer(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('transcription_enabled', sa.Boolean(), nullable=False),
    sa.Column('record_audio', sa.Boolean(), nullable=False),
    sa.Column('camera_allowed', sa.Boolean(), nullable=False),
    sa.Column('screen_share_allowed', sa.Boolean(), nullable=False),
    sa.Column('text_retention_days', sa.Integer(), nullable=True),
    sa.Column('audio_retention_days', sa.Integer(), nullable=True),
    sa.Column('protocol_instructions', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('ad_guid', sa.String(length=64), nullable=False),
    sa.Column('sam_account_name', sa.String(length=256), nullable=False),
    sa.Column('upn', sa.String(length=320), nullable=True),
    sa.Column('display_name', sa.String(length=300), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_is_admin', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ad_guid')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_sam_account_name'), ['sam_account_name'], unique=False)

    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('actor_name', sa.String(length=300), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=40), nullable=False),
    sa.Column('target_id', sa.String(length=80), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_at'), ['at'], unique=False)

    op.create_table('meetings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('livekit_room', sa.String(length=80), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_reason', sa.String(length=40), nullable=True),
    sa.Column('started_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('empty_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('transcription_enabled', sa.Boolean(), nullable=False),
    sa.Column('record_audio', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['started_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('livekit_room')
    )
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_meetings_ended_at'), ['ended_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_meetings_room_id'), ['room_id'], unique=False)
        batch_op.create_index('uq_meetings_one_active_per_room', ['room_id'], unique=True, postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))

    op.create_table('room_acl',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('subject_type', sa.String(length=10), nullable=False),
    sa.Column('subject_ref', sa.String(length=512), nullable=False),
    sa.Column('display_name', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('room_id', 'subject_type', 'subject_ref', name='uq_room_acl_subject')
    )
    with op.batch_alter_table('room_acl', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_room_acl_room_id'), ['room_id'], unique=False)

    op.create_table('meeting_participants',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('meeting_participants', schema=None) as batch_op:
        batch_op.create_index('ix_meeting_participants_meeting_user', ['meeting_id', 'user_id'], unique=False)

    op.create_table('transcript_segments',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('segment_uid', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('participant_identity', sa.String(length=80), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=True),
    sa.Column('model', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('metrics', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('segment_uid')
    )
    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.create_index('ix_segments_meeting_started', ['meeting_id', 'started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_transcript_segments_room_id'), ['room_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transcript_segments_user_id'), ['user_id'], unique=False)



def downgrade() -> None:
    # Первая версия схемы (создана autogenerate, вычитана вручную; типы приведены к переносимым).
    with op.batch_alter_table('transcript_segments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transcript_segments_user_id'))
        batch_op.drop_index(batch_op.f('ix_transcript_segments_room_id'))
        batch_op.drop_index('ix_segments_meeting_started')

    op.drop_table('transcript_segments')
    with op.batch_alter_table('meeting_participants', schema=None) as batch_op:
        batch_op.drop_index('ix_meeting_participants_meeting_user')

    op.drop_table('meeting_participants')
    with op.batch_alter_table('room_acl', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_room_acl_room_id'))

    op.drop_table('room_acl')
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.drop_index('uq_meetings_one_active_per_room', postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_meetings_room_id'))
        batch_op.drop_index(batch_op.f('ix_meetings_ended_at'))

    op.drop_table('meetings')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_at'))
        batch_op.drop_index(batch_op.f('ix_audit_log_action'))

    op.drop_table('audit_log')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_sam_account_name'))

    op.drop_table('users')
    op.drop_table('rooms')
    op.drop_table('app_settings')
