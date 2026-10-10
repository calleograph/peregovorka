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


ROOT = Path(__file__).resolve().parents[2]


def test_every_persistent_volume_and_env_file_key_has_a_policy():
    assert C.volume_problems(ROOT / "deployment" / "compose.yml") == []
    assert C.env_file_problems(ROOT / ".env.example") == []


def test_a_new_volume_or_env_key_without_a_policy_is_detected(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("      - ${DATA_ROOT}/brand_new_volume:/data/x\n      - ${DATA_ROOT:?DATA_ROOT is required}/postgres:/var/lib/postgresql/data\n", encoding="utf-8")
    assert any("brand_new_volume" in p for p in C.volume_problems(compose))
    env = tmp_path / ".env.example"
    env.write_text("APP_PUBLIC_URL=x\nBRAND_NEW_KEY=1\n# ASR_NEW_SETTING=2\n", encoding="utf-8")
    msgs = C.env_file_problems(env)
    assert any("BRAND_NEW_KEY" in m for m in msgs) and not any("ASR_NEW_SETTING" in m or "APP_PUBLIC_URL" in m for m in msgs)


def test_secret_environment_values_are_recognised_by_name():
    for n in ("postgres_password", "app_master_key", "internal_api_token", "livekit_api_secret", "database_url", "ldap_bind_password"):
        assert R.is_secret_env(n), n
    for n in ("app_public_url", "ldap_admin_group_dn", "default_audio_retention_days"):
        assert not R.is_secret_env(n), n
