"""Обработка завершённых встреч переживает сбои: очередь в строке встречи

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-06 11:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0063'
down_revision: Union[str, None] = '0062'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
T = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column('meetings', sa.Column('finalized_at', T, nullable=True))
    op.add_column('meetings', sa.Column('finalize_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('meetings', sa.Column('finalize_after', T, nullable=True))
    op.add_column('meetings', sa.Column('finalize_error', sa.String(500), nullable=True))
    # старые встречи уже обработаны прежней версией: иначе обновление запустило бы их заново
    op.execute("UPDATE meetings SET finalized_at = ended_at WHERE ended_at IS NOT NULL")
    op.create_index('ix_meetings_finalize_pending', 'meetings', ['ended_at'], postgresql_where=sa.text('ended_at IS NOT NULL AND finalized_at IS NULL'),
                    sqlite_where=sa.text('ended_at IS NOT NULL AND finalized_at IS NULL'))


def downgrade() -> None:
    op.drop_index('ix_meetings_finalize_pending', table_name='meetings')
    for c in ('finalize_error', 'finalize_after', 'finalize_attempts', 'finalized_at'):
        op.drop_column('meetings', c)
