"""Узлы CDN Peregovorka (подключение по SSH, раздача HLS)

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-04 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0060'
down_revision: Union[str, None] = '0059'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
T = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'cdn_nodes',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('public_name', sa.String(253), nullable=False, unique=True),
        sa.Column('region', sa.String(60)),
        sa.Column('weight', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('capacity', sa.Integer()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('state', sa.String(12), nullable=False, server_default='new'),
        sa.Column('ssh_host', sa.String(253), nullable=False),
        sa.Column('ssh_port', sa.Integer(), nullable=False, server_default='22'),
        sa.Column('ssh_user', sa.String(64), nullable=False),
        sa.Column('host_key', sa.Text(), nullable=False, server_default=''),
        sa.Column('host_fp', sa.String(80), nullable=False, server_default=''),
        sa.Column('mgmt_key_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('api_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('pull_key_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('tls_mode', sa.String(12), nullable=False, server_default='acme'),
        sa.Column('acme_email', sa.String(200)),
        sa.Column('facts', J),
        sa.Column('assessment', J),
        sa.Column('stages', J),
        sa.Column('diag', sa.Text()),
        sa.Column('last_error', sa.String(500)),
        sa.Column('health', sa.String(8), nullable=False, server_default='unknown'),
        sa.Column('fails', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('oks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_check_at', T),
        sa.Column('last_ok_at', T),
        sa.Column('stats', J),
        sa.Column('version', sa.String(20)),
        sa.Column('generation', sa.String(16)),
        sa.Column('installed_at', T),
        sa.Column('created_at', T, nullable=False),
        sa.Column('updated_at', T, nullable=False),
    )


def downgrade() -> None:
    op.drop_table('cdn_nodes')
