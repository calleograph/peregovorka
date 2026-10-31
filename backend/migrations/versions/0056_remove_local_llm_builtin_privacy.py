"""Удаление встроенной локальной модели (Qwen3) и внешнего сервиса обезличивания (DocClean); встроенное обезличивание на Python.

Настройки переносятся бережно:
  * llm.provider «local» → «external», если подключение к модели по API уже настроено (модель или подключения), иначе «off» (документы скажут, что модель не настроена);
    задачи «local» (резюме, карта) → «как протокол»; выбор «local» у комнат и встреч → «системная модель»;
  * обезличивание комнаты «inherit» → «on», если прежнее общее обезличивание было включено, иначе «off»; «on»/«off» сохраняются;
  * прежние настройки и профили DocClean удаляются вместе с хранившимися в них токенами: встроенной подсистеме адрес и токен не нужны.
Материалы встреч и стенограммы не меняются.

Revision ID: 0056
Revises: 0055
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0056'
down_revision: Union[str, None] = '0055'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _val(raw):
    try:
        return json.loads(raw) if raw is not None else None
    except (ValueError, TypeError):
        return None


def upgrade() -> None:
    bind = op.get_bind()
    st = sa.table('app_settings', sa.column('key', sa.String), sa.column('value', sa.Text))
    have = {r.key: r.value for r in bind.execute(sa.select(st.c.key, st.c.value).where(st.c.key.like('llm.%') | st.c.key.like('anonymizer.%'))).all()}
    profiles = sa.table('api_profiles', sa.column('kind', sa.String))
    llm_profiles = bind.execute(sa.select(sa.func.count()).select_from(profiles).where(profiles.c.kind == 'llm')).scalar_one()

    if _val(have.get('llm.provider')) == 'local':
        configured = bool(_val(have.get('llm.model'))) or bool(_val(have.get('llm.protocol_profile'))) or llm_profiles > 0
        bind.execute(sa.update(st).where(st.c.key == 'llm.provider').values(value=json.dumps('external' if configured else 'off')))
        if not configured:
            bind.execute(sa.update(st).where(st.c.key == 'llm.enabled').values(value=json.dumps(False)))
    for k in ('llm.summary_provider', 'llm.map_provider'):
        if _val(have.get(k)) == 'local':
            bind.execute(sa.update(st).where(st.c.key == k).values(value=json.dumps('same')))
    bind.execute(sa.delete(st).where(st.c.key == 'llm.local_model'))

    old_anon_on = bool(_val(have.get('anonymizer.enabled')))
    bind.execute(sa.delete(st).where(st.c.key.like('anonymizer.%')))                 # адрес, токен и режимы DocClean
    bind.execute(sa.delete(profiles).where(profiles.c.kind == 'anonymizer'))         # профили DocClean вместе с токенами

    rooms = sa.table('rooms', sa.column('anonymize_mode', sa.String), sa.column('llm_mode', sa.String), sa.column('llm_summary_mode', sa.String))
    bind.execute(sa.update(rooms).where(rooms.c.anonymize_mode == 'inherit').values(anonymize_mode='on' if old_anon_on else 'off'))
    bind.execute(sa.update(rooms).where(rooms.c.llm_mode == 'local').values(llm_mode='inherit'))
    bind.execute(sa.update(rooms).where(rooms.c.llm_summary_mode == 'local').values(llm_summary_mode='inherit'))
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('anonymize_profile', sa.String(12), nullable=False, server_default=''))
        b.alter_column('anonymize_mode', server_default='off')
        b.drop_column('anonymizer_profile_id')
        b.drop_column('llm_local_model')
        b.drop_column('llm_summary_local_model')

    meetings = sa.table('meetings', sa.column('id', sa.Uuid), sa.column('llm_override', sa.JSON), sa.column('llm_summary_override', sa.JSON))
    for col in ('llm_override', 'llm_summary_override'):
        c = meetings.c[col]
        for mid, v in bind.execute(sa.select(meetings.c.id, c).where(c.is_not(None))).all():
            if isinstance(v, str):
                v = _val(v)
            if isinstance(v, dict) and v.get('mode') == 'local':
                bind.execute(sa.update(meetings).where(meetings.c.id == mid).values({col: None}))


def downgrade() -> None:
    with op.batch_alter_table('rooms') as b:
        b.add_column(sa.Column('llm_summary_local_model', sa.String(80)))
        b.add_column(sa.Column('llm_local_model', sa.String(80)))
        b.add_column(sa.Column('anonymizer_profile_id', sa.Uuid()))
        b.alter_column('anonymize_mode', server_default='inherit')
        b.drop_column('anonymize_profile')
