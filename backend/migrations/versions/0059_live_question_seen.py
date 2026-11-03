"""Вопрос зрителя: когда ведущий его увидел (статус «Получен ведущим» у зрителя); цвет значка комнаты

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-03 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0059'
down_revision: Union[str, None] = '0058'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('live_questions') as t:
        t.add_column(sa.Column('seen_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('icon_hue', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('icon_hue')
    with op.batch_alter_table('live_questions') as t:
        t.drop_column('seen_at')
