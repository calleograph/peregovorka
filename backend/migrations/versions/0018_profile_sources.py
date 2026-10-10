"""источники профиля: значения по источникам, внешние идентификаторы, происхождение аватарки

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-10 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar_source', sa.String(10), nullable=True))
        batch_op.add_column(sa.Column('profile_sources', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('external_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('external_ids')
        batch_op.drop_column('profile_sources')
        batch_op.drop_column('avatar_source')
