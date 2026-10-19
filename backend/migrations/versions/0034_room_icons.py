"""Значок комнаты: из встроенной библиотеки или загруженное изображение

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-19 21:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.add_column(sa.Column('icon_kind', sa.String(10), nullable=True))     # NULL — стандартный значок (цветной квадрат с буквами); library | image
        t.add_column(sa.Column('icon_key', sa.String(40), nullable=True))      # ключ значка библиотеки
        t.add_column(sa.Column('icon_rev', sa.String(16), nullable=True))      # версия (хэш) картинки — часть адреса, чтобы кэш браузера обновлялся


def downgrade() -> None:
    with op.batch_alter_table('rooms') as t:
        t.drop_column('icon_rev')
        t.drop_column('icon_key')
        t.drop_column('icon_kind')
