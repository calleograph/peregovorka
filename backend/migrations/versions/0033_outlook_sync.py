"""Outlook: связь комнаты с событием календаря для серверной сверки

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-18 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0033'
down_revision: Union[str, None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [
    ('outlook_organizer', sa.String(254)), ('outlook_ews_id', sa.String(700)), ('outlook_rest_id', sa.String(700)), ('outlook_series_id', sa.String(700)),
    ('outlook_tz', sa.String(64)), ('outlook_synced_at', sa.DateTime(timezone=True)), ('outlook_sync_state', sa.String(12)), ('outlook_sync_error', sa.String(300)),
]


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        for name, typ in COLUMNS:
            t.add_column(sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        for name, _ in COLUMNS:
            t.drop_column(name)
