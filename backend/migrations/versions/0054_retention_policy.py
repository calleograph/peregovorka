"""Единая политика хранения: общие сроки текста, аудио и видео; комната либо наследует их, либо задаёт свои

Текст — бессрочно, аудио — 60 дней, видео (в том числе записи эфиров) — 30 дней. Явно заданные сроки комнат сохраняются; комнаты со старыми значениями
по умолчанию (текст 365, аудио 30) и без значения переходят на общие сроки. Удаление по новым срокам начинается не сразу после обновления, а через 7 дней
(general.retention_cleanup_from): администратор успевает проверить сроки. Сами материалы во время обновления не удаляются.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-31 12:00:00
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0054'
down_revision: Union[str, None] = '0053'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TEXT_DEFAULT, OLD_AUDIO_DEFAULT = 365, 30


def upgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('text_retention_custom', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('audio_retention_custom', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('video_retention_custom', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        b.add_column(sa.Column('video_retention_days', sa.Integer(), nullable=True))
    bind = op.get_bind()
    rooms = sa.table('rooms', sa.column('text_retention_days', sa.Integer), sa.column('audio_retention_days', sa.Integer),
                     sa.column('text_retention_custom', sa.Boolean), sa.column('audio_retention_custom', sa.Boolean))
    # явное значение, отличное от прежнего умолчания, — своё у комнаты; остальное наследует общий срок
    bind.execute(sa.update(rooms).where(rooms.c.text_retention_days.is_not(None), rooms.c.text_retention_days != OLD_TEXT_DEFAULT).values(text_retention_custom=True))
    bind.execute(sa.update(rooms).where(rooms.c.audio_retention_days.is_not(None), rooms.c.audio_retention_days != OLD_AUDIO_DEFAULT).values(audio_retention_custom=True))

    st = sa.table('app_settings', sa.column('key', sa.String), sa.column('value', sa.Text), sa.column('is_secret', sa.Boolean), sa.column('updated_at', sa.DateTime(timezone=True)))
    now = datetime.now(timezone.utc)
    have = {r.key: r.value for r in bind.execute(sa.select(st.c.key, st.c.value).where(st.c.key.in_(
        ('general.default_audio_retention_days', 'general.default_video_retention_days', 'video.retention_days', 'general.retention_cleanup_from')))).all()}
    if have.get('general.default_audio_retention_days') in ('null', ''):
        bind.execute(sa.delete(st).where(st.c.key == 'general.default_audio_retention_days'))          # «не задано» → новый общий срок 60 дней
    try:
        vdays = int(json.loads(have.get('video.retention_days') or '0') or 0)
    except (ValueError, TypeError):
        vdays = 0
    if vdays > 0 and 'general.default_video_retention_days' not in have:
        bind.execute(sa.insert(st).values(key='general.default_video_retention_days', value=json.dumps(vdays), is_secret=False, updated_at=now))   # администратор уже задавал срок видео
    if 'general.retention_cleanup_from' not in have:
        start = (now + timedelta(days=7)).replace(microsecond=0).isoformat()
        bind.execute(sa.insert(st).values(key='general.retention_cleanup_from', value=json.dumps(start), is_secret=False, updated_at=now))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        for c in ('video_retention_days', 'video_retention_custom', 'audio_retention_custom', 'text_retention_custom'):
            b.drop_column(c)
