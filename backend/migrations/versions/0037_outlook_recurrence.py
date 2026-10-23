"""Шаблон повторения серии встреч из Outlook (ближайшее занятие в «Предстоящих встречах»)

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-24 09:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0037'
down_revision: Union[str, None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('outlook_recurrence', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('outlook_recurrence')
