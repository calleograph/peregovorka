from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import User
from app.profiles import manual
from app.profiles.service import apply_effective, record_ad

from .conftest import login

API = "/api/v1/profile"


def _policy(client, fields: str):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/profile_policy", json={"editable_fields": fields})
    assert r.status_code == 200, r.text


def _field(p, name):
    return next(f for f in p["fields"] if f["name"] == name)


def _alice(client):
    login(client, "alice")
    return client.get(API).json()


def test_defaults_allow_title_department_phone_only(client):
    p = _alice(client)
    assert [f["name"] for f in p["fields"] if f["editable"]] == ["title", "department", "phone"]
    assert p["restorable"] is True
    r = client.patch(API, json={"fields": {"display_name": "Хакер"}})
    assert r.status_code == 422 and "запретил" in r.json()["detail"]
    assert client.patch(API, json={"fields": {}}).status_code == 422


def test_manual_edit_survives_login_refresh_and_is_marked(client):
    _policy(client, "title,department,phone,display_name,email")
    p = _alice(client)
    r = client.patch(API, json={"fields": {"title": "Главный по связи", "phone": "+7 000"}})
    assert r.status_code == 200
    f = _field(r.json(), "title")
    assert f["value"] == "Главный по связи" and f["manual"] is True and f["manual_at"] and f["source"] == "local"
    assert _field(r.json(), "email")["manual"] is False
    # повторный вход (синхронизация с каталогом) и «обновить из AD» не перезаписывают правку
    p2 = _alice(client)
    assert _field(p2, "title")["value"] == "Главный по связи"
    r = client.post(f"{API}/refresh")
    assert r.status_code == 200 and _field(r.json(), "title")["value"] == "Главный по связи" and _field(r.json(), "phone")["value"] == "+7 000"
    # значение каталога видно отдельно
    assert "ad_value" in _field(r.json(), "title")


def test_ad_changes_do_not_override_manual_but_apply_to_other_fields(client, directory):
    _policy(client, "title")
    login(client, "alice")
    client.patch(API, json={"fields": {"title": "Моя должность"}})
    ident = directory.users["alice"][1]
    directory.users["alice"] = (directory.users["alice"][0], SimpleNamespace(**{**ident.__dict__, "title": "Должность из AD", "department": "Новый отдел"}))
    r = client.post(f"{API}/refresh")
    assert r.status_code == 200
    assert _field(r.json(), "title")["value"] == "Моя должность"
    assert _field(r.json(), "title")["ad_value"] == "Должность из AD"
    assert _field(r.json(), "department")["value"] == "Новый отдел"          # неправленые поля обновляются как раньше


def test_restore_from_ldap_returns_directory_value_and_audits(client, directory):
    _policy(client, "title,phone")
    login(client, "alice")
    ident = directory.users["alice"][1]
    directory.users["alice"] = (directory.users["alice"][0], SimpleNamespace(**{**ident.__dict__, "title": "Должность из AD"}))
    client.post(f"{API}/refresh")
    client.patch(API, json={"fields": {"title": "Моя", "phone": "111"}})
    r = client.post(f"{API}/restore", json={"fields": ["title"]})
    assert r.status_code == 200
    assert _field(r.json(), "title")["value"] == "Должность из AD" and _field(r.json(), "title")["manual"] is False
    assert _field(r.json(), "phone")["manual"] is True                              # остальное не тронуто
    r = client.post(f"{API}/restore", json={})                                      # без списка — все правки
    assert _field(r.json(), "phone")["manual"] is False
    login(client, "root")
    acts = [a["action"] for a in client.get("/api/v1/admin/audit").json()]
    assert {"profile.edit", "profile.restore"} <= set(acts)
    edit = next(a for a in client.get("/api/v1/admin/audit", params={"action": "profile.edit"}).json())
    assert edit["details"]["fields"]["title"]["new"] == "Моя"


def test_validation_and_local_account(client):
    _policy(client, "title,email,phone")
    _alice(client)
    assert client.patch(API, json={"fields": {"title": "   "}}).status_code == 422
    assert client.patch(API, json={"fields": {"email": "без-собаки"}}).status_code == 422
    assert client.patch(API, json={"fields": {"unknown": "x"}}).status_code == 422
    login(client, "root")                                                           # у каталожного root профиль редактируется так же
    assert client.patch(API, json={"fields": {"phone": "123"}}).status_code == 200


def test_pure_helpers():
    u = SimpleNamespace(title="AD", department=None, phone=None, display_name="A", email="a@x.org", profile_sources={"ad": {"title": "AD"}}, auth_source="ad")
    done = manual.set_manual(u, {"title": "Мой"}, {"title"}, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert done == {"title": ("AD", "Мой")} and u.title == "Мой"
    assert manual.set_manual(u, {"title": "Мой"}, {"title"}) == {}                     # то же значение — ничего не меняется
    u.title = "перезаписано синхронизацией"
    manual.apply_manual(u)
    assert u.title == "Мой"
    assert manual.restore(u, ["title", "phone"]) == ["title"] and u.title == "AD"
    manual.set_manual(u, {"phone": "5"}, {"phone"})
    assert manual.restore(u, ["phone"]) == ["phone"] and u.phone is None             # в каталоге значения нет — поле пустеет
    with pytest.raises(manual.ManualError):
        manual.set_manual(u, {"phone": "1"}, {"title"})
    assert manual.editable_set(SimpleNamespace(editable_fields="title, bogus ,phone")) == {"title", "phone"}


def test_apply_effective_respects_manual(client):
    # слияние с порталом не отменяет правку пользователя
    u = SimpleNamespace(title="B", department=None, phone=None, display_name="A", email=None,
                        profile_sources={"ad": {"title": "AD"}, "bitrix": {"title": "Портал"}, "local": {"title": "Моя"}, "manual_at": {"title": "2026-01-01T00:00:00+00:00"}}, auth_source="ad")
    apply_effective(u, SimpleNamespace(priority_title="bitrix, ad, local", priority_display_name="ad", priority_email="ad", priority_department="ad", priority_phone="ad", priority_avatar="local"), use_bitrix=True)
    assert u.title == "Моя"
    record_ad(u, {"title": "AD2"})
    apply_effective(u, None, use_bitrix=False)
    assert u.title == "Моя"
