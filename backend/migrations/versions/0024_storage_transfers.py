"""перенос записей между хранилищами: задания и состояние каждого файла

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-11 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'storage_transfers',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('direction', sa.String(12), nullable=False),
        sa.Column('scope', sa.String(10), nullable=False, server_default='all'),
        sa.Column('meeting_id', sa.Uuid(), sa.ForeignKey('meetings.id', ondelete='SET NULL')),
        sa.Column('state', sa.String(12), nullable=False, server_default='queued', index=True),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('done', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bytes_total', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bytes_done', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(500)),
        sa.Column('created_by', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_at', _UTC, nullable=False, index=True),
        sa.Column('started_at', _UTC),
        sa.Column('finished_at', _UTC),
    )
    op.create_table(
        'storage_transfer_items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('transfer_id', sa.Uuid(), sa.ForeignKey('storage_transfers.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('recording_id', sa.Uuid(), nullable=False),
        sa.Column('state', sa.String(10), nullable=False, server_default='pending'),
        sa.Column('error', sa.String(500)),
        sa.Column('bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cleanup_at', _UTC),
    )
    op.create_index('ix_storage_transfer_items_state', 'storage_transfer_items', ['transfer_id', 'state'])


def downgrade() -> None:
    op.drop_index('ix_storage_transfer_items_state', table_name='storage_transfer_items')
    op.drop_table('storage_transfer_items')
    op.drop_table('storage_transfers')
