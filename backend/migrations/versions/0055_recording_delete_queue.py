"""Очередь удаления записей: строка с file_state='deleting' живёт, пока файл не удалён локально и во внешнем хранилище.

Revision ID: 0055
Revises: 0054
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0055'
down_revision: Union[str, None] = '0054'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("recordings") as b:
        b.add_column(sa.Column("delete_attempts", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("delete_error", sa.String(300)))
        b.add_column(sa.Column("delete_next_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("recordings") as b:
        b.drop_column("delete_next_at")
        b.drop_column("delete_error")
        b.drop_column("delete_attempts")
