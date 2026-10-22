"""Комната: скрывать нецензурную лексику в стенограмме (по умолчанию включено)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-23 03:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0035'
down_revision: Union[str, None] = '0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('mask_profanity', sa.Boolean(), nullable=False, server_default=sa.text('true')))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('mask_profanity')
