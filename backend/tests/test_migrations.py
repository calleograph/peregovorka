"""Схема БД создаётся ТОЛЬКО миграциями; тест ловит расхождение моделей и миграций."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]


def _config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def test_single_head():
    heads = ScriptDirectory.from_config(_config(Path("x.db"))).get_heads()
    assert len(heads) == 1


def test_upgrade_head_matches_models_and_downgrades_cleanly(tmp_path):
    db = tmp_path / "mig.db"
    cfg = _config(db)
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        assert {"users", "rooms", "room_acl", "meetings", "meeting_participants", "transcript_segments",
                "audit_log", "app_settings", "alembic_version"} <= tables
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        diff = compare_metadata(ctx, Base.metadata)
        assert diff == [], f"модели расходятся с миграциями: {diff}"
    engine.dispose()

    command.downgrade(cfg, "base")
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        assert set(sa.inspect(conn).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_one_active_meeting_per_room_enforced_by_schema(tmp_path):
    db = tmp_path / "mig2.db"
    command.upgrade(_config(db), "head")
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO rooms (id,slug,name,is_enabled,max_participants,transcription_enabled,record_audio,"
                             "camera_allowed,screen_share_allowed,created_at,updated_at) VALUES "
                             "('r1','s','n',1,5,1,0,1,1,'2026-01-01','2026-01-01')")
        ins = ("INSERT INTO meetings (id,room_id,livekit_room,started_at,transcription_enabled,record_audio,created_at) "
               "VALUES ('%s','r1','%s','2026-01-01',1,0,'2026-01-01')")
        conn.exec_driver_sql(ins % ("m1", "lk1"))
    with engine.begin() as conn:
        try:
            conn.exec_driver_sql(ins % ("m2", "lk2"))
            raised = False
        except sa.exc.IntegrityError:
            raised = True
    assert raised, "вторая активная встреча в той же комнате должна отклоняться"
    engine.dispose()


def test_0056_moves_settings_off_the_removed_local_model_and_docclean_without_losing_data(tmp_path):
    db = tmp_path / "mig56.db"
    cfg = _config(db)
    command.upgrade(cfg, "0055")
    engine = sa.create_engine(f"sqlite:///{db}")
    room = ("INSERT INTO rooms (id,slug,name,is_enabled,max_participants,transcription_enabled,record_audio,camera_allowed,screen_share_allowed,"
            "created_at,updated_at,anonymize_mode,llm_mode) VALUES ('%s','%s','n',1,5,1,0,1,1,'2026-01-01','2026-01-01','%s','%s')")
    with engine.begin() as conn:
        for k, v in (("llm.provider", '"local"'), ("llm.summary_provider", '"local"'), ("llm.local_model", '"qwen3-1.7b-q4_k_m"'),
                     ("anonymizer.enabled", "true"), ("anonymizer.base_url", '"https://anon.example.org"')):
            conn.exec_driver_sql(f"INSERT INTO app_settings (key,value,is_secret,updated_at) VALUES ('{k}','{v}',0,'2026-01-01')")
        conn.exec_driver_sql("INSERT INTO api_profiles (id,kind,name,config,secret_enc,is_default,created_at,updated_at) "
                             "VALUES ('00000000000000000000000000000001','anonymizer','DocClean','{}','x',0,'2026-01-01','2026-01-01')")
        conn.exec_driver_sql(room % ("00000000000000000000000000000011", "a", "inherit", "local"))
        conn.exec_driver_sql(room % ("00000000000000000000000000000012", "b", "off", "inherit"))
        conn.exec_driver_sql("INSERT INTO meetings (id,room_id,livekit_room,started_at,transcription_enabled,record_audio,created_at,llm_override) "
                             "VALUES ('00000000000000000000000000000021','00000000000000000000000000000011','lk1','2026-01-01',1,0,'2026-01-01','{\"mode\": \"local\"}')")
    engine.dispose()
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        st = dict(conn.exec_driver_sql("SELECT key, value FROM app_settings").all())
        assert st["llm.provider"] == '"off"', "подключения к модели по API не было — «отключено», а не молчаливый переход на чужой сервис"
        assert st["llm.summary_provider"] == '"same"' and "llm.local_model" not in st and not [k for k in st if k.startswith("anonymizer.")]
        assert conn.exec_driver_sql("SELECT count(*) FROM api_profiles").scalar() == 0, "профиль DocClean удалён вместе с токеном"
        rooms = {r[0]: r[1:] for r in conn.exec_driver_sql("SELECT id, anonymize_mode, llm_mode FROM rooms").all()}
        assert rooms == {"00000000000000000000000000000011": ("on", "inherit"), "00000000000000000000000000000012": ("off", "inherit")}, "наследование при включённом прежнем обезличивании — включено"
        assert conn.exec_driver_sql("SELECT llm_override FROM meetings").scalar() is None
    engine.dispose()
