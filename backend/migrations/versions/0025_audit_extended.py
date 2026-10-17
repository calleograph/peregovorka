"""журнал аудита: тип исполнителя, User-Agent, сессия, результат, подпись записи

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-17 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0025'
down_revision: Union[str, None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('audit_log') as t:
        t.add_column(sa.Column('actor_type', sa.String(12), nullable=False, server_default='user'))
        t.add_column(sa.Column('user_agent', sa.String(300)))
        t.add_column(sa.Column('session_id', sa.String(24)))
        t.add_column(sa.Column('result', sa.String(12), nullable=False, server_default='success'))
        t.add_column(sa.Column('sig', sa.String(64)))
        t.create_index('ix_audit_log_result', ['result'])


def downgrade() -> None:
    with op.batch_alter_table('audit_log') as t:
        t.drop_index('ix_audit_log_result')
        for c in ('sig', 'result', 'session_id', 'user_agent', 'actor_type'):
            t.drop_column(c)
