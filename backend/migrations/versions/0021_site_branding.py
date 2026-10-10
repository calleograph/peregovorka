"""настройки сайта: юридические документы организации, их редакции и подтверждения

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-10 23:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0021'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UTC = sa.DateTime(timezone=True)
_BIGPK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table(
        'legal_documents',
        sa.Column('kind', sa.String(32), primary_key=True),
        sa.Column('title', sa.String(200), nullable=False, server_default=''),
        sa.Column('draft_md', sa.Text(), nullable=False, server_default=''),
        sa.Column('content_md', sa.Text(), nullable=False, server_default=''),
        sa.Column('published_title', sa.String(200), nullable=False, server_default=''),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('require_consent', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('published_at', _UTC),
        sa.Column('updated_at', _UTC, nullable=False),
        sa.Column('updated_by', sa.String(300)),
    )
    op.create_table(
        'legal_revisions',
        sa.Column('id', _BIGPK, primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('content_md', sa.Text(), nullable=False),
        sa.Column('published_at', _UTC, nullable=False),
        sa.Column('published_by', sa.String(300)),
        sa.Column('unpublished_at', _UTC),
    )
    op.create_index('ix_legal_revisions_kind_version', 'legal_revisions', ['kind', 'version'])
    op.create_table(
        'legal_consents',
        sa.Column('id', _BIGPK, primary_key=True, autoincrement=True),
        sa.Column('subject_type', sa.String(8), nullable=False),
        sa.Column('subject_id', sa.String(64), nullable=False),
        sa.Column('subject_name', sa.String(300)),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('accepted_at', _UTC, nullable=False),
        sa.Column('ip', sa.String(64)),
        sa.Column('user_agent', sa.String(300)),
    )
    op.create_index('ix_legal_consents_subject', 'legal_consents', ['subject_type', 'subject_id', 'kind'])


def downgrade() -> None:
    op.drop_index('ix_legal_consents_subject', table_name='legal_consents')
    op.drop_table('legal_consents')
    op.drop_index('ix_legal_revisions_kind_version', table_name='legal_revisions')
    op.drop_table('legal_revisions')
    op.drop_table('legal_documents')
