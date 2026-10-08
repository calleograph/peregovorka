"""Единый слой хранения (профили хранилищ, подпапки) и вложения чата: проверки, права, отказ хранилища, удаление."""
from __future__ import annotations

from pathlib import Path

from .conftest import login, make_room, put_settings
from .test_collab_guests import ghead, guest_join, guest_room
from .test_transcripts import _join

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"x" * 64


def api(mid, tail=""):
    return f"/api/v1/meetings/{mid}/chat{tail}"


def upload(client, mid, name, data, headers=None):
    return client.post(api(mid, "/attachments"), params={"name": name}, content=data, headers=headers or {})


def profile(client, settings, name="Файловый сервер №1", **over):
    login(client, "root")
    body = {"name": name, "kind": "local", "config": {"local_path": str(Path(settings.data_dir) / "fileserver")}, **over}
    r = client.post("/api/v1/admin/storages", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------------------------- вложения
def test_image_attachment_roundtrip_with_inline_preview_and_safe_headers(client, settings):
    room = make_room(client)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    r = upload(client, mid, "..\\..\\скрин <1>.png", PNG)
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["kind"] == "image" and att["mime"] == "image/png" and att["size"] == len(PNG) and "\\" not in att["name"] and "<" not in att["name"]
    # файл лежит в хранилище приложения, в имени пути нет ничего от клиента
    stored = [p for p in (Path(settings.data_dir) / "chat-files").rglob("*") if p.is_file()]
    assert len(stored) == 1 and stored[0].name == f"{att['id']}.png"
    msg = client.post(api(mid), json={"text": "", "attachments": [att["id"]]})
    assert msg.status_code == 201 and msg.json()["attachments"][0]["id"] == att["id"]
    history = client.get(api(mid)).json()["messages"]
    assert history[-1]["attachments"][0]["name"] == att["name"]
    got = client.get(api(mid, f"/attachments/{att['id']}"))
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"].startswith("inline") and got.headers["x-content-type-options"] == "nosniff"
    dl = client.get(api(mid, f"/attachments/{att['id']}"), params={"download": "true"})
    assert dl.headers["content-disposition"].startswith("attachment") and dl.headers["content-type"] == "application/octet-stream"


def test_regular_file_is_download_only_and_text_export_lists_it(client):
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "Отчёт.pdf", PDF).json()
    assert att["kind"] == "file"
    client.post(api(mid), json={"text": "Смотрите отчёт", "attachments": [att["id"]]})
    got = client.get(api(mid, f"/attachments/{att['id']}"))
    assert got.headers["content-disposition"].startswith("attachment") and got.headers["content-type"] == "application/octet-stream"
    assert "[файл: Отчёт.pdf" in client.get(api(mid, ".txt")).text


def test_dangerous_or_mismatched_files_are_rejected(client):
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    assert upload(client, mid, "run.exe", b"MZ" + b"\x00" * 40).status_code == 422
    assert upload(client, mid, "page.html", b"<script>alert(1)</script>").status_code == 422
    assert upload(client, mid, "photo.png", b"<script>alert(1)</script>").status_code == 422       # расширение картинки, содержимое — нет
    assert upload(client, mid, "fake.pdf", b"not a pdf at all").status_code == 422
    assert upload(client, mid, "noext", b"data").status_code == 422
    assert upload(client, mid, "empty.txt", b"").status_code == 422
    assert upload(client, mid, "disguised.txt", PNG).status_code == 422                              # картинка под видом текста


def test_size_limit_and_files_per_message_are_configurable(client):
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    put_settings(client, "chat_files", max_size_mb=1, max_files_per_message=2)
    login(client, "alice")
    assert upload(client, mid, "big.txt", b"a" * (1024 * 1024 + 1)).status_code == 413
    ids = [upload(client, mid, f"f{i}.txt", b"hello").json()["id"] for i in range(3)]
    assert client.post(api(mid), json={"text": "x", "attachments": ids}).status_code == 422
    assert client.post(api(mid), json={"text": "x", "attachments": ids[:2]}).status_code == 201
    put_settings(client, "chat_files", allowed_extensions="pdf")
    login(client, "alice")
    assert upload(client, mid, "a.txt", b"hello").status_code == 422
    assert client.put("/api/v1/admin/settings/chat_files", json={"allowed_extensions": "pdf,exe"}).status_code in (401, 403, 422)


def test_forbidden_extensions_cannot_be_enabled_by_admin(client):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/chat_files", json={"allowed_extensions": "pdf,html"})
    assert r.status_code == 422 and "html" in r.text


def test_attachment_access_rules(client):
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "a.txt", b"hello").json()
    # до отправки сообщения файл видит только загрузивший
    other = make_room(client, name="Другая")
    bid = _join(client, "bob", other["id"])["meeting_id"]
    assert client.get(api(mid, f"/attachments/{att['id']}")).status_code == 404            # bob не в этой встрече
    login(client, "alice")
    assert client.get(api(mid, f"/attachments/{att['id']}")).status_code == 200
    # чужое вложение нельзя прикрепить к своему сообщению и к сообщению в другой встрече
    login(client, "bob")
    assert client.post(api(bid), json={"text": "x", "attachments": [att["id"]]}).status_code == 422
    login(client, "alice")
    assert client.post(api(bid), json={"text": "x", "attachments": [att["id"]]}).status_code in (404, 422)
    sent = client.post(api(mid), json={"text": "x", "attachments": [att["id"]]})
    assert sent.status_code == 201
    assert client.post(api(mid), json={"text": "y", "attachments": [att["id"]]}).status_code == 422   # повторно нельзя
    assert client.delete(api(mid, f"/attachments/{att['id']}")).status_code == 409                    # отправленное не удаляется


def test_pending_attachment_can_be_removed_and_file_goes_away(client, settings):
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "a.txt", b"hello").json()
    root = Path(settings.data_dir) / "chat-files"
    assert len([p for p in root.rglob("*") if p.is_file()]) == 1
    assert client.delete(api(mid, f"/attachments/{att['id']}")).status_code == 204
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_guest_can_attach_and_see_files_in_own_meeting(client):
    room = guest_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    g = guest_join(client, room["guest_token"]).json()
    r = client.post(api(mid, "/attachments"), params={"name": "g.txt"}, content=b"guest file", headers=ghead(g))
    assert r.status_code == 201
    sent = client.post(api(mid), json={"text": "", "attachments": [r.json()["id"]]}, headers=ghead(g))
    assert sent.status_code == 201
    assert client.get(api(mid, f"/attachments/{r.json()['id']}"), headers=ghead(g)).content == b"guest file"


def test_storage_failure_is_a_clear_error_and_is_journaled(client, monkeypatch):
    from app.services.storage import StorageError

    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]

    async def boom(self, db):
        raise StorageError("SMB: не удалось подключиться/авторизоваться (TimeoutError)")

    monkeypatch.setattr("app.services.filestore.FileStore.chat_backend", boom)
    r = upload(client, mid, "a.txt", b"hello")
    assert r.status_code == 503 and "хранилище" in r.json()["detail"].lower()
    login(client, "root")
    client.portal.call(client.app_obj.state.journal.flush)
    items = client.get("/api/v1/admin/journal", params={"limit": 200, "category": "storage"}).json()["items"]
    assert any(e["event"] == "chat_file_save_failed" for e in items)


# ------------------------------------------------------------------------- профили хранилищ
def test_storage_profile_is_created_once_and_functions_only_choose_it(client, settings):
    p = profile(client, settings)
    assert p["kind"] == "local" and p["secret_set"] is False and p["address"].endswith("fileserver")
    for group in ("chat_files", "audio_storage", "storage"):
        extra = {"enabled": True} if group != "chat_files" else {}
        put_settings(client, group, profile_id=p["id"], **extra)
    listed = client.get("/api/v1/admin/storages").json()
    assert set(listed["items"][0]["used_by"]) == {"Вложения чата", "Записи аудио", "Протоколы и материалы встречи"}
    assert "Chat" in listed["folders"] and "Audio" in listed["folders"]
    # используемое хранилище удалить нельзя
    assert client.delete(f"/api/v1/admin/storages/{p['id']}").status_code == 409
    # несуществующее хранилище выбрать нельзя
    assert client.put("/api/v1/admin/settings/chat_files", json={"profile_id": "00000000-0000-0000-0000-000000000000"}).status_code == 422


def test_profile_test_creates_subfolders_and_chat_files_go_to_chat_folder(client, settings):
    p = profile(client, settings)
    res = client.post(f"/api/v1/admin/storages/{p['id']}/test").json()
    assert res["ok"] is True
    root = Path(settings.data_dir) / "fileserver"
    assert {"Audio", "Transcripts", "Protocols", "Chat", "Boards", "Logs"} <= {d.name for d in root.iterdir() if d.is_dir()}
    put_settings(client, "chat_files", profile_id=p["id"])
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "a.txt", b"hello").json()
    assert (root / "Chat" / "files" / mid / f"{att['id']}.txt").read_bytes() == b"hello"
    assert client.get(api(mid, f"/attachments/{att['id']}")).content == b"hello"      # читается из того профиля, куда писали
    # после смены профиля старое вложение остаётся читаемым
    login(client, "root")
    put_settings(client, "chat_files", profile_id="")
    login(client, "alice")
    assert client.get(api(mid, f"/attachments/{att['id']}")).content == b"hello"


def test_profile_secret_is_never_returned_and_delete_blocked_by_attachments(client, settings):
    login(client, "root")
    r = client.post("/api/v1/admin/storages", json={"name": "SMB", "kind": "smb", "secret": "TopSecret!1",
                                                     "config": {"smb_server": "files.corp", "smb_share": "share", "smb_base_path": "peregovorka",
                                                                "smb_username": "svc", "smb_domain": "CORP"}})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["secret_set"] is True and "TopSecret" not in r.text and out["address"] == "\\\\files.corp\\share\\peregovorka"
    assert "TopSecret" not in client.get("/api/v1/admin/storages").text
    bad = client.post("/api/v1/admin/storages", json={"name": "bad", "kind": "smb", "config": {"smb_server": "x"}})
    assert bad.status_code == 422                                            # нет ресурса
    dup = client.post("/api/v1/admin/storages", json={"name": "SMB", "kind": "local", "config": {"local_path": str(Path(settings.data_dir) / "x")}})
    assert dup.status_code == 422
    # администраторы — только администраторы
    login(client, "alice")
    assert client.get("/api/v1/admin/storages").status_code == 403


def test_smb_profile_is_used_through_the_common_interface(client, settings, monkeypatch):
    """Вложения чата на SMB: потребитель не знает про SMB — работает тот же интерфейс хранилища с подпапкой Chat/files."""
    written: dict[str, bytes] = {}

    class FakeSmb:
        def __init__(self, server, share, base_path, username, password, domain=""):
            assert (server, share, base_path, username, password, domain) == ("files.corp", "share", "pg", "svc", "TopSecret!1", "CORP")

        def write_bytes(self, rel, data):
            written[rel] = data
            return "\\\\files.corp\\share\\" + rel

        def read_bytes(self, rel):
            return written[rel]

        def delete(self, rel):
            written.pop(rel, None)

        def exists(self, rel):
            return rel in written

        def list_dir(self, rel):
            return []

        def delete_dir(self, rel):
            pass

        def test(self):
            return "ok"

    monkeypatch.setattr("app.services.storage.SmbStorage", FakeSmb)
    login(client, "root")
    p = client.post("/api/v1/admin/storages", json={"name": "SMB", "kind": "smb", "secret": "TopSecret!1", "config": {
        "smb_server": "files.corp", "smb_share": "share", "smb_base_path": "pg", "smb_username": "svc", "smb_domain": "CORP"}}).json()
    put_settings(client, "chat_files", profile_id=p["id"])
    room = make_room(client)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "doc.pdf", PDF).json()
    assert list(written) == [f"Chat/files/{mid}/{att['id']}.pdf"]
    assert client.get(api(mid, f"/attachments/{att['id']}")).content == PDF


def test_materials_are_split_into_profile_subfolders_and_attachments_follow_retention(client, settings):
    from .test_admin_features import _drain

    p = profile(client, settings)
    put_settings(client, "storage", enabled=True, profile_id=p["id"])
    put_settings(client, "chat_files", profile_id=p["id"])
    room = make_room(client, text_retention_days=0)
    mid = _join(client, "alice", room["id"])["meeting_id"]
    att = upload(client, mid, "a.txt", b"hello").json()
    client.post(api(mid), json={"text": "итоги в файле", "attachments": [att["id"]]})
    client.app_obj.state.protocols.flush_delay = 0
    assert client.post(f"/api/v1/meetings/{mid}/end").status_code == 204
    _drain(client)
    root = Path(settings.data_dir) / "fileserver"
    assert list((root / "Transcripts").rglob("protocol.txt")), "стенограмма в Transcripts/"
    chats = list((root / "Chat").rglob("chat.txt"))
    assert chats and "[файл: a.txt" in chats[0].read_text(encoding="utf-8"), "переписка со ссылкой на файл в Chat/"
    assert (root / "Chat" / "files" / mid / f"{att['id']}.txt").exists()
    # срок хранения текста 0 → переписка и её файлы удаляются вместе (после льготного периода — имитируем администратором)
    from datetime import timedelta

    from app.models import Meeting, utcnow
    from sqlalchemy import update

    async def age():
        async with client.app_obj.state.session_maker() as db:
            await db.execute(update(Meeting).values(ended_at=utcnow() - timedelta(hours=1)))
            await db.commit()

    client.portal.call(age)
    login(client, "root")
    assert client.post("/api/v1/admin/retention/run").status_code == 200
    assert not (root / "Chat" / "files" / mid / f"{att['id']}.txt").exists()
