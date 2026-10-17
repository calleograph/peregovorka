"""двухфакторная аутентификация (TOTP): поля пользователя и резервные коды

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as t:
        t.add_column(sa.Column('totp_secret_enc', sa.Text()))
        t.add_column(sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        t.add_column(sa.Column('totp_last_step', sa.BigInteger()))
        t.add_column(sa.Column('totp_enabled_at', sa.DateTime(timezone=True)))
    op.create_table(
        'user_backup_codes',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_user_backup_codes_user_id', 'user_backup_codes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_backup_codes_user_id', table_name='user_backup_codes')
    op.drop_table('user_backup_codes')
    with op.batch_alter_table('users') as t:
        for c in ('totp_enabled_at', 'totp_last_step', 'totp_enabled', 'totp_secret_enc'):
            t.drop_column(c)
