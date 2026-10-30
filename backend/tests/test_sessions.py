"""Несколько устройств: до N одновременных сессий одной учётной записи (по умолчанию 5), вход с телефона не завершает сессию компьютера, шестой вход не выбрасывает прежние,
«Активные устройства», принудительное завершение администратором, независимость сессий и участие в одной встрече с нескольких устройств."""
from __future__ import annotations

import jwt
import pytest

from app.services.livekit import identity_device, parse_user_identity, user_identity
from .conftest import login, make_room
from .test_transcripts import _join

API = "/api/v1"
COOKIE = "vm_session"
PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
PC = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def sign_in(c, user="alice", ua=PC, extra=None):
    """Отдельный вход «с устройства»: возвращает (ответ, cookie сессии)."""
    c.cookies.clear()
    r = c.post(f"{API}/auth/login", json={"login": user, "password": f"{user}-pass", **(extra or {})}, headers={"User-Agent": ua})
    return r, c.cookies.get(COOKIE)


def as_device(c, cookie):
    c.cookies.clear()
    if cookie:
        c.cookies.set(COOKIE, cookie)
    c.headers.pop("X-CSRF-Token", None)


def me_status(c, cookie):
    as_device(c, cookie)
    return c.get(f"{API}/auth/me").status_code


def test_phone_login_does_not_end_the_computer_session_and_five_sessions_work_together(client):
    r, pc = sign_in(client, ua=PC)
    assert r.status_code == 200
    r, phone = sign_in(client, ua=PHONE)
    assert r.status_code == 200 and pc != phone
    assert me_status(client, pc) == 200 and me_status(client, phone) == 200, "вход с телефона не завершает сессию на компьютере и наоборот"
    cookies = [pc, phone] + [sign_in(client, ua=PC)[1] for _ in range(3)]
    assert all(me_status(client, c) == 200 for c in cookies), "пять сессий одной учётной записи работают одновременно"


def test_sixth_login_is_refused_without_ending_anything_and_a_chosen_session_can_be_freed(client):
    cookies = [sign_in(client, ua=PC)[1] for _ in range(5)]
    r, sixth = sign_in(client, ua=PHONE)
    assert r.status_code == 409 and sixth is None
    d = r.json()["detail"]
    assert d["code"] == "session_limit" and d["limit"] == 5 and len(d["sessions"]) == 5 and d["free_token"]
    assert all(me_status(client, c) == 200 for c in cookies), "прежние сессии не завершены автоматически"
    assert all(set(x) >= {"id", "device", "last_seen"} and "_key" not in x for x in d["sessions"])
    victim = d["sessions"][0]["id"]
    client.cookies.clear()
    ok = client.post(f"{API}/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": victim}, headers={"User-Agent": PHONE})
    assert ok.status_code == 200
    new = client.cookies.get(COOKIE)
    alive = [me_status(client, c) for c in cookies]
    assert alive.count(401) == 1 and alive.count(200) == 4, "завершена ровно одна выбранная сессия"
    assert me_status(client, new) == 200
    again = client.post(f"{API}/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": victim})
    assert again.status_code in (401, 404), "право освободить место одноразовое"


def test_free_token_cannot_end_someone_elses_session(client):
    _, bob_cookie = sign_in(client, "bob")
    bob_pub = (lambda: (as_device(client, bob_cookie), client.get(f"{API}/auth/sessions").json()["items"][0]["id"])[1])()
    for _ in range(5):
        sign_in(client, "alice")
    r, _ = sign_in(client, "alice")
    token = r.json()["detail"]["free_token"]
    client.cookies.clear()
    res = client.post(f"{API}/auth/login/free-slot", json={"free_token": token, "session_id": bob_pub})
    assert res.status_code == 404 and me_status(client, bob_cookie) == 200, "чужую сессию завершить нельзя"


def test_logout_ends_only_the_current_session_end_others_and_active_devices_list(client):
    _, pc = sign_in(client, ua=PC)
    _, phone = sign_in(client, ua=PHONE)
    as_device(client, pc)
    items = client.get(f"{API}/auth/sessions").json()["items"]
    assert len(items) == 2 and items[0]["current"] is True and "компьютер" in items[0]["device"] and any("телефон" in x["device"] for x in items)
    assert all(x["last_seen"] and "csrf" not in str(x).lower() and pc not in str(x) and phone not in str(x) for x in items), "сами идентификаторы сессий наружу не отдаются"
    csrf = client.post(f"{API}/auth/login", json={"login": "alice", "password": "alice-pass"}).json()["csrf_token"] if False else None
    as_device(client, phone)
    me = client.get(f"{API}/auth/me").json()
    client.headers["X-CSRF-Token"] = me["csrf_token"]
    assert client.post(f"{API}/auth/logout").status_code == 204
    assert me_status(client, phone) == 401 and me_status(client, pc) == 200, "выход завершает только текущую сессию"
    _, other = sign_in(client, ua=PHONE)
    as_device(client, pc)
    client.headers["X-CSRF-Token"] = client.get(f"{API}/auth/me").json()["csrf_token"]
    assert client.post(f"{API}/auth/sessions/end-others").json() == {"ended": 1}
    assert me_status(client, other) == 401 and me_status(client, pc) == 200


def admin_device(c):
    """Администратор входит один раз и работает с этой сессией (отдельные входы test-helper'а считались бы сессиями при малом лимите)."""
    r, cookie = sign_in(c, "root")
    assert r.status_code == 200
    return cookie, r.json()["csrf_token"]


def as_admin(c, adm):
    as_device(c, adm[0])
    c.headers["X-CSRF-Token"] = adm[1]


def test_configurable_limit_and_unlimited_and_admin_force_logout_and_deactivation(client):
    adm = admin_device(client)
    as_admin(client, adm)
    assert client.put(f"{API}/admin/settings/auth_security", json={"max_sessions": 2}).status_code == 200
    a = [sign_in(client, "alice")[1] for _ in range(2)]
    assert sign_in(client, "alice")[0].status_code == 409, "лимит настраивается администратором"
    as_admin(client, adm)
    users = client.get(f"{API}/admin/users").json()
    users = users if isinstance(users, list) else (users.get("items") or users.get("users"))
    uid = next(u["id"] for u in users if u["sam_account_name"] == "alice")
    sess = client.get(f"{API}/admin/users/{uid}/sessions").json()["items"]
    assert len(sess) == 2 and all("device" in x for x in sess)
    assert client.delete(f"{API}/admin/users/{uid}/sessions", params={"pub": sess[0]["id"]}).json() == {"ended": 1}
    assert sorted(me_status(client, c) for c in a) == [200, 401]
    as_admin(client, adm)
    assert client.put(f"{API}/admin/settings/auth_security", json={"max_sessions": 0}).status_code == 200
    many = [sign_in(client, "alice")[1] for _ in range(8)]
    assert all(me_status(client, c) == 200 for c in many[:8]), "0 — без ограничения"
    as_admin(client, adm)
    assert client.delete(f"{API}/admin/users/{uid}/sessions").json()["ended"] >= 8
    assert all(me_status(client, c) == 401 for c in many), "принудительный выход администратором завершает сессии"
    again = [sign_in(client, "alice")[1] for _ in range(3)]
    as_admin(client, adm)
    assert client.patch(f"{API}/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert all(me_status(client, c) == 401 for c in again), "отключение учётной записи завершает все её сессии на всех устройствах"
    as_admin(client, adm)
    assert client.put(f"{API}/admin/settings/auth_security", json={"max_sessions": 101}).status_code == 422


# ------------------------------------------------------------------------------------------ участие в одной встрече с нескольких устройств
def join(c, cookie, room_id, client_id, ua):
    as_device(c, cookie)
    csrf = c.get(f"{API}/auth/me").json()["csrf_token"]
    return c.post(f"{API}/rooms/{room_id}/join", json={}, headers={"X-CSRF-Token": csrf, "X-Client-Id": client_id, "User-Agent": ua})


def test_two_devices_of_one_user_join_one_meeting_without_kicking_each_other(client):
    room = make_room(client)
    _, pc = sign_in(client, ua=PC)
    _, phone = sign_in(client, ua=PHONE)
    a = join(client, pc, room["id"], "tab-computer-0001", PC).json()
    b = join(client, phone, room["id"], "tab-phone-000002", PHONE).json()
    assert a["identity"] != b["identity"] and parse_user_identity(a["identity"]) == parse_user_identity(b["identity"]), "у каждого устройства свой идентификатор участника"
    assert a["identity"] == user_identity(parse_user_identity(a["identity"])) and identity_device(b["identity"]), "первое подключение — основной identity, второе — с меткой"
    name_a = jwt.decode(a["token"], options={"verify_signature": False})["name"]
    name_b = jwt.decode(b["token"], options={"verify_signature": False})["name"]
    assert name_a != name_b and "телефон" in name_b, "устройства различимы в списке участников"
    # повторное подключение того же устройства (обрыв сети) — тот же identity, лишних подключений нет
    assert join(client, pc, room["id"], "tab-computer-0001", PC).json()["identity"] == a["identity"]
    assert join(client, phone, room["id"], "tab-phone-000002", PHONE).json()["identity"] == b["identity"]
    # выход с телефона не считается выходом пользователя из встречи, пока компьютер подключён
    as_device(client, phone)
    csrf = client.get(f"{API}/auth/me").json()["csrf_token"]
    mid = a["meeting_id"]
    assert client.post(f"{API}/meetings/{mid}/leave", headers={"X-CSRF-Token": csrf, "X-Client-Id": "tab-phone-000002"}).status_code == 204
    as_device(client, pc)
    meeting = client.get(f"{API}/meetings/{mid}", headers={"X-Client-Id": "tab-computer-0001"}).json()
    assert meeting.get("ended_at") is None
    # клиент без идентификатора (приложение Android до 0.1.3, API-клиенты), пока основное подключение занято вкладкой браузера, получает СВОЕГО участника, а не общий u-<hex>
    # (раньше они совпадали, и LiveKit закрывал вкладку: «уже вошла другая вкладка этого браузера с тем же идентификатором»)
    c1 = join(client, phone, room["id"], "", PHONE).json()
    assert c1["identity"] not in (a["identity"], b["identity"]) and identity_device(c1["identity"]), "отдельный участник по сеансу входа"
    assert join(client, phone, room["id"], "", PHONE).json()["identity"] == c1["identity"], "тот же сеанс после обрыва — тот же участник"


def test_legacy_client_first_then_browser_tab_and_two_tabs_do_not_collide(client):
    room = make_room(client)
    _, pc = sign_in(client, ua=PC)
    _, phone = sign_in(client, ua=PHONE)
    old_app = join(client, phone, room["id"], "", PHONE).json()                       # клиент без идентификатора пришёл первым: основной участник у него
    tab1 = join(client, pc, room["id"], "tab-computer-0001", PC).json()
    tab2 = join(client, pc, room["id"], "tab-computer-0002", PC).json()              # вторая вкладка того же браузера и той же сессии
    ids = {old_app["identity"], tab1["identity"], tab2["identity"]}
    assert len(ids) == 3, "три независимых подключения одного пользователя"
    assert identity_device(old_app["identity"]) == "" and all(identity_device(x) for x in (tab1["identity"], tab2["identity"]))
    new_app = join(client, phone, room["id"], "app-0123456789abcdef", PHONE).json()   # приложение, присылающее свой идентификатор, — ещё один участник
    assert new_app["identity"] not in ids and join(client, phone, room["id"], "app-0123456789abcdef", PHONE).json()["identity"] == new_app["identity"]
    # выход одной вкладки не закрывает остальные подключения
    as_device(client, pc)
    csrf = client.get(f"{API}/auth/me").json()["csrf_token"]
    mid = old_app["meeting_id"]
    assert client.post(f"{API}/meetings/{mid}/leave", headers={"X-CSRF-Token": csrf, "X-Client-Id": "tab-computer-0002"}).status_code == 204
    assert client.get(f"{API}/meetings/{mid}", headers={"X-Client-Id": "tab-computer-0001"}).json().get("ended_at") is None


def test_identity_helpers_roundtrip_and_reject_garbage():
    import uuid
    u = uuid.uuid4()
    assert parse_user_identity(user_identity(u)) == u and parse_user_identity(user_identity(u, "ab12cd34")) == u and identity_device(user_identity(u, "ab12cd34")) == "ab12cd34"
    for bad in ("u-" + u.hex + "~", "u-" + u.hex + "~zz!", "u-" + u.hex + "~12", "g-" + u.hex, "asr-x"):
        assert parse_user_identity(bad) is None
