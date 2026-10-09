"""временные переговорки и прежние адреса комнат

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-14 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slug_history', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('lifetime', sa.String(length=12), server_default='permanent', nullable=False))
        batch_op.add_column(sa.Column('lifecycle', sa.String(length=12), server_default='active', nullable=False))
        batch_op.add_column(sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('auto_close_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('created_by_user_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('created_by_name', sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        for c in ('created_by_name', 'created_by_user_id', 'auto_close_at', 'closed_at', 'lifecycle', 'lifetime', 'slug_history'):
            batch_op.drop_column(c)
