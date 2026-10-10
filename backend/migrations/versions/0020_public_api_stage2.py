"""публичный API, этап 2: подписки на события (webhooks) и доставки, фоновые задачи, ключи идемпотентности

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-10 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('url', sa.String(500), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('status', sa.String(12), nullable=False, server_default='active'),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('rooms', sa.JSON()),
        sa.Column('secret_enc', sa.Text()),
        sa.Column('previous_secret_enc', sa.Text()),
        sa.Column('previous_until', _UTC),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_success_at', _UTC),
        sa.Column('last_failure_at', _UTC),
        sa.Column('last_error', sa.String(300)),
        sa.Column('disabled_reason', sa.String(200)),
        sa.Column('created_by', sa.String(300)),
        sa.Column('created_at', _UTC, nullable=False),
        sa.Column('updated_at', _UTC, nullable=False),
    )
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('endpoint_id', sa.Uuid(), sa.ForeignKey('webhook_endpoints.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_id', sa.String(40), nullable=False),
        sa.Column('event_type', sa.String(60), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('manual_retries', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('next_attempt_at', _UTC, nullable=False),
        sa.Column('last_status', sa.Integer()),
        sa.Column('last_error', sa.String(300)),
        sa.Column('attempt_log', sa.JSON()),
        sa.Column('created_at', _UTC, nullable=False),
        sa.Column('delivered_at', _UTC),
    )
    op.create_index('ix_webhook_deliveries_endpoint_id', 'webhook_deliveries', ['endpoint_id'])
    op.create_index('ix_webhook_deliveries_created_at', 'webhook_deliveries', ['created_at'])
    op.create_index('ix_webhook_deliveries_due', 'webhook_deliveries', ['status', 'next_attempt_at'])
    op.create_table(
        'api_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('client_id', sa.Uuid(), sa.ForeignKey('api_clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('meeting_id', sa.Uuid(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('params', sa.JSON()),
        sa.Column('result', sa.JSON()),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', _UTC, nullable=False),
        sa.Column('started_at', _UTC),
        sa.Column('finished_at', _UTC),
    )
    op.create_index('ix_api_jobs_meeting_id', 'api_jobs', ['meeting_id'])
    op.create_index('ix_api_jobs_client_created', 'api_jobs', ['client_id', 'created_at'])
    op.create_index('ix_api_jobs_status_created', 'api_jobs', ['status', 'created_at'])
    op.create_table(
        'api_idempotency',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True, autoincrement=True),
        sa.Column('client_id', sa.Uuid(), sa.ForeignKey('api_clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('state', sa.String(12), nullable=False),
        sa.Column('response_status', sa.Integer()),
        sa.Column('response_body', sa.JSON()),
        sa.Column('created_at', _UTC, nullable=False),
        sa.UniqueConstraint('client_id', 'key', name='uq_api_idempotency_client_key'),
    )
    op.create_index('ix_api_idempotency_client_id', 'api_idempotency', ['client_id'])
    op.create_index('ix_api_idempotency_created_at', 'api_idempotency', ['created_at'])


def downgrade() -> None:
    op.drop_table('api_idempotency')
    op.drop_table('api_jobs')
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_endpoints')
