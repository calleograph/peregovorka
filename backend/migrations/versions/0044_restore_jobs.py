"""Восстановление материалов встреч после переноса хранилища: задания сканирования и восстановления

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-26 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0044'
down_revision: Union[str, None] = '0043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'restore_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('parent_id', sa.Uuid()),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('params', JSON, nullable=False),
        sa.Column('progress', JSON, nullable=False),
        sa.Column('result', JSON),
        sa.Column('error', sa.String(500)),
        sa.Column('started_by_user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('started_by_name', sa.String(200), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('heartbeat_at', TS),
        sa.Column('finished_at', TS),
    )


def downgrade() -> None:
    op.drop_table('restore_jobs')
