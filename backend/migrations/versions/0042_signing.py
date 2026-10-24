"""Подпись клиента Windows: подписывающие агенты, сведения о сертификатах (без закрытых ключей), задания и корпоративные сборки

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-24 20:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0042'
down_revision: Union[str, None] = '0041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), 'postgresql')
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'signing_agents',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('host', sa.String(200), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('version', sa.String(40), nullable=False),
        sa.Column('info', JSON),
        sa.Column('created_by_user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('last_seen_at', TS),
        sa.Column('last_ip', sa.String(64)),
        sa.Column('revoked_at', TS),
    )
    op.create_table(
        'signing_certs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('signing_agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('thumbprint', sa.String(40), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('subject', sa.String(400), nullable=False),
        sa.Column('issuer', sa.String(400), nullable=False),
        sa.Column('serial', sa.String(80), nullable=False),
        sa.Column('not_before', TS, nullable=False),
        sa.Column('not_after', TS, nullable=False),
        sa.Column('key_alg', sa.String(60), nullable=False),
        sa.Column('sig_alg', sa.String(60), nullable=False),
        sa.Column('check', JSON),
        sa.Column('cer', sa.LargeBinary()),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('checked_at', TS),
        sa.Column('retired_at', TS),
    )
    op.create_index('ix_signing_certs_agent_id', 'signing_certs', ['agent_id'])
    op.create_table(
        'signing_jobs',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('agent_id', sa.Uuid(), sa.ForeignKey('signing_agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('cert_id', sa.Uuid(), sa.ForeignKey('signing_certs.id', ondelete='SET NULL')),
        sa.Column('params', JSON, nullable=False),
        sa.Column('result', JSON),
        sa.Column('progress', sa.String(200), nullable=False),
        sa.Column('error', sa.String(500)),
        sa.Column('created_by_user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('created_by_name', sa.String(200), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('started_at', TS),
        sa.Column('finished_at', TS),
    )
    op.create_index('ix_signing_jobs_agent_id', 'signing_jobs', ['agent_id'])


def downgrade() -> None:
    op.drop_table('signing_jobs')
    op.drop_table('signing_certs')
    op.drop_table('signing_agents')
