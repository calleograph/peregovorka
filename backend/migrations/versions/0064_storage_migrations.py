"""Перенос данных между хранилищами; запись помнит профиль, где лежит её файл

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-06 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0064'
down_revision: Union[str, None] = '0063'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
T = sa.DateTime(timezone=True)
U = sa.Uuid()


def upgrade() -> None:
    op.add_column('recordings', sa.Column('export_profile_id', U, nullable=True))
    op.create_index('ix_recordings_export_profile_id', 'recordings', ['export_profile_id'])
    op.create_table(
        'storage_migrations',
        sa.Column('id', U, primary_key=True),
        sa.Column('source_id', U, nullable=False),
        sa.Column('target_id', U, nullable=False),
        sa.Column('source_name', sa.String(120), server_default='', nullable=False),
        sa.Column('target_name', sa.String(120), server_default='', nullable=False),
        sa.Column('mode', sa.String(10), server_default='full', nullable=False),
        sa.Column('groups', J, nullable=False),
        sa.Column('state', sa.String(14), server_default='scanning', nullable=False),
        sa.Column('resume_state', sa.String(14)),
        sa.Column('total', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('done', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('failed', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('bytes_total', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('bytes_done', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('estimate', J),
        sa.Column('switched', J),
        sa.Column('report', J),
        sa.Column('events', J, nullable=False),
        sa.Column('error', sa.String(500)),
        sa.Column('cancel_requested', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_by', sa.String(200), server_default='', nullable=False),
        sa.Column('created_at', T, nullable=False),
        sa.Column('copy_started_at', T),
        sa.Column('switched_at', T),
        sa.Column('finished_at', T),
    )
    op.create_index('ix_storage_migrations_state', 'storage_migrations', ['state'])
    op.create_index('ix_storage_migrations_created_at', 'storage_migrations', ['created_at'])
    op.create_table(
        'storage_migration_items',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('migration_id', U, sa.ForeignKey('storage_migrations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('path', sa.String(1500), nullable=False),
        sa.Column('category', sa.String(16), nullable=False),
        sa.Column('size', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('mtime', sa.Float(), server_default='0', nullable=False),
        sa.Column('sha256', sa.String(64)),
        sa.Column('state', sa.String(10), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('overwrite', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('error', sa.String(500)),
        sa.UniqueConstraint('migration_id', 'path', name='uq_storage_migration_item_path'),
    )
    op.create_index('ix_storage_migration_items_state', 'storage_migration_items', ['migration_id', 'state'])


def downgrade() -> None:
    op.drop_table('storage_migration_items')
    op.drop_table('storage_migrations')
    op.drop_index('ix_recordings_export_profile_id', table_name='recordings')
    op.drop_column('recordings', 'export_profile_id')
