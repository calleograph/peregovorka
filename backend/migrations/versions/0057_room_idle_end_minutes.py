"""Комната: через сколько минут бездействия завершать забытую встречу с одним человеком (по умолчанию 30, 0 — не завершать)

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-02 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0057'
down_revision: Union[str, None] = '0056'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('idle_end_minutes', sa.Integer(), nullable=False, server_default=sa.text('30')))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('idle_end_minutes')
