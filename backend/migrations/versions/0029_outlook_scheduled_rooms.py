"""запланированные конференции из Outlook: время, окно входа, ключ события

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-18 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0029'
down_revision: Union[str, None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('scheduled_start', sa.DateTime(timezone=True)))
        t.add_column(sa.Column('scheduled_end', sa.DateTime(timezone=True)))
        t.add_column(sa.Column('join_opens_at', sa.DateTime(timezone=True)))
        t.add_column(sa.Column('outlook_key', sa.String(80)))
        t.add_column(sa.Column('recurring', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        t.add_column(sa.Column('cancelled_at', sa.DateTime(timezone=True)))
        t.create_index('uq_rooms_outlook_key', ['created_by_user_id', 'outlook_key'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_index('uq_rooms_outlook_key')
        for c in ('cancelled_at', 'recurring', 'outlook_key', 'join_opens_at', 'scheduled_end', 'scheduled_start'):
            t.drop_column(c)
