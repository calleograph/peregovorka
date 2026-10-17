"""журнал телефонных вызовов SIP

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-18 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0032'
down_revision: Union[str, None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sip_calls',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('direction', sa.String(8), nullable=False),
        sa.Column('room_id', sa.Uuid()),
        sa.Column('room_name', sa.String(200)),
        sa.Column('meeting_id', sa.Uuid()),
        sa.Column('guest_id', sa.Uuid()),
        sa.Column('initiator', sa.String(200)),
        sa.Column('initiator_user_id', sa.Uuid()),
        sa.Column('number', sa.String(64), nullable=False, server_default=''),
        sa.Column('profile_name', sa.String(120)),
        sa.Column('result', sa.String(12), nullable=False, server_default='dialing'),
        sa.Column('sip_status', sa.Integer()),
        sa.Column('reason', sa.String(300)),
        sa.Column('tech_id', sa.String(160)),
        sa.Column('answered_at', sa.DateTime(timezone=True)),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        sa.Column('duration_s', sa.Integer()),
        sa.Column('end_reason', sa.String(20)),
    )
    op.create_index('ix_sip_calls_started_at', 'sip_calls', ['started_at'])
    op.create_index('ix_sip_calls_room_id', 'sip_calls', ['room_id'])
    op.create_index('ix_sip_calls_guest_id', 'sip_calls', ['guest_id'])
    op.create_index('ix_sip_calls_initiator_user_id', 'sip_calls', ['initiator_user_id'])


def downgrade() -> None:
    op.drop_table('sip_calls')
