"""языковая модель и телефония (SIP) на уровне комнаты; настройки конкретной встречи; профили SIP; архив в рассылке

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-10 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('llm_mode', sa.String(length=10), server_default='inherit', nullable=False))
        batch_op.add_column(sa.Column('llm_local_model', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('sip_mode', sa.String(length=10), server_default='off', nullable=False))
        batch_op.add_column(sa.Column('sip_profile_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('sip_extension', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('sip_allow_inbound', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('sip_allow_outbound', sa.Boolean(), server_default=sa.text('false'), nullable=False))
        batch_op.add_column(sa.Column('sip_dispatch_rule_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('sip_contacts', sa.JSON(), nullable=True))
        batch_op.create_unique_constraint('uq_rooms_sip_extension', ['sip_extension'])
    # комнаты старых версий с собственным внешним профилем сохраняют выбор
    op.execute("UPDATE rooms SET llm_mode = 'profile' WHERE llm_profile_id IS NOT NULL")

    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_override', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('llm_override', sa.JSON(), nullable=True))

    with op.batch_alter_table('guest_participants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lk_identity', sa.String(length=160), nullable=True))
        batch_op.create_index('ix_guest_participants_lk_identity', ['lk_identity'], unique=False)

    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('options', sa.JSON(), nullable=True))

    op.create_table(
        'sip_profiles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('direction', sa.String(length=10), server_default='both', nullable=False),
        sa.Column('host', sa.String(length=253), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('transport', sa.String(length=8), server_default='udp', nullable=False),
        sa.Column('username', sa.String(length=200), server_default='', nullable=False),
        sa.Column('secret_enc', sa.Text(), server_default='', nullable=False),
        sa.Column('realm', sa.String(length=253), server_default='', nullable=False),
        sa.Column('caller_id', sa.String(length=64), server_default='', nullable=False),
        sa.Column('allowed_numbers', sa.JSON(), nullable=True),
        sa.Column('inbound_numbers', sa.JSON(), nullable=True),
        sa.Column('allowed_addresses', sa.JSON(), nullable=True),
        sa.Column('codecs', sa.JSON(), nullable=True),
        sa.Column('media_encryption', sa.String(length=10), server_default='disable', nullable=False),
        sa.Column('ring_timeout_s', sa.Integer(), server_default='45', nullable=False),
        sa.Column('lk_outbound_trunk_id', sa.String(length=100), nullable=True),
        sa.Column('lk_inbound_trunk_id', sa.String(length=100), nullable=True),
        sa.Column('last_check', sa.JSON(), nullable=True),
        sa.Column('last_check_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('sip_profiles')
    with op.batch_alter_table('mail_messages', schema=None) as batch_op:
        batch_op.drop_column('options')
    with op.batch_alter_table('guest_participants', schema=None) as batch_op:
        batch_op.drop_index('ix_guest_participants_lk_identity')
        batch_op.drop_column('lk_identity')
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.drop_column('llm_override')
        batch_op.drop_column('delivery_override')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_constraint('uq_rooms_sip_extension', type_='unique')
        for c in ('sip_contacts', 'sip_dispatch_rule_id', 'sip_allow_outbound', 'sip_allow_inbound', 'sip_extension', 'sip_profile_id', 'sip_mode', 'llm_local_model', 'llm_mode'):
            batch_op.drop_column(c)
