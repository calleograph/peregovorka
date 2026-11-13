"""Качество камеры по умолчанию в комнате (минимальное для всех комнат)

Revision ID: 0065
Revises: 0064
Create Date: 2026-11-13 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0065'
down_revision: Union[str, None] = '0064'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rooms', sa.Column('camera_quality', sa.String(8), nullable=False, server_default='min'))


def downgrade() -> None:
    op.drop_column('rooms', 'camera_quality')
