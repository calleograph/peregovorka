"""параметры генерации по умолчанию: температура 0, раздельные пределы длины ответа

Старые установки сохраняли значения по умолчанию прежней версии явно (температура 0,2; единая длина ответа 4000). Эти строки убираются, чтобы
действовали новые умолчания (температура 0; резюме 2000 и протокол 7000 токенов). Изменённые администратором значения не затрагиваются.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    settings = sa.table('app_settings', sa.column('key', sa.String), sa.column('value', sa.Text))
    for key, old in (('llm.temperature', ('0.2', '0.20')), ('llm.max_tokens', ('4000',))):
        bind.execute(sa.delete(settings).where(settings.c.key == key, settings.c.value.in_(old)))
    profiles = sa.table('api_profiles', sa.column('id', sa.Uuid), sa.column('kind', sa.String), sa.column('config', sa.JSON))
    for row in bind.execute(sa.select(profiles.c.id, profiles.c.config).where(profiles.c.kind == 'llm')).all():
        cfg = dict(row.config or {})
        changed = False
        if cfg.get('temperature') == 0.2:
            cfg.pop('temperature')
            changed = True
        if cfg.get('max_tokens') == 4000:
            cfg.pop('max_tokens')
            changed = True
        if changed:
            bind.execute(sa.update(profiles).where(profiles.c.id == row.id).values(config=cfg))


def downgrade() -> None:
    pass
