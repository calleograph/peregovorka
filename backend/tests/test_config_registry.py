"""Реестр конфигурации обязан описывать КАЖДЫЙ параметр, которым пользуется приложение: таблицы, столбцы, группы настроек, переменные окружения.
Новый параметр без решения «переносится ли он» — ошибка этого теста, а не тихая потеря при переносе на новый сервер."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table

from app.models import Base
from app.services.config_backup import coverage as C
from app.services.config_backup import registry as R
from app.services.settings import GROUPS, _Group


def test_registry_covers_every_table_column_group_and_env_parameter():
    problems = C.all_problems()
    assert problems == [], "\n".join(problems)


def _copy_metadata() -> MetaData:
    md = MetaData()
    for t in Base.metadata.tables.values():
        t.to_metadata(md)
    return md


def test_a_new_table_without_a_policy_is_detected():
    md = _copy_metadata()
    Table("brand_new_settings", md, Column("id", Integer, primary_key=True), Column("value", String))
    msg = "\n".join(C.table_problems(md))
    assert "brand_new_settings" in msg and "не описана в реестре" in msg


def test_a_new_column_in_an_exported_table_without_a_policy_is_detected():
    md = _copy_metadata()
    md.tables["rooms"].append_column(Column("brand_new_flag", String))
    md.tables["mail_profiles"].append_column(Column("new_secret_token", String))
    msg = "\n".join(C.table_problems(md))
    assert "rooms.brand_new_flag" in msg and "mail_profiles.new_secret_token" in msg


def test_a_new_environment_parameter_without_a_policy_is_detected():
    from app.config import Settings

    msg = "\n".join(C.env_problems(set(Settings.model_fields) | {"brand_new_env_param"}))
    assert "brand_new_env_param" in msg


def test_a_new_settings_group_without_a_policy_is_detected_and_a_new_field_is_exported_automatically():
    class Fake(_Group):
        x: int = 1

    assert "новая_группа" in "\n".join(C.group_problems({**GROUPS, "новая_группа": Fake}))
    # новое поле существующей группы входит в копию без правок реестра (группа переносится целиком, исключения перечислены явно) — это проверяется в тесте экспорта
    assert all(not p.skip or set(p.skip) <= set(GROUPS[g].model_fields) for g, p in R.GROUPS_POLICY.items())


def test_a_direct_environment_read_without_a_policy_is_detected(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "m.py").write_text('import os\nx = os.environ.get("BRAND_NEW_VAR")\ny = os.getenv("FFMPEG_BIN")\n', encoding="utf-8")
    msg = "\n".join(C.env_read_problems(app))
    assert "BRAND_NEW_VAR" in msg and "FFMPEG_BIN" not in msg


def test_hash_and_secret_columns_are_consistent():
    for n, p in R.TABLES.items():
        for c in p.hashes:
            assert p.columns[c] == R.HASH, (n, c)
        for c, pol in p.columns.items():
            if pol == R.SECRET:
                assert p.aad is not None, n
    assert R.TABLES["rooms"].aad is None and R.TABLES["storage_profiles"].aad({"id": "abc"}) == b"storage_profile.abc"
    assert R.TABLES["webhook_endpoints"].aad({"id": "x"}) == b"webhook:x"


def test_every_old_schema_version_has_a_transform_chain_to_the_current_one():
    # сейчас версия одна; при её повышении без описания перехода это упадёт
    assert R.upgrade_payload({"a": 1}, R.SCHEMA_VERSION) == {"a": 1}
    for v in range(1, R.SCHEMA_VERSION):
        assert v in R.TRANSFORMS, f"нет правила преобразования архива версии {v}"
