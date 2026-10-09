"""Кто может входить в систему: список разрешённых групп каталога («Доступ к системе»), проверка пользователя, аудит."""
from __future__ import annotations

from .conftest import ADMIN_GROUP, OTHER_GROUP, STAFF_GROUP, login, put_settings
from .test_local_admin_ldap import make_local_admin

AUTH = "/api/v1/auth"
ADM = "/api/v1/admin"
THIRD_GROUP = "cn=doctors,ou=groups,dc=corp,dc=test"


def try_login(client, user: str):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return client.post(f"{AUTH}/login", json={"login": user, "password": f"{user}-pass"})


def journal(client, event: str) -> list[dict]:
    client.portal.call(client.app_obj.state.journal.flush)
    login(client, "root")
    items = client.get(f"{ADM}/journal", params={"q": event, "limit": 200}).json()["items"]
    return [i for i in items if i["event"] == event]


def test_old_install_with_empty_acl_blocks_nobody_and_status_warns(client, directory):
    assert try_login(client, "alice").status_code == 200
    assert try_login(client, "carol").status_code == 200
    login(client, "root")
    st = client.get(f"{ADM}/access/status").json()
    assert st["restricted"] is False and st["user_groups"] == 0


def test_allowlist_denies_other_groups_without_session_and_applies_without_restart(client, directory):
    put_settings(client, "access", user_groups=[STAFF_GROUP])
    assert client.get(f"{ADM}/access/status").json()["restricted"] is True
    assert try_login(client, "alice").status_code == 200
    r = try_login(client, "carol")                                   # верный пароль, но группа не разрешена
    assert r.status_code == 403 and r.json()["detail"]["code"] == "access_denied"
    assert "set-cookie" not in {k.lower() for k in r.headers}        # сессия не создана
    assert client.get(f"{AUTH}/me").status_code == 401
    assert client.get(f"{ADM}/rooms").status_code in (401, 403)       # данных нет
    # вторая группа: достаточно членства в любой из групп; применяется сразу, без перезапуска
    put_settings(client, "access", user_groups=[STAFF_GROUP, OTHER_GROUP, THIRD_GROUP])
    assert try_login(client, "carol").status_code == 200
    put_settings(client, "access", user_groups=[STAFF_GROUP])
    assert try_login(client, "carol").status_code == 403


def test_admin_group_and_local_admin_do_not_depend_on_the_login_acl(client, directory):
    make_local_admin(client, must_change=False)
    put_settings(client, "access", user_groups=[OTHER_GROUP])
    assert try_login(client, "root").status_code == 200              # администратор группы ADMIN_GROUP не в списке допуска, но вход есть
    assert try_login(client, "alice").status_code == 403
    r = client.post(f"{AUTH}/login", json={"login": "admin", "password": "Qm4-Rt8w-Yn2k-Bd6x"})
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is True


def test_login_acl_and_admin_acl_are_separate(client, directory):
    put_settings(client, "access", user_groups=[STAFF_GROUP], admin_groups=[ADMIN_GROUP])
    me = try_login(client, "alice").json()["user"]
    assert me["is_admin"] is False                                    # допуск не даёт прав администратора
    put_settings(client, "access", user_groups=[OTHER_GROUP])
    assert try_login(client, "root").json()["user"]["is_admin"] is True


def test_guest_link_is_not_affected_by_the_login_acl(client, directory):
    from .conftest import make_room
    room = make_room(client, guest_access_enabled=True)
    put_settings(client, "access", user_groups=[OTHER_GROUP])
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert client.get(f"/api/v1/guest/room/{room['guest_token']}").status_code == 200       # гость идёт мимо каталога
    assert try_login(client, "alice").status_code == 403


def test_check_user_reports_group_admin_and_denial(client, directory):
    put_settings(client, "access", user_groups=[STAFF_GROUP])
    ok = client.post(f"{ADM}/access/check-user", json={"login": "alice"}).json()
    assert ok["found"] and ok["would_log_in"] and ok["allowed_via"] == [STAFF_GROUP] and ok["admin"] is False
    no = client.post(f"{ADM}/access/check-user", json={"login": "carol"}).json()
    assert no["found"] and no["would_log_in"] is False and no["reason"] == "not_in_allowed_groups" and no["allowed_via"] == []
    adm = client.post(f"{ADM}/access/check-user", json={"login": "root"}).json()
    assert adm["would_log_in"] and adm["admin"] is True and adm["admin_via"] == [ADMIN_GROUP]
    assert client.post(f"{ADM}/access/check-user", json={"login": "nobody"}).json() == {"found": False, "message": "Пользователь не найден в каталоге"}
    assert client.post(f"{ADM}/access/check-user", json={"login": ""}).status_code == 422
    try_login(client, "alice")
    assert client.post(f"{ADM}/access/check-user", json={"login": "alice"}).status_code == 403      # не администратор


def test_acl_changes_login_denials_and_logins_are_audited_without_secrets(client, directory):
    put_settings(client, "access", user_groups=[STAFF_GROUP, OTHER_GROUP])
    put_settings(client, "access", user_groups=[STAFF_GROUP])
    try_login(client, "carol")
    try_login(client, "alice")
    login(client, "root")
    rows = [a for a in client.get(f"{ADM}/audit", params={"action": "settings.access.update"}).json()]
    added = [a["details"].get("login_groups_added") for a in rows]
    removed = [a["details"].get("login_groups_removed") for a in rows]
    assert [STAFF_GROUP, OTHER_GROUP] in added and [OTHER_GROUP] in removed and all(a["actor"] for a in rows)
    denied = journal(client, "login_denied_acl")
    assert denied and denied[0]["user"] == "carol" and denied[0]["data"]["reason"] == "not_in_allowed_groups"
    ok = [i for i in journal(client, "login_ok") if i["user"] == "alice"]
    assert ok and ok[0]["data"]["allowed_via"] == [STAFF_GROUP]
    assert "carol-pass" not in str(denied) and "alice-pass" not in str(ok)
    assert journal(client, "acl_login_changed")


def test_setup_wizard_has_a_login_acl_step(client, directory):
    login(client, "root")
    steps = {s["id"]: s for s in client.get(f"{ADM}/setup/status").json()["steps"]}
    assert steps["login_acl"]["done"] is False and steps["login_acl"]["page"] == "login_access"
    put_settings(client, "access", user_groups=[STAFF_GROUP])
    assert {s["id"]: s["done"] for s in client.get(f"{ADM}/setup/status").json()["steps"]}["login_acl"] is True
