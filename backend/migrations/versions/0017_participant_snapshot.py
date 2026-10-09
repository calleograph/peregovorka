"""снимок данных участника на момент встречи (должность, подразделение и т.д.)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-10 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('meeting_participants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('snapshot', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('meeting_participants', schema=None) as batch_op:
        batch_op.drop_column('snapshot')
