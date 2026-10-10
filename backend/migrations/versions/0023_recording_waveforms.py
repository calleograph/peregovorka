"""волновая форма записей: компактный массив пиков громкости

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-11 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0023'
down_revision: Union[str, None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'recording_waveforms',
        sa.Column('recording_id', sa.Uuid(), sa.ForeignKey('recordings.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('status', sa.String(12), nullable=False, server_default='processing'),
        sa.Column('bucket_ms', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('peaks', sa.LargeBinary(), nullable=False),
        sa.Column('error', sa.String(300)),
        sa.Column('built_at', sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table('recording_waveforms')
