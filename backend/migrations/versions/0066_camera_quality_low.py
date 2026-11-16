"""Качество камеры по умолчанию — «Низкое»: комнаты с прежним умолчанием «Минимальное» переводятся на него

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-16 12:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0066'
down_revision: Union[str, None] = '0065'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # «Минимальное» было умолчанием для всех комнат с 0.75.0; осознанно выбранных значений отличить нельзя
    op.execute("UPDATE rooms SET camera_quality = 'low' WHERE camera_quality = 'min'")
    with op.batch_alter_table('rooms') as b:
        b.alter_column('camera_quality', existing_type=None, server_default='low', existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.alter_column('camera_quality', existing_type=None, server_default='min', existing_nullable=False)
