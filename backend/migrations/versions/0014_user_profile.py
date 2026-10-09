"""профиль пользователя: должность, подразделение, телефон, аватарка

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('department', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('phone', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('profile_synced_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('avatar_mime', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('avatar_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        for c in ('avatar_updated_at', 'avatar_mime', 'profile_synced_at', 'phone', 'department', 'title'):
            batch_op.drop_column(c)
