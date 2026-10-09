"""шаблоны писем для рассылки материалов встречи

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-10 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mail_templates',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('subject', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.Column('materials', sa.JSON(), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('mail_templates')
