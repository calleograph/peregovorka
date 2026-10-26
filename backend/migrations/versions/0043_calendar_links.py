"""Календарь Outlook в Peregovorka: согласие сотрудника на показ и ведение его календаря

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-26 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0043'
down_revision: Union[str, None] = '0042'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'calendar_links',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('email', sa.String(254), nullable=False),
        sa.Column('consented_at', TS, nullable=False),
        sa.Column('last_ok_at', TS),
        sa.Column('last_error', sa.String(300)),
    )


def downgrade() -> None:
    op.drop_table('calendar_links')
