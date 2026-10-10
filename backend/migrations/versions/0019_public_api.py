"""публичный API: сервисные учётные записи, ключи, журнал обращений

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-10 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'api_clients',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False, unique=True),
        sa.Column('description', sa.Text()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('rooms', sa.JSON()),
        sa.Column('ip_allowlist', sa.JSON()),
        sa.Column('created_by', sa.String(300)),
        sa.Column('created_at', _UTC, nullable=False),
        sa.Column('updated_at', _UTC, nullable=False),
    )
    op.create_table(
        'api_keys',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('client_id', sa.Uuid(), sa.ForeignKey('api_clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_id', sa.String(16), nullable=False, unique=True),
        sa.Column('secret_hash', sa.String(64), nullable=False),
        sa.Column('last4', sa.String(4), nullable=False),
        sa.Column('label', sa.String(120)),
        sa.Column('expires_at', _UTC),
        sa.Column('revoked_at', _UTC),
        sa.Column('last_used_at', _UTC),
        sa.Column('last_used_ip', sa.String(64)),
        sa.Column('created_at', _UTC, nullable=False),
    )
    op.create_index('ix_api_keys_client_id', 'api_keys', ['client_id'])
    op.create_table(
        'api_request_log',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True, autoincrement=True),
        sa.Column('at', _UTC, nullable=False),
        sa.Column('client_id', sa.Uuid()),
        sa.Column('key_id', sa.String(16)),
        sa.Column('method', sa.String(8), nullable=False),
        sa.Column('path', sa.String(200), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('ms', sa.Integer(), nullable=False),
        sa.Column('ip', sa.String(64)),
        sa.Column('request_id', sa.String(40)),
        sa.Column('error_code', sa.String(60)),
    )
    op.create_index('ix_api_request_log_at', 'api_request_log', ['at'])
    op.create_index('ix_api_request_log_client_at', 'api_request_log', ['client_id', 'at'])


def downgrade() -> None:
    op.drop_table('api_request_log')
    op.drop_table('api_keys')
    op.drop_table('api_clients')
