"""Видеозаписи в общей системе хранения: у каждой выгруженной записи — в каком хранилище она лежит (аудио или видео)

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-30 09:00:00

Раньше видеозаписи выгружались в хранилище аудиозаписей (подпапка Audio/). Новые выгружаются в хранилище видеозаписей (подпапка Video/);
уже выгруженные остаются на прежнем месте — им проставляется export_area='audio', чтобы просмотр, скачивание и перенос находили их там.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0052'
down_revision: Union[str, None] = '0051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('recordings') as b:
        b.add_column(sa.Column('export_area', sa.String(length=10), nullable=True))
    op.execute("UPDATE recordings SET export_area = 'audio' WHERE export_status = 'exported'")


def downgrade() -> None:
    with op.batch_alter_table('recordings') as b:
        b.drop_column('export_area')
