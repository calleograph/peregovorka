"""api profiles (несколько LLM/обезличивателей), журнал событий, настройки ИИ в комнате

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    # Только добавление: существующие данные и настройки не затрагиваются. Комнаты получают anonymize_mode='inherit' — поведение прежнее.
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.add_column(sa.Column('anonymize_mode', sa.String(length=10), server_default='inherit', nullable=False))
        batch_op.add_column(sa.Column('llm_profile_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('anonymizer_profile_id', sa.Uuid(), nullable=True))

    op.create_table('api_profiles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('config', _JSON, nullable=False),
    sa.Column('secret_enc', sa.Text(), server_default='', nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'name', name='uq_api_profile_name')
    )
    with op.batch_alter_table('api_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_profiles_kind'), ['kind'], unique=False)

    op.create_table('event_log',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('level', sa.String(length=8), nullable=False),
    sa.Column('category', sa.String(length=24), nullable=False),
    sa.Column('event', sa.String(length=64), nullable=False),
    sa.Column('user_name', sa.String(length=300), nullable=True),
    sa.Column('room', sa.String(length=200), nullable=True),
    sa.Column('meeting_id', sa.String(length=40), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('client', sa.String(length=160), nullable=True),
    sa.Column('message', sa.String(length=600), nullable=True),
    sa.Column('data', _JSON, nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('event_log', schema=None) as batch_op:
        for col in ('at', 'level', 'category', 'event', 'user_name', 'room'):
            batch_op.create_index(batch_op.f(f'ix_event_log_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('event_log', schema=None) as batch_op:
        for col in ('room', 'user_name', 'event', 'category', 'level', 'at'):
            batch_op.drop_index(batch_op.f(f'ix_event_log_{col}'))
    op.drop_table('event_log')
    with op.batch_alter_table('api_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_profiles_kind'))
    op.drop_table('api_profiles')
    with op.batch_alter_table('rooms', schema=None) as batch_op:
        batch_op.drop_column('anonymizer_profile_id')
        batch_op.drop_column('llm_profile_id')
        batch_op.drop_column('anonymize_mode')
