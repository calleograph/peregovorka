"""история заданий автоматического копирования настроек

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-17 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0026'
down_revision: Union[str, None] = '0025'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'config_backup_runs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('kind', sa.String(10), nullable=False, server_default='backup'),
        sa.Column('trigger', sa.String(10), nullable=False),
        sa.Column('actor', sa.String(300)),
        sa.Column('status', sa.String(12), nullable=False, server_default='running'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('file_name', sa.String(120)),
        sa.Column('size_bytes', sa.BigInteger()),
        sa.Column('sha256', sa.String(64)),
        sa.Column('message', sa.String(500)),
    )
    op.create_index('ix_config_backup_runs_started_at', 'config_backup_runs', ['started_at'])


def downgrade() -> None:
    op.drop_index('ix_config_backup_runs_started_at', table_name='config_backup_runs')
    op.drop_table('config_backup_runs')
