"""отдельный выбор языковой модели для краткого резюме (комната, встреча)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('llm_summary_mode', sa.String(length=10), server_default='inherit', nullable=False))
        batch_op.add_column(sa.Column('llm_summary_profile_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('llm_summary_local_model', sa.String(length=80), nullable=True))
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('llm_summary_override', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('meetings', schema=None) as batch_op:
        batch_op.drop_column('llm_summary_override')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('llm_summary_local_model')
        batch_op.drop_column('llm_summary_profile_id')
        batch_op.drop_column('llm_summary_mode')
