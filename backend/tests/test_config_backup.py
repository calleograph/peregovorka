"""Резервная копия конфигурации: шифрованный контейнер, экспорт, предпросмотр и применение на «чистом» втором сервере.

Главная проверка — сквозная: настроенный сервер А → архив → новый сервер Б с другим ключом шифрования: секреты расшифровываются ключом Б, переговорки, интеграции и настройки на месте,
данные встреч НЕ перенесены, локальный администратор и пользователи Б не тронуты, а при любой ошибке в процессе применения Б остаётся в прежнем состоянии.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (ApiClient, ApiKey, ApiProfile, AppSetting, AuditLog, LdapProfile, MailProfile, MailTemplate, Meeting, Room, StorageProfile, TranscriptSegment, User,
                        WebhookEndpoint, utcnow)
from app.publicapi import keys as pkeys
from app.services.config_backup import container as K

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_transcripts import _join

CA_PEM_NOTE = "ca"                      # сертификаты проверяются отдельным тестом реестра; здесь — секреты, комнаты, интеграции, настройки


def db_run(c, fn):
    async def go():
        async with c.app_obj.state.session_maker() as db:
            return await fn(db)
    return c.portal.call(go)


# ----------------------------------------------------------------------------------------------------- контейнер
def test_password_is_20_random_unambiguous_characters():
    ps = {K.generate_password() for _ in range(200)}
    assert len(ps) == 200 and all(len(p) == 20 and set(p) <= set(K.ALPHABET) for p in ps)
    assert not set("0O1Il") & set(K.ALPHABET)
    assert K.normalize_password(" ab-cd ef\n") == "abcdef"


def test_container_round_trip_wrong_password_tampering_and_format_errors(monkeypatch):
    payload = {"manifest": {"kind": "peregovorka-config", "schema": 1}, "x": "секрет-значение"}
    pw = K.generate_password()
    blob = K.seal(payload, pw, app_version="9.9.9")
    assert b"\xd1\x81\xd0\xb5\xd0\xba\xd1\x80" not in blob and b"secret" not in blob and "секрет".encode() not in blob, "содержимое зашифровано"
    h, back = K.open_(blob, pw)
    assert back == payload and h["app_version"] == "9.9.9" and h["kdf"]["alg"] == "scrypt"
    assert K.open_(blob, " " + pw[:5] + "-" + pw[5:] + " ")[1] == payload, "пробелы и дефисы при вводе пароля не мешают"
    with pytest.raises(K.ContainerError) as e:
        K.open_(blob, "wrong-password-123456")
    assert e.value.code == "bad_password"
    for pos in (len(blob) - 5, len(blob) // 2, 30):                       # любое изменение (в том числе заголовка) обнаруживается
        bad = bytearray(blob)
        bad[pos] ^= 0x01
        with pytest.raises(K.ContainerError):
            K.open_(bytes(bad), pw)
    with pytest.raises(K.ContainerError) as e:
        K.open_(b"not an archive at all", pw)
    assert e.value.code == "not_archive"
    with pytest.raises(K.ContainerError) as e:
        K.open_(blob[:20], pw)
    assert e.value.code in ("not_archive", "bad_header")
    # слишком новый архив: сообщение до ввода пароля
    monkeypatch.setattr(K, "SCHEMA_VERSION", 99)
    newer = K.seal(payload, pw, app_version="99.0.0")
    monkeypatch.undo()
    with pytest.raises(K.ContainerError) as e:
        K.read_header(newer)
    assert e.value.code == "too_new" and "99.0.0" in e.value.message and "Обновите" in e.value.message


def test_hostile_archives_are_rejected_cheaply(monkeypatch):
    pw = K.generate_password()
    blob = K.seal({"a": 1}, pw, app_version="1")
    (hl,) = struct.unpack(">I", blob[7:11])
    hdr = json.loads(blob[11:11 + hl])
    hdr["kdf"]["n"] = 1 << 30                                              # «бомба» памяти в заголовке
    hb = json.dumps(hdr).encode()
    evil = blob[:7] + struct.pack(">I", len(hb)) + hb + blob[11 + hl:]
    with pytest.raises(K.ContainerError) as e:
        K.open_(evil, pw)
    assert e.value.code == "bad_header"
    with pytest.raises(K.ContainerError) as e:
        K.read_header(b"PGCFG\x01\n" + struct.pack(">I", 10 ** 8) + b"x" * 50)
    assert e.value.code == "bad_header"
    monkeypatch.setattr(K, "MAX_FILE_BYTES", 100)
    with pytest.raises(K.ContainerError) as e:
        K.read_header(blob)
    assert e.value.code == "too_large"
    monkeypatch.undo()
    monkeypatch.setattr(K, "MAX_PLAIN_BYTES", 1000)                        # распаковка сильно сжатых данных ограничена
    big = K.seal({"z": "0" * 100_000}, pw, app_version="1")
    with pytest.raises(K.ContainerError) as e:
        K.open_(big, pw)
    assert e.value.code == "too_large"


# ----------------------------------------------------------------------------------------------------- сервер А
def seed(c) -> dict:
    """Настроенный сервер: секреты везде, где они бывают, плюс данные встреч, которые переноситься НЕ должны."""
    login(c, "root")
    box = c.app_obj.state.settings_svc.box
    ids = {k: uuid.uuid4() for k in ("ldap", "mail", "client", "key", "hook", "tmpl")}
    put_settings(c, "llm", enabled=True, type="openai_compatible", base_url="https://llm.old-corp.test/v1", model="main-model", api_key="LLM-KEY-A")
    put_settings(c, "storage", enabled=True, mode="smb", smb_server="files.old-corp.test", smb_share="docs", smb_username="svc", smb_password="SMB-PASS-A")
    put_settings(c, "general", **{k: v for k, v in c.get("/api/v1/admin/settings/general").json().items() if k in ("max_participants",)} or {})
    assert c.post("/api/v1/admin/storages", json={"name": "Файловый сервер", "kind": "smb", "config": {"smb_server": "files.old-corp.test", "smb_share": "rec", "smb_username": "svc"}, "secret": "STORAGE-PASS-A"}).status_code == 201
    assert c.post("/api/v1/admin/api-profiles", json={"kind": "llm", "name": "Резервный", "secret": "PROFILE-SECRET-A",
                                                      "config": {"type": "openai_compatible", "base_url": "https://llm-a.old-corp.test/v1", "model": "a-model"}}).status_code == 201
    pw_room = make_room(c, name="Совещания", description="Главная", password="room-pass-1")
    plain = make_room(c, name="Открытая")
    tmp = make_room(c, name="Временная")

    async def rows(db):
        db.add(LdapProfile(id=ids["ldap"], name="AD главный", host="dc1.old-corp.test", port=636, base_dn="DC=old-corp,DC=test", bind_dn="CN=svc,DC=old-corp,DC=test",
                           secret_enc=box.encrypt("LDAP-PASS-A", aad=f"ldap_profile.{ids['ldap']}".encode())))
        db.add(MailProfile(id=ids["mail"], name="Почта", host="smtp.old-corp.test", port=587, security="starttls", auth_type="login", username="robot@old-corp.test",
                           secret_enc=box.encrypt("SMTP-PASS-A", aad=f"mail_profile.{ids['mail']}".encode()), from_address="robot@old-corp.test", is_active=True))
        db.add(MailTemplate(id=ids["tmpl"], name="Итоги", subject="Итоги {{room}}", body="Текст письма", is_default=True))
        db.add(ApiClient(id=ids["client"], name="Интеграция CRM", scopes=["meetings:read"], rooms=[str(pw_room["id"])], ip_allowlist=["10.0.0.0/8"], enabled=True))
        await db.flush()
        full, key_id, digest, last4 = pkeys.new_key()
        ids["api_key_full"] = full
        db.add(ApiKey(id=ids["key"], client_id=ids["client"], key_id=key_id, secret_hash=digest, last4=last4, label="основной"))
        db.add(WebhookEndpoint(id=ids["hook"], name="CRM-события", url="https://crm.old-corp.test/hook", events=[], secret_enc=box.encrypt("HOOK-SECRET-A", aad=f"webhook:{ids['hook']}".encode())))
        t = await db.get(Room, uuid.UUID(tmp["id"]))
        t.lifetime = "temporary"
        # данные встреч — НЕ переносятся
        r = await db.get(Room, uuid.UUID(plain["id"]))
        m = Meeting(room_id=r.id, livekit_room="lk-seed", started_by_user_id=(await db.execute(select(User.id).limit(1))).scalars().first())
        db.add(m)
        await db.flush()
        db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=r.id, participant_identity="u-x", started_at=utcnow(), ended_at=utcnow(), text="секретная реплика встречи"))
        await db.commit()
    db_run(c, rows)
    ids["pw_room"], ids["plain_room"], ids["tmp_room"] = pw_room["id"], plain["id"], tmp["id"]
    return ids


def export(c, admin_pw="root-pass") -> tuple[bytes, str]:
    login(c, "root")
    r = c.post("/api/v1/admin/config/export", json={"password": admin_pw})
    assert r.status_code == 200, r.text
    return r.content, r.headers["x-archive-password"]


def test_export_needs_admin_reauthentication_and_never_leaves_traces(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        ids = seed(c)
        login(c, "alice")
        assert c.post("/api/v1/admin/config/export", json={"password": "alice-pass"}).status_code == 403, "только администратор"
        assert c.get("/api/v1/admin/config/info").status_code == 403
        login(c, "root")
        assert c.post("/api/v1/admin/config/export", json={"password": "wrong"}).status_code == 403, "неверный пароль администратора"
        c.cookies.clear()
        assert c.post("/api/v1/admin/config/export", json={"password": "root-pass"}).status_code == 401
        blob, pw = export(c)
        r = c.post("/api/v1/admin/config/export", json={"password": "root-pass"})
        assert r.headers["cache-control"].startswith("no-store") and r.headers["x-accel-buffering"] == "no" and "attachment" in r.headers["content-disposition"]
        assert len(pw) == 20 and r.headers["x-archive-password"] != pw, "каждый раз новый пароль"
        for secret in ("LLM-KEY-A", "SMB-PASS-A", "STORAGE-PASS-A", "PROFILE-SECRET-A", "LDAP-PASS-A", "SMTP-PASS-A", "HOOK-SECRET-A", "old-corp", "секретная реплика"):
            assert secret.encode() not in blob and secret.encode("utf-16-le") not in blob, f"«{secret}» виден в файле без пароля"
        # на диск сервера архив не пишется: после выгрузки в каталоге данных нет файлов архива
        assert not [p for p in Path(s.data_dir).rglob("*") if p.is_file() and (p.suffix == ".pgcfg" or p.read_bytes()[:7] == K.MAGIC)]
        # в аудите — только факт, без пароля и содержимого
        audit = db_run(c, lambda db: _audit(db))
        text = json.dumps(audit, ensure_ascii=False)
        assert "config.export" in text and pw not in text and "LLM-KEY" not in text and "root-pass" not in text
        info = c.get("/api/v1/admin/config/info").json()
        names = {i["name"] for i in info["included"]}
        assert {"rooms", "ldap_profiles", "mail_profiles", "storage_profiles", "webhook_endpoints", "api_keys"} <= names and "transcript_segments" not in names
        assert any("ключи" in h["what"] for h in info["hashed"]) and any("пароли переговорок" == h["what"] for h in info["hashed"])
        assert "APP_MASTER_KEY" in info["environment"]


async def _audit(db):
    return [(a.action, a.details, a.actor_name) for a in (await db.execute(select(AuditLog))).scalars().all()]


def upload(c, path: str, blob: bytes, archive_pw: str, **headers):
    return c.post(path, content=blob, headers={"content-type": "application/octet-stream", "x-archive-password": archive_pw, **headers})


def test_full_restore_on_a_clean_server_with_another_master_key(tmp_path, directory):
    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    with running_app(sa, directory) as ca:
        ids = seed(ca)
        blob, pw = export(ca)
    assert sa.app_master_key != sb.app_master_key
    with running_app(sb, directory) as c:
        login(c, "root")
        users_before = db_run(c, lambda db: _count(db, User))
        insp = c.post("/api/v1/admin/config/import/inspect", content=blob, headers={"content-type": "application/octet-stream"}).json()
        assert insp["schema"] == 1 and insp["size_bytes"] == len(blob)
        # неверный пароль архива — отказ без изменений; ограничение перебора
        assert upload(c, "/api/v1/admin/config/import/preview", blob, "x" * 20).status_code == 400
        prev = upload(c, "/api/v1/admin/config/import/preview", blob, pw)
        assert prev.status_code == 200, prev.text
        p = prev.json()
        assert p["target_clean"] is True and p["needs_ack"] is True
        kinds = {w["kind"] for w in p["warnings"]}
        assert {"host", "url"} <= kinds or {"url", "smb"} <= kinds, kinds
        assert any("ldap" in (w["where"] or "").lower() or "каталогу" in w["where"] for w in p["warnings"]), "адрес каталога старого сервера отмечен"
        assert any("old-corp" in str(w["value"]) for w in p["warnings"])
        assert any(h["what"] == "пароли переговорок" for h in p["hashed"]) and any("API" in h["what"] for h in p["hashed"])
        assert db_run(c, lambda db: _count(db, LdapProfile)) == 0, "предпросмотр ничего не меняет"
        # применение: без подтверждения и с неверным паролем администратора — отказ
        assert upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass"}).status_code == 400
        assert upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "nope", "x-import-confirm": "yes"}).status_code == 403
        assert db_run(c, lambda db: _count(db, LdapProfile)) == 0
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 200, r.text
        rep = r.json()
        assert rep["ok"] and rep["applied"]["tables"]["rooms"] == 2 and "summary" in rep and rep["files_problems"] == []
        statuses = {x["status"] for x in rep["checks"]}
        assert statuses <= {"restored", "needs_attention", "failed"}
        assert any(x["component"].startswith("Каталог") and x["status"] != "restored" for x in rep["checks"]), "несуществующий каталог старого сервера честно отмечен как требующий внимания"
        # --- секреты расшифровываются ключом нового сервера и совпадают с исходными
        box_b = c.app_obj.state.settings_svc.box

        async def check(db):
            ldap = (await db.execute(select(LdapProfile))).scalars().one()
            assert ldap.id == ids["ldap"] and box_b.decrypt(ldap.secret_enc, aad=f"ldap_profile.{ldap.id}".encode()) == "LDAP-PASS-A"
            mail = (await db.execute(select(MailProfile))).scalars().one()
            assert box_b.decrypt(mail.secret_enc, aad=f"mail_profile.{mail.id}".encode()) == "SMTP-PASS-A" and mail.is_active
            sp = (await db.execute(select(StorageProfile))).scalars().one()
            assert box_b.decrypt(sp.secret_enc, aad=f"storage_profile.{sp.id}".encode()) == "STORAGE-PASS-A" and sp.config["smb_server"] == "files.old-corp.test"
            ap = (await db.execute(select(ApiProfile))).scalars().one()
            assert box_b.decrypt(ap.secret_enc, aad=f"api_profile.{ap.id}".encode()) == "PROFILE-SECRET-A"
            hook = await db.get(WebhookEndpoint, ids["hook"])
            assert box_b.decrypt(hook.secret_enc, aad=f"webhook:{hook.id}".encode()) == "HOOK-SECRET-A" and hook.status == "active" and hook.consecutive_failures == 0
            llm = await db.get(AppSetting, "llm.api_key")
            assert llm.is_secret and box_b.decrypt(llm.value, aad=b"llm.api_key") == "LLM-KEY-A"
            assert (await db.get(AppSetting, "storage.smb_server")).value == json.dumps("files.old-corp.test")
            # интеграции: ключ остался рабочим (хэш перенесён), права и адреса те же
            cl = await db.get(ApiClient, ids["client"])
            assert cl.scopes == ["meetings:read"] and cl.ip_allowlist == ["10.0.0.0/8"]
            key = await db.get(ApiKey, ids["key"])
            _, secret = pkeys.parse_key(ids["api_key_full"])
            assert pkeys.secret_matches(secret, key.secret_hash)
            # переговорки: постоянные перенесены с теми же id, пароль работает, временная — нет, данных встреч нет
            rooms = {r.name: r for r in (await db.execute(select(Room))).scalars().all()}
            assert set(rooms) == {"Совещания", "Открытая"} and str(rooms["Совещания"].id) == ids["pw_room"]
            from app.security.passwords import verify_room_password
            assert verify_room_password(rooms["Совещания"].password_hash, "room-pass-1") and not verify_room_password(rooms["Совещания"].password_hash, "bad")
            assert (await db.execute(select(Meeting))).scalars().all() == [] and (await db.execute(select(TranscriptSegment))).scalars().all() == []
            assert (await db.get(MailTemplate, ids["tmpl"])).subject == "Итоги {{room}}"
            return True
        assert db_run(c, check)
        # --- пользователи и локальный администратор не тронуты, вход работает
        assert db_run(c, lambda db: _count(db, User)) == users_before
        c.cookies.clear()
        assert c.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}).status_code == 200
        # --- аудит: факт, без секретов
        text = json.dumps(db_run(c, lambda db: _audit(db)), ensure_ascii=False)
        assert "config.import" in text and "LDAP-PASS-A" not in text and pw not in text
        # --- повторный импорт того же архива идемпотентен
        login(c, "root")
        again = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert again.status_code == 200 and db_run(c, lambda db: _count(db, Room)) == 2 and db_run(c, lambda db: _count(db, LdapProfile)) == 1


async def _count(db, model):
    from sqlalchemy import func
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


def test_a_failure_in_the_middle_of_applying_leaves_the_server_unchanged(tmp_path, directory, monkeypatch):
    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    with running_app(sa, directory) as ca:
        seed(ca)
        blob, pw = export(ca)
    with running_app(sb, directory) as c:
        login(c, "root")
        put_settings(c, "general", max_participants=7) if False else None
        before = db_run(c, lambda db: _snapshot(db))
        from app.services.config_backup import importer

        real = importer.IMPORT_ACTOR
        calls = {"n": 0}
        orig = importer.AppSetting

        class Boom(orig):                                                     # сбой при записи настроек — после того, как таблицы уже вставлены
            def __init__(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("диск переполнен")
                super().__init__(*a, **k)

        monkeypatch.setattr(importer, "AppSetting", Boom)
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 500 and "отменены" in json.dumps(r.json(), ensure_ascii=False)
        monkeypatch.setattr(importer, "AppSetting", orig)
        assert db_run(c, lambda db: _snapshot(db)) == before, "после сбоя состояние сервера прежнее"
        failed = [a for a in db_run(c, lambda db: _audit(db)) if a[0] == "config.import"]
        assert failed and failed[-1][1]["result"] == "failed"
        ok = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert ok.status_code == 200, "после устранения причины импорт проходит"


async def _snapshot(db):
    from sqlalchemy import func
    out = {}
    for m in (Room, LdapProfile, MailProfile, StorageProfile, ApiProfile, WebhookEndpoint, ApiClient, MailTemplate, AppSetting):
        out[m.__name__] = (await db.execute(select(func.count()).select_from(m))).scalar_one()
    return out


def test_conflicting_room_with_meetings_blocks_the_import_but_an_empty_one_is_replaced(tmp_path, directory):
    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    with running_app(sa, directory) as ca:
        login(ca, "root")
        make_room(ca, name="Планёрка", slug="standup")
        blob, pw = export(ca)
    with running_app(sb, directory) as c:
        room = make_room(c, name="Своя планёрка", slug="standup")                 # занятый адрес на новом сервере
        a = _join(c, "alice", room["id"])
        login(c, "root")
        prev = upload(c, "/api/v1/admin/config/import/preview", blob, pw).json()
        assert any("есть встречи" in x for x in prev["conflicts"])
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 422 and "встречи" in json.dumps(r.json(), ensure_ascii=False)
        assert db_run(c, lambda db: _names(db)) == ["Своя планёрка"], "ничего не изменено"


async def _names(db):
    return sorted(r.name for r in (await db.execute(select(Room))).scalars().all())


def test_hostile_content_inside_a_valid_archive_is_rejected(tmp_path, directory):
    s = make_settings(tmp_path)
    with running_app(s, directory) as c:
        login(c, "root")
        pw = K.generate_password()
        good = {"manifest": {"kind": "peregovorka-config", "schema": 1, "app_version": "1"}, "settings": {}, "tables": {}, "files": {}}
        for mutate, text in ((lambda p: p["tables"].update(users=[{"id": str(uuid.uuid4())}]), "не входит в состав"),
                             (lambda p: p["tables"].update(rooms=[{"id": str(uuid.uuid4()), "evil_column": 1}]), "неизвестные столбцы"),
                             (lambda p: p["settings"].update(nope={"a": 1}), "неизвестная группа"),
                             (lambda p: p["files"].update({"../../etc/passwd": base64.b64encode(b"x").decode()}), "не входит в состав"),
                             (lambda p: p["files"].update({"branding/logo": "not base64!!"}), "повреждённые данные"),
                             (lambda p: p["tables"].update(rooms=[{"id": "не-uuid"}]), "не соответствует типу")):
            import copy
            p = copy.deepcopy(good)
            mutate(p)
            blob = K.seal(p, pw, app_version="1")
            r = upload(c, "/api/v1/admin/config/import/preview", blob, pw)
            assert r.status_code == 422 and text in json.dumps(r.json(), ensure_ascii=False), (text, r.text)
        # слишком большое тело отсекается до разбора
        r = c.post("/api/v1/admin/config/import/inspect", content=b"x" * 10, headers={"content-type": "application/octet-stream", "content-length": str(K.MAX_FILE_BYTES + 1)})
        assert r.status_code in (400, 413)


def test_certificates_documents_sip_templates_branding_roles_and_what_must_not_move(tmp_path, directory):
    import datetime as dt
    import io

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from PIL import Image

    from app.models import CaCertificate, LegalDocument, LegalRevision, ProtocolTemplate, RoomAcl, RoomModerator, SipProfile

    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Корпоративный УЦ")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    png = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 30, 30, 255)).save(png, "PNG")
    box_a = None
    with running_app(sa, directory) as ca:
        login(ca, "root")
        room = make_room(ca, name="Руководство")
        assert ca.post("/api/v1/admin/ca", json={"pem": pem, "label": "Корневой УЦ"}).status_code in (200, 201)
        ca.app_obj.state.branding.save("logo", png.getvalue())
        sip_id, tid_g, tid_u = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        box_a = ca.app_obj.state.settings_svc.box

        async def rows(db):
            db.add(SipProfile(id=sip_id, name="АТС", host="pbx.old-corp.test", port=5060, username="trunk", secret_enc=box_a.encrypt("SIP-PASS-A", aad=f"sip_profile.{sip_id}".encode()),
                              lk_outbound_trunk_id="ST_old_out", lk_inbound_trunk_id="ST_old_in"))
            db.add(LegalDocument(kind="privacy", title="Политика", draft_md="черновик", content_md="Опубликованный текст", published_title="Политика", version=2, published=True, require_consent=True))
            db.add(LegalRevision(kind="privacy", version=2, title="Политика", content_md="Опубликованный текст", published_by="Админ"))
            db.add(ProtocolTemplate(id=tid_g, name="Общий шаблон", kind="protocol", instruction="Пиши кратко", scope="global"))
            root = (await db.execute(select(User).where(User.sam_account_name == "root"))).scalars().first()
            db.add(ProtocolTemplate(id=tid_u, name="Личный шаблон", kind="any", instruction="личное", scope="user", owner_user_id=root.id))
            db.add(RoomModerator(room_id=uuid.UUID(room["id"]), subject_type="user", subject_ref="guid-руководителя", display_name="Иван"))
            await db.commit()
        db_run(ca, rows)
        blob, pw = export(ca)
    with running_app(sb, directory) as c:
        login(c, "root")
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 200, r.text
        sip_warn = [x for x in r.json()["checks"] if x["component"] == "SIP-телефония"]
        assert sip_warn and sip_warn[0]["status"] == "needs_attention" and "синхронизац" in sip_warn[0]["message"]
        box_b = c.app_obj.state.settings_svc.box

        async def check(db):
            sip = await db.get(SipProfile, sip_id)
            assert box_b.decrypt(sip.secret_enc, aad=f"sip_profile.{sip_id}".encode()) == "SIP-PASS-A"
            assert sip.lk_outbound_trunk_id is None and sip.lk_inbound_trunk_id is None, "идентификаторы транков старого LiveKit не переносятся"
            ca_row = (await db.execute(select(CaCertificate))).scalars().one()
            assert "BEGIN CERTIFICATE" in ca_row.pem and ca_row.label == "Корневой УЦ"
            doc = await db.get(LegalDocument, "privacy")
            assert doc.published and doc.version == 2 and doc.content_md == "Опубликованный текст" and doc.require_consent
            assert (await db.execute(select(LegalRevision))).scalars().one().published_by == "Админ"
            assert [t.name for t in (await db.execute(select(ProtocolTemplate))).scalars().all()] == ["Общий шаблон"], "личные шаблоны пользователей не переносятся"
            mod = (await db.execute(select(RoomModerator))).scalars().one()
            assert mod.subject_ref == "guid-руководителя" and mod.display_name == "Иван"
            return True
        assert db_run(c, check)
        assert c.app_obj.state.branding.read("logo"), "оформление (логотип) перенесено"
        prev_users = [r.sam_account_name for r in db_run(c, lambda db: _users(db))]
        assert "root" in prev_users


async def _users(db):
    return (await db.execute(select(User))).scalars().all()


# ----------------------------------------------------------------------------------------------------- права переговорок без переноса пользователей
def test_room_permissions_work_on_the_new_server_without_any_users_being_transferred(tmp_path, directory):
    from .conftest import STAFF_GROUP

    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    alice_guid = directory.users["alice"][1].ad_guid
    with running_app(sa, directory) as ca:
        login(ca, "root")
        room = make_room(ca, name="Закрытая", acl=[{"subject_type": "group", "subject_ref": STAFF_GROUP}],
                         moderators=[{"subject_type": "user", "subject_ref": alice_guid, "display_name": "Алиса"}])
        make_room(ca, name="Только администраторы", acl=[{"subject_type": "group", "subject_ref": "cn=admins,dc=corp,dc=test"}])
        blob, pw = export(ca)
    with running_app(sb, directory) as c:
        login(c, "root")
        assert db_run(c, lambda db: _names_users(db)) == ["root"], "на новом сервере пользователей каталога ещё нет"
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 200, r.text
        assert db_run(c, lambda db: _names_users(db)) == ["root"], "импорт не создал и не удалил пользователей"
        # первый вход Алисы после импорта: доступ по группе и роль руководителя по идентификатору каталога — без какого-либо переноса пользователей
        login(c, "alice")
        visible = {x["name"] for x in c.get("/api/v1/rooms").json()}
        assert "Закрытая" in visible and "Только администраторы" not in visible
        assert c.get(f"/api/v1/rooms/{room['id']}/manage").status_code == 200, "Алиса — руководитель по идентификатору каталога"
        assert c.patch(f"/api/v1/rooms/{room['id']}/manage", json={"welcome_message": "Привет"}).status_code == 200
        login(c, "bob")                                                       # тот же допуск по группе, но не руководитель
        assert "Закрытая" in {x["name"] for x in c.get("/api/v1/rooms").json()}
        assert c.get(f"/api/v1/rooms/{room['id']}/manage").status_code == 403
        login(c, "carol")                                                     # чужая группа
        assert "Закрытая" not in {x["name"] for x in c.get("/api/v1/rooms").json()}


async def _names_users(db):
    return sorted(u.sam_account_name for u in (await db.execute(select(User))).scalars().all())


# ----------------------------------------------------------------------------------------------------- откат файлов и сертификатов
def _cert_pem() -> str:
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "УЦ " + uuid.uuid4().hex[:6])])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _png(color) -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGBA", (32, 32), color).save(b, "PNG")
    return b.getvalue()


def _prepare_pair(tmp_path, directory):
    """Сервер А с логотипом и сертификатом; архив; возвращает настройки Б и архив."""
    sa, sb = make_settings(tmp_path / "a"), make_settings(tmp_path / "b")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    with running_app(sa, directory) as ca:
        login(ca, "root")
        make_room(ca, name="Комната А")
        assert ca.post("/api/v1/admin/ca", json={"pem": _cert_pem(), "label": "УЦ из архива"}).status_code in (200, 201)
        ca.app_obj.state.branding.save("logo", _png((200, 0, 0, 255)))
        blob, pw = export(ca)
    return sb, blob, pw


def _state_of_files(s) -> dict:
    data = Path(s.data_dir)
    out = {}
    for rel in ("branding/logo.png", "ca/bundle.pem"):
        p = data / rel
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    out["leftovers"] = sorted(str(p.relative_to(data)) for p in data.rglob("*") if p.is_file() and (".import-" in p.name))
    return out


def _prepare_target_with_existing_files(c):
    login(c, "root")
    assert c.post("/api/v1/admin/ca", json={"pem": _cert_pem(), "label": "Свой УЦ"}).status_code in (200, 201)
    c.app_obj.state.branding.save("logo", _png((0, 0, 200, 255)))


def test_failure_while_preparing_files_leaves_database_and_files_untouched(tmp_path, directory, monkeypatch):
    sb, blob, pw = _prepare_pair(tmp_path, directory)
    with running_app(sb, directory) as c:
        _prepare_target_with_existing_files(c)
        before_files, before_db = _state_of_files(sb), db_run(c, lambda db: _snapshot(db))
        from app.services.branding import BrandingStore

        monkeypatch.setattr(BrandingStore, "stage", lambda self, kind, png: (_ for _ in ()).throw(OSError("нет места на диске")))
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 422 and "изображение" in json.dumps(r.json(), ensure_ascii=False)
        assert _state_of_files(sb) == before_files and db_run(c, lambda db: _snapshot(db)) == before_db
        assert db_run(c, lambda db: _count(db, Room)) == 0


def test_failure_at_the_final_commit_returns_the_previous_logo_and_certificate_bundle(tmp_path, directory, monkeypatch):
    sb, blob, pw = _prepare_pair(tmp_path, directory)
    with running_app(sb, directory) as c:
        _prepare_target_with_existing_files(c)
        before_files, before_db = _state_of_files(sb), db_run(c, lambda db: _snapshot(db))
        from sqlalchemy.ext.asyncio import AsyncSession

        real = AsyncSession.commit
        state = {"armed": False}
        from app.services.config_backup import files as cf

        real_promote = cf.promote

        def promote_then_arm(staged):
            promo = real_promote(staged)
            state["armed"] = True                                                   # файлы уже подменены; следующая фиксация базы (импорта) упадёт
            return promo

        async def boom(self):
            if state["armed"]:
                state["armed"] = False
                raise RuntimeError("сбой при фиксации")
            return await real(self)

        monkeypatch.setattr(cf, "promote", promote_then_arm)
        monkeypatch.setattr(AsyncSession, "commit", boom)
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 500 and "отменены" in json.dumps(r.json(), ensure_ascii=False)
        assert _state_of_files(sb) == before_files, "логотип и набор сертификатов вернулись к прежним, временных и резервных файлов не осталось"
        assert db_run(c, lambda db: _snapshot(db)) == before_db
        assert c.app_obj.state.branding.read("logo") == _png((0, 0, 200, 255))
        monkeypatch.setattr(cf, "promote", real_promote)
        monkeypatch.setattr(AsyncSession, "commit", real)
        ok = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert ok.status_code == 200, "после устранения причины импорт проходит"
        after = _state_of_files(sb)
        assert after["branding/logo.png"] != before_files["branding/logo.png"] and after["ca/bundle.pem"] != before_files["ca/bundle.pem"] and after["leftovers"] == []


def test_success_replaces_files_and_the_bundle_matches_the_database(tmp_path, directory):
    sb, blob, pw = _prepare_pair(tmp_path, directory)
    with running_app(sb, directory) as c:
        _prepare_target_with_existing_files(c)
        r = upload(c, "/api/v1/admin/config/import/apply", blob, pw, **{"x-admin-password": "root-pass", "x-import-confirm": "yes"})
        assert r.status_code == 200, r.text
        bundle = (Path(sb.data_dir) / "ca" / "bundle.pem").read_text(encoding="ascii")
        from app.models import CaCertificate

        pems = [x.pem for x in db_run(c, lambda db: _all(db, CaCertificate))]
        assert len(pems) == 2 and all(p.strip() in bundle for p in pems), "набор доверенных сертификатов соответствует базе: и свой УЦ, и УЦ из архива"
        assert _state_of_files(sb)["leftovers"] == []


async def _all(db, model):
    return (await db.execute(select(model))).scalars().all()


# ----------------------------------------------------------------------------------------------------- окружение
def test_environment_of_the_source_server_is_reported_without_secrets_and_compared_with_the_new_one(tmp_path, directory):
    sa = make_settings(tmp_path / "a", ldap_access_group_dn="cn=old-staff,dc=old,dc=test", default_audio_retention_days=90, app_public_url="https://old.example.test")
    sb = make_settings(tmp_path / "b", ldap_access_group_dn="", default_audio_retention_days=30, app_public_url="https://new.example.test")
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    with running_app(sa, directory) as ca:
        login(ca, "root")
        blob, pw = export(ca)
    header, payload = K.open_(blob, pw)
    env = payload["manifest"]["environment"]
    assert env["ldap_access_group_dn"] == "cn=old-staff,dc=old,dc=test" and env["default_audio_retention_days"] == "90"
    assert env["app_master_key"] in ("задан", "не задан") and sa.app_master_key not in json.dumps(payload) and "internal-test-token" not in json.dumps(payload)
    assert env["postgres_password"] in ("задан", "не задан") and env["database_url"] in ("задан", "не задан")
    with running_app(sb, directory) as c:
        login(c, "root")
        p = upload(c, "/api/v1/admin/config/import/preview", blob, pw).json()
        names = {x["name"]: x for x in p["environment"]["important"]}
        assert names["LDAP_ACCESS_GROUP_DN"]["source"] == "cn=old-staff,dc=old,dc=test" and names["LDAP_ACCESS_GROUP_DN"]["here"] == "" and "допуска" in names["LDAP_ACCESS_GROUP_DN"]["note"]
        assert "DEFAULT_AUDIO_RETENTION_DAYS" in names and "APP_PUBLIC_URL" in names
        assert "APP_MASTER_KEY" not in names, "ключи шифрования у серверов разные по определению — это не отличие, которое нужно сверять"
