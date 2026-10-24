"""Корпоративные чаты: личные и групповые переписки, права (членство при каждом обращении, подмена идентификаторов), сообщения без дублей,
правка и удаление своих, прочитанность и события на несколько устройств, очистка и удаление только у себя, папки, поиск, вложения, пересылка,
поиск сотрудников (каталог, отключённые), присутствие и «сообщить о появлении в сети», срок хранения, ограничение частоты, аудит."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.models import AuditLog, ChatMessage, User, utcnow

from .conftest import login

API = "/api/v1"
C = f"{API}/chats"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def uid(client, user: str) -> str:
    login(client, user)
    return client.get(f"{API}/auth/me").json()["user"]["id"]


def _db(client, fn):
    async def go():
        async with client.app_obj.state.session_maker() as db:
            return await fn(db)
    return client.portal.call(go)


def direct(client, me: str, other_id: str) -> str:
    login(client, me)
    r = client.post(f"{C}/direct", json={"id": other_id})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def say(client, chat: str, text: str, **kw) -> dict:
    r = client.post(f"{C}/{chat}/messages", json={"text": text, "client_id": kw.pop("client_id", uuid.uuid4().hex), **kw})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_direct_chat_is_unique_per_pair_and_messages_are_idempotent(client):
    bob = uid(client, "bob")
    alice = uid(client, "alice")
    cid = direct(client, "alice", bob)
    assert direct(client, "bob", alice) == cid, "у пары одна личная переписка — открывается существующая"
    assert client.post(f"{C}/direct", json={"id": alice}).json()["created"] is False
    login(client, "alice")
    assert client.post(f"{C}/direct", json={"id": alice}).status_code == 422, "сам с собой — нельзя"
    m1 = say(client, cid, "Привет, Боб!\nКак дела?", client_id="retry-0001")
    again = client.post(f"{C}/{cid}/messages", json={"text": "Привет, Боб!\nКак дела?", "client_id": "retry-0001"})
    assert again.status_code == 200 and again.json()["id"] == m1["id"], "повтор после сбоя сети — то же сообщение, без дубля"
    assert _db(client, lambda db: _count(db, ChatMessage.chat_id == uuid.UUID(cid))) == 1
    login(client, "bob")
    lst = client.get(C).json()
    it = lst["items"][0]
    assert it["id"] == cid and it["title"] == "Alice A" and it["unread"] == 1 and lst["unread"] == 1 and it["last"]["text"].startswith("Привет, Боб!")
    assert client.get(f"{C}/unread").json()["unread"] == 1
    page = client.get(f"{C}/{cid}/messages").json()
    assert [m["text"] for m in page["messages"]] == ["Привет, Боб!\nКак дела?"] and page["messages"][0]["author_name"] == "Alice A"
    assert client.post(f"{C}/{cid}/read", json={"last_id": m1["id"]}).json()["last_read_id"] == m1["id"]
    assert client.get(f"{C}/unread").json()["unread"] == 0
    r = client.get(f"{C}/{cid}/messages/{m1['id']}/receipts")
    assert r.status_code == 200
    login(client, "alice")
    assert client.get(f"{C}/{cid}/messages/{m1['id']}/receipts").json()["items"] == [{"user_id": bob, "name": "Bob B", "read": True}]


def _count(db, cond):
    async def go():
        return (await db.execute(select(func.count()).select_from(ChatMessage).where(cond))).scalar_one()
    return go()


def test_outsiders_get_404_everywhere_and_ids_cannot_be_swapped(client):
    bob, carol = uid(client, "bob"), uid(client, "carol")
    cid = direct(client, "alice", bob)
    m = say(client, cid, "секрет")
    f = client.post(f"{C}/{cid}/files", params={"name": "s.png"}, content=PNG).json()
    say(client, cid, "", files=[f["id"]])
    own = direct(client, "carol", bob)                                   # у Кэрол есть свой чат с Бобом
    login(client, "carol")
    for path in (f"{C}/{cid}", f"{C}/{cid}/messages", f"{C}/{cid}/search?q=секрет", f"{C}/{cid}/files/{f['id']}", f"{C}/{cid}/messages/{m['id']}/receipts"):
        assert client.get(path).status_code == 404, path
    assert client.post(f"{C}/{cid}/messages", json={"text": "влезть", "client_id": uuid.uuid4().hex}).status_code == 404
    assert client.get(f"{C}/{own}/files/{f['id']}").status_code == 404, "файл чужого чата через свой чат не отдаётся"
    assert client.post(f"{C}/{own}/messages", json={"text": "", "files": [f["id"]], "client_id": uuid.uuid4().hex}).status_code == 422
    assert client.post(f"{C}/{own}/messages", json={"forward": {"chat_id": cid, "message_id": m["id"]}, "client_id": uuid.uuid4().hex}).status_code == 404
    assert client.post(f"{C}/{own}/messages", json={"text": "ответ", "reply_to": m["id"], "client_id": uuid.uuid4().hex}).status_code == 422
    assert all(i["id"] != cid for i in client.get(C).json()["items"])
    assert client.get(f"{C}/search/all", params={"q": "секрет"}).json()["messages"] == []
    assert carol


def test_edit_and_delete_only_own_messages_delete_removes_files(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    f = client.post(f"{C}/{cid}/files", params={"name": "pic.png"}, content=PNG).json()
    m = say(client, cid, "черновик", files=[f["id"]])
    assert m["files"][0]["kind"] == "image"
    assert client.patch(f"{C}/{cid}/messages/{m['id']}", json={"text": "исправлено"}).json()["edited_at"]
    login(client, "bob")
    assert client.patch(f"{C}/{cid}/messages/{m['id']}", json={"text": "взлом"}).status_code == 403
    assert client.delete(f"{C}/{cid}/messages/{m['id']}").status_code == 403
    assert client.get(f"{C}/{cid}/files/{f['id']}").status_code == 200, "участник видит вложение"
    login(client, "alice")
    assert client.delete(f"{C}/{cid}/messages/{m['id']}").status_code == 200
    got = client.get(f"{C}/{cid}/messages").json()["messages"][0]
    assert got["deleted"] and got["text"] == "" and got["files"] == []
    assert client.get(f"{C}/{cid}/files/{f['id']}").status_code == 404
    rows = _db(client, lambda db: _audit(db, "chat.message_delete"))
    assert rows and "черновик" not in str(rows) and "исправлено" not in str(rows), "в аудит — факт удаления, без текста переписки"


def _audit(db, action):
    async def go():
        return [(a.action, a.details) for a in (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars()]
    return go()


def test_group_admins_members_roles_and_audit(client):
    bob, carol = uid(client, "bob"), uid(client, "carol")
    login(client, "alice")
    r = client.post(f"{C}/group", json={"title": "Проект «Альфа»", "members": [bob]})
    assert r.status_code == 201
    gid = r.json()["id"]
    info = client.get(f"{C}/{gid}").json()
    assert info["kind"] == "group" and info["my_role"] == "admin" and {m["name"]: m["role"] for m in info["members"]} == {"Alice A": "admin", "Bob B": "member"}
    login(client, "bob")
    assert client.post(f"{C}/{gid}/members", json={"members": [carol]}).status_code == 403, "участник не добавляет людей"
    assert client.patch(f"{C}/{gid}", json={"title": "Чужое"}).status_code == 403
    login(client, "alice")
    assert client.post(f"{C}/{gid}/members", json={"members": [carol]}).json()["added"] == 1
    assert client.patch(f"{C}/{gid}/members/{bob}", json={"role": "admin"}).status_code == 200
    me = client.get(f"{API}/auth/me").json()["user"]["id"]
    assert client.patch(f"{C}/{gid}/members/{me}", json={"role": "member"}).status_code == 200
    login(client, "bob")
    assert client.patch(f"{C}/{gid}/members/{bob}", json={"role": "member"}).status_code == 409, "последний администратор остаётся"
    assert client.delete(f"{C}/{gid}/members/{carol}").status_code == 200
    login(client, "carol")
    assert client.get(f"{C}/{gid}/messages").status_code == 404, "исключённый теряет доступ"
    login(client, "bob")
    texts = [m["text"] for m in client.get(f"{C}/{gid}/messages").json()["messages"] if m["kind"] == "system"]
    assert any("создаёт группу" in t for t in texts) and any("добавляет" in t for t in texts) and any("исключает" in t for t in texts)
    actions = {a for a, _ in _db(client, lambda db: _all_audit(db))}
    assert {"chat.group_create", "chat.members_add", "chat.role", "chat.members_remove"} <= actions


def _all_audit(db):
    async def go():
        return [(a.action, a.details) for a in (await db.execute(select(AuditLog).where(AuditLog.action.like("chat.%")))).scalars()]
    return go()


def test_clear_and_delete_only_for_me_and_new_message_brings_chat_back(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    say(client, cid, "раз")
    say(client, cid, "два")
    assert client.post(f"{C}/{cid}/clear").status_code == 200
    assert client.get(f"{C}/{cid}/messages").json()["messages"] == []
    login(client, "bob")
    assert [m["text"] for m in client.get(f"{C}/{cid}/messages").json()["messages"]] == ["раз", "два"], "у собеседника переписка осталась"
    assert client.delete(f"{C}/{cid}").status_code == 200
    assert client.get(C).json()["items"] == []
    login(client, "alice")
    say(client, cid, "три")
    login(client, "bob")
    items = client.get(C).json()["items"]
    assert [i["id"] for i in items] == [cid] and [m["text"] for m in client.get(f"{C}/{cid}/messages").json()["messages"]] == ["три"]


def test_folders_pins_archive_mute_are_personal(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    say(client, cid, "x")
    f = client.post(f"{C}/folders/mine", json={"name": "Работа"}).json()
    assert client.post(f"{C}/folders/mine", json={"name": "Работа"}).status_code == 409
    assert client.patch(f"{C}/{cid}/me", json={"folder_id": f["id"], "pinned": True, "muted": True, "archived": True}).status_code == 200
    it = client.get(C).json()["items"][0]
    assert it["folder_id"] == f["id"] and it["pinned"] and it["muted"] and it["archived"]
    assert client.get(f"{C}/unread").json()["unread"] == 0
    login(client, "bob")
    it = client.get(C).json()["items"][0]
    assert not (it["folder_id"] or it["pinned"] or it["muted"] or it["archived"]), "у собеседника ничего не изменилось"
    assert client.patch(f"{C}/{cid}/me", json={"folder_id": f["id"]}).status_code == 404, "чужая папка недоступна"
    assert client.delete(f"{C}/folders/mine/{f['id']}").status_code == 404
    login(client, "alice")
    assert client.patch(f"{C}/folders/mine/{f['id']}", json={"name": "Проекты"}).status_code == 200
    assert client.delete(f"{C}/folders/mine/{f['id']}").status_code == 200
    it = client.get(C).json()["items"][0]
    assert it["folder_id"] is None and it["id"] == cid, "папка удалена, переписка осталась"


def test_search_in_chat_with_filters_and_paging(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    for i in range(30):
        say(client, cid, f"Отчёт номер {i} по проекту Альфа" if i % 3 == 0 else f"обычное сообщение {i}")
    login(client, "bob")
    say(client, cid, "Отчёт от Боба: «квартальный план» готов")
    r = client.get(f"{C}/{cid}/search", params={"q": "отчёт альфа"}).json()
    assert r["total"] == 10 and all("Альфа" in x["text"] for x in r["items"]) and r["items"][0]["id"] > r["items"][-1]["id"]
    r = client.get(f"{C}/{cid}/search", params={"q": '"квартальный план"'}).json()
    assert r["total"] == 1 and r["items"][0]["author_name"] == "Bob B"
    alice = uid(client, "alice")
    r = client.get(f"{C}/{cid}/search", params={"q": "отчёт", "author": bob}).json()
    assert r["total"] == 1
    r = client.get(f"{C}/{cid}/search", params={"q": "отчёт", "author": alice, "date_from": (utcnow() + timedelta(days=1)).date().isoformat()}).json()
    assert r["total"] == 0
    hit = client.get(f"{C}/{cid}/search", params={"q": "номер 15"}).json()["items"][0]["id"]
    win = client.get(f"{C}/{cid}/messages", params={"around_id": hit, "limit": 10}).json()
    assert hit in [m["id"] for m in win["messages"]] and win["has_more_before"] and win["has_more_after"]
    page = client.get(f"{C}/{cid}/messages", params={"limit": 10}).json()
    older = client.get(f"{C}/{cid}/messages", params={"limit": 10, "before_id": page["messages"][0]["id"]}).json()
    assert len(older["messages"]) == 10 and older["messages"][-1]["id"] < page["messages"][0]["id"], "длинная история — постранично"
    newer = client.get(f"{C}/{cid}/messages", params={"after_id": page["messages"][-2]["id"]}).json()
    assert [m["id"] for m in newer["messages"]] == [page["messages"][-1]["id"]], "после переподключения — только пропущенное"
    g = client.get(f"{C}/search/all", params={"q": "квартальный"}).json()
    assert [m["chat_id"] for m in g["messages"]] == [cid]
    assert client.get(f"{C}/search/all", params={"q": "Bob"}).json()["chats"][0]["id"] == cid


def test_forward_reply_and_attachment_rules(client):
    bob, carol = uid(client, "bob"), uid(client, "carol")
    a_b = direct(client, "alice", bob)
    a_c = direct(client, "alice", carol)
    m = say(client, a_b, "важная новость")
    fwd = say(client, a_c, "", forward={"chat_id": a_b, "message_id": m["id"]})
    assert fwd["text"] == "важная новость" and fwd["forwarded"]["name"] == "Alice A"
    rep = say(client, a_b, "ответ", reply_to=m["id"])
    assert rep["reply_to"]["text"] == "важная новость"
    bad = client.post(f"{C}/{a_b}/files", params={"name": "evil.exe"}, content=b"MZ\x90\x00")
    assert bad.status_code == 422
    fake = client.post(f"{C}/{a_b}/files", params={"name": "pic.png"}, content=b"not a png")
    assert fake.status_code == 422, "тип проверяется по содержимому"
    f = client.post(f"{C}/{a_b}/files", params={"name": "doc.pdf"}, content=b"%PDF-1.4 test").json()
    login(client, "bob")
    assert client.get(f"{C}/{a_b}/files/{f['id']}").status_code == 404, "неотправленное вложение видит только загрузивший"
    login(client, "alice")
    say(client, a_b, "файл", files=[f["id"]])
    login(client, "bob")
    d = client.get(f"{C}/{a_b}/files/{f['id']}")
    assert d.status_code == 200 and d.headers["content-disposition"].startswith("attachment") and d.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in d.headers["content-security-policy"]


def test_people_search_directory_and_disabled_accounts(client, directory):
    directory.add("dave", "dave-pass", name="Dave Directory")              # в каталоге, ещё не входил
    bob = uid(client, "bob")

    async def disable(db):
        (await db.get(User, uuid.UUID(bob))).is_active = False
        await db.commit()
    login(client, "alice")
    r = client.get(f"{C}/people", params={"q": "dave"}).json()
    assert r["users"] and r["users"][0]["source"] == "directory" and r["users"][0]["id"] is None
    ref, login_ = r["users"][0]["ref"], r["users"][0]["login"]
    assert client.post(f"{C}/direct", json={"ref": ref, "login": "bob"}).status_code == 404, "чужой логин к ссылке не подставить"
    cid = client.post(f"{C}/direct", json={"ref": ref, "login": login_}).json()["id"]
    login(client, "dave")
    assert [i["id"] for i in client.get(C).json()["items"]] == [cid], "запись из каталога и вход — один и тот же сотрудник"
    _db(client, disable)
    login(client, "alice")
    assert all(u["name"] != "Bob B" for u in client.get(f"{C}/people", params={"q": "bob"}).json()["users"]), "отключённые не показываются"
    assert client.post(f"{C}/direct", json={"id": bob}).status_code == 404


def test_presence_respects_privacy_and_watch_is_one_shot(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    redis = client.app_obj.state.redis
    from app.services import chats as cs

    async def online():
        return await cs.touch_presence(redis, uuid.UUID(bob), True)
    assert client.post(f"{C}/presence/watch/{bob}", json={"on": True}).status_code == 200
    assert client.get(f"{C}/{cid}").json()["members"][1 if client.get(f"{C}/{cid}").json()["members"][0]["id"] != bob else 0]["watch"] is True
    with client.websocket_connect(f"{API}/ws") as ws:
        ws.send_json({"type": "subscribe_user"})
        assert ws.receive_json()["type"] == "subscribed_user"

        async def came():
            u = await _db_user(client, bob)
            if await cs.touch_presence(redis, u.id, True):
                await cs.came_online(redis, u)
        client.portal.call(came)
        ev = ws.receive_json()
        assert ev["type"] == "chat_presence_online" and ev["user_id"] == bob
    peer = client.get(C).json()["items"][0]["peer"]
    assert peer["online"] is True
    login(client, "bob")
    assert client.put(f"{C}/prefs", json={"presence": False}).json()["presence"] is False
    login(client, "alice")
    client.portal.call(online)
    assert client.get(C).json()["items"][0]["peer"]["online"] is False, "скрывший статус не показывается «в сети»"
    assert not client.portal.call(lambda: redis.exists(f"presence:watch:{bob}")), "подписка одноразовая"


async def _db_user(client, uid_):
    async with client.app_obj.state.session_maker() as db:
        return await db.get(User, uuid.UUID(uid_))


def test_realtime_events_reach_every_device_of_each_member(client):
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    login(client, "bob")
    with client.websocket_connect(f"{API}/ws") as phone, client.websocket_connect(f"{API}/ws") as laptop:
        for ws in (phone, laptop):
            ws.send_json({"type": "subscribe_user"})
            assert ws.receive_json()["type"] == "subscribed_user"
        login(client, "alice")
        m = say(client, cid, "на оба устройства")
        for ws in (phone, laptop):
            ev = ws.receive_json()
            assert ev["type"] == "chat_message" and ev["message"]["id"] == m["id"] and ev["chat_id"] == cid
        login(client, "bob")
        client.post(f"{C}/{cid}/read", json={"last_id": m["id"]})
        for ws in (phone, laptop):
            ev = ws.receive_json()
            assert ev == {"type": "chat_read", "chat_id": cid, "user_id": bob, "last_read_id": m["id"]}, "прочитанность синхронизируется на всех устройствах"


def test_rate_limit_retention_and_settings(client):
    bob = uid(client, "bob")
    login(client, "root")
    assert client.put(f"{API}/admin/settings/chats", json={"messages_per_minute": 5, "retention_days": 30}).status_code == 200
    cid = direct(client, "alice", bob)
    codes = [client.post(f"{C}/{cid}/messages", json={"text": f"m{i}", "client_id": uuid.uuid4().hex}).status_code for i in range(7)]
    assert codes[:5] == [201] * 5 and codes[5] == 429

    async def age(db):
        for m in (await db.execute(select(ChatMessage).where(ChatMessage.chat_id == uuid.UUID(cid)).order_by(ChatMessage.id).limit(3))).scalars():
            m.created_at = utcnow() - timedelta(days=31)
        await db.commit()
    _db(client, age)
    from app.services import chats as cs

    async def purge(db):
        return await cs.purge_expired(db, client.app_obj.state.chat_files, 30)
    assert _db(client, purge) == 3
    assert [m["text"] for m in client.get(f"{C}/{cid}/messages").json()["messages"]] == ["m3", "m4"]
    login(client, "root")
    assert client.put(f"{API}/admin/settings/chats", json={"enabled": False}).status_code == 200
    login(client, "alice")
    assert client.get(C).status_code == 403 and client.get(f"{C}/unread").json() == {"unread": 0, "enabled": False}


def test_duplicate_send_race_returns_the_stored_message(client, monkeypatch):
    """Повтор одного сообщения пришёл одновременно в два экземпляра backend: второй упирается в уникальность (chat, автор, client_id) и должен
    вернуть записанное первым, а не ошибку (раньше — 500 из-за обращения к устаревшим после отката объектам)."""
    from app.services import chats as cs
    bob = uid(client, "bob")
    cid = direct(client, "alice", bob)
    first = say(client, cid, "один раз", client_id="race-00000001")
    real = cs._by_client
    calls = {"n": 0}

    async def miss_first(db, chat_id, author_id, client_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(db, chat_id, author_id, client_id)      # проверка «уже есть?» опоздала — как при гонке
    monkeypatch.setattr(cs, "_by_client", miss_first)
    r = client.post(f"{C}/{cid}/messages", json={"text": "один раз", "client_id": "race-00000001"})
    assert r.status_code == 200 and r.json()["id"] == first["id"] and calls["n"] == 2
    assert _db(client, lambda db: _count(db, ChatMessage.chat_id == uuid.UUID(cid))) == 1
