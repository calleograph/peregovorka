"""0.13.0: настройки сайта — оформление установки, изображения, контакты, юридические документы, подтверждения, экспорт/импорт и сохранность при перезапуске."""
from __future__ import annotations

import io
import zipfile

from PIL import Image

from .conftest import login, make_settings, running_app
from .test_collab_guests import guest_join, guest_room, started

API = "/api/v1"


def png(w=300, h=120, color=(30, 90, 200, 255)) -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(b, "PNG")
    return b.getvalue()


def put_file(client, path: str, data: bytes, ctype="application/octet-stream"):
    return client.put(f"{API}{path}", content=data, headers={"content-type": ctype})


def site(client) -> dict:
    client.cookies.clear()
    return client.get(f"{API}/public/site").json()


def test_clean_install_uses_standard_look_and_exposes_nothing_private(client):
    d = client.get(f"{API}/public/site").json()
    assert d["name"] == "Peregovorka" and d["customized"] is False and d["support"] is None and d["documents"] == []
    assert d["assets"] == {"logo": None, "logo_compact": None, "favicon": None}
    assert client.get(f"{API}/public/legal/privacy_policy").status_code == 404


def test_admin_customizes_and_public_sees_it_validation_and_roles(client):
    login(client, "alice")
    assert client.put(f"{API}/admin/settings/site", json={"name": "Х"}).status_code == 403
    login(client, "root")
    assert client.put(f"{API}/admin/settings/site", json={"primary_color": "red"}).status_code == 422
    assert client.put(f"{API}/admin/settings/site", json={"org_url": "javascript:alert(1)"}).status_code == 422
    assert client.put(f"{API}/admin/settings/site", json={"support_email": "не адрес"}).status_code == 422
    r = client.put(f"{API}/admin/settings/site", json={"name": "Встречи Компании", "short_name": "Встречи", "primary_color": "#1A56DB", "theme": "dark",
                                                       "org_full": "Общество «Пример»", "support_email": "help@example.com", "support_phone": "+7 (495) 000-00-00", "welcome_text": "Добро пожаловать"})
    assert r.status_code == 200, r.text
    d = site(client)
    assert d["name"] == "Встречи Компании" and d["short_name"] == "Встречи" and d["primary_color"] == "#1a56db" and d["theme"] == "dark" and d["customized"] is True
    assert d["support"]["support_email"] == "help@example.com" and d["org"]["full"] == "Общество «Пример»" and d["welcome_text"] == "Добро пожаловать"
    audit = [a["action"] for a in (login(client, "root") and client.get(f"{API}/admin/audit").json())]
    assert any(a.startswith("settings") or "site" in a for a in audit)


def test_support_contacts_visibility_modes(client):
    login(client, "root")
    client.put(f"{API}/admin/settings/site", json={"support_email": "help@example.com", "support_mode": "auth"})
    assert client.get(f"{API}/public/site").json()["support"] is not None          # root вошёл
    client.cookies.clear()
    assert client.get(f"{API}/public/site").json()["support"] is None              # аноним: контакты только вошедшим
    login(client, "root")
    client.put(f"{API}/admin/settings/site", json={"support_mode": "off"})
    assert client.get(f"{API}/public/site").json()["support"] is None
    client.put(f"{API}/admin/settings/site", json={"support_mode": "all", "support_email": ""})
    assert client.get(f"{API}/public/site").json()["support"] is None, "пустые поля не показываются"


def test_images_are_validated_reencoded_versioned_and_removable(client):
    login(client, "alice")
    assert put_file(client, "/admin/site/assets/logo", png()).status_code == 403
    login(client, "root")
    assert put_file(client, "/admin/site/assets/nope", png()).status_code == 404
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert put_file(client, "/admin/site/assets/logo", svg).status_code == 422
    assert put_file(client, "/admin/site/assets/logo", b"MZ" + b"\x00" * 100).status_code == 422, "по содержимому, а не по расширению"
    assert put_file(client, "/admin/site/assets/logo", b"\x89PNG\r\n\x1a\n" + b"junk").status_code == 422, "повреждённый файл"
    assert put_file(client, "/admin/site/assets/logo", png(8, 8)).status_code == 422, "слишком маленькое"
    assert put_file(client, "/admin/site/assets/logo", b"x" * (2 * 1024 * 1024 + 10)).status_code == 413
    ok = put_file(client, "/admin/site/assets/logo", png(1200, 300))
    assert ok.status_code == 200
    d = site(client)
    url = d["assets"]["logo"]
    assert url and "?v=" in url and d["customized"] is True
    img = client.get(url)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and "immutable" in img.headers["cache-control"] and img.headers["x-content-type-options"] == "nosniff"
    out = Image.open(io.BytesIO(img.content))
    assert out.width <= 480 and out.height <= 160 and abs(out.width / out.height - 4) < 0.05, "вписан в рамку, пропорции сохранены"
    assert "no-cache" in client.get(f"{API}/public/site/asset/logo?v=old").headers["cache-control"]
    # смена изображения меняет адрес — браузер не покажет старое
    login(client, "root")
    put_file(client, "/admin/site/assets/logo", png(300, 120, (200, 20, 20, 255)))
    assert site(client)["assets"]["logo"] != url
    # favicon: ICO допустим только для значка; результат — квадратный PNG
    ico = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 128, 0, 255)).save(ico, "ICO")
    login(client, "root")
    assert put_file(client, "/admin/site/assets/favicon", ico.getvalue()).status_code == 200
    assert put_file(client, "/admin/site/assets/logo", ico.getvalue()).status_code == 422
    fav = client.get(site(client)["assets"]["favicon"])
    assert Image.open(io.BytesIO(fav.content)).size == (64, 64)
    login(client, "root")
    assert client.delete(f"{API}/admin/site/assets/logo").status_code == 204
    assert site(client)["assets"]["logo"] is None and client.get(f"{API}/public/site/asset/logo").status_code == 404


def test_legal_documents_publish_versions_unpublish_and_markdown_is_stored_as_is(client):
    login(client, "alice")
    assert client.get(f"{API}/admin/site/legal").status_code == 403
    login(client, "root")
    items = client.get(f"{API}/admin/site/legal").json()["items"]
    assert [i["kind"] for i in items] == ["privacy_policy", "personal_data_consent", "terms", "usage_rules"] and not any(i["published"] for i in items)
    assert client.post(f"{API}/admin/site/legal/privacy_policy/publish").status_code == 422, "пустой документ не публикуется"
    assert client.put(f"{API}/admin/site/legal/hack", json={}).status_code == 404
    assert client.put(f"{API}/admin/site/legal/privacy_policy", json={"hack": 1}).status_code == 422
    r = client.put(f"{API}/admin/site/legal/privacy_policy", json={"title": "Политика", "draft_md": "# Политика\n\nТекст <script>alert(1)</script>", "require_consent": False})
    assert r.status_code == 200 and r.json()["version"] == 0
    client.cookies.clear()
    assert client.get(f"{API}/public/legal/privacy_policy").status_code == 404, "черновик публично не виден"
    login(client, "root")
    assert client.post(f"{API}/admin/site/legal/privacy_policy/publish").json()["version"] == 1
    pub = client.get(f"{API}/public/legal/privacy_policy").json()
    assert pub["version"] == 1 and pub["title"] == "Политика" and "<script>" in pub["content_md"], "сервер хранит текст как есть; безопасный показ — на клиенте"
    client.put(f"{API}/admin/site/legal/privacy_policy", json={"draft_md": "# Политика 2"})
    assert client.get(f"{API}/public/legal/privacy_policy").json()["version"] == 1, "правка черновика не меняет опубликованное"
    assert client.post(f"{API}/admin/site/legal/privacy_policy/publish").json()["version"] == 2
    revs = client.get(f"{API}/admin/site/legal/privacy_policy/revisions").json()["items"]
    assert [r["version"] for r in revs] == [2, 1] and revs[1]["unpublished_at"] is not None and revs[0]["unpublished_at"] is None
    assert [d["kind"] for d in site(client)["documents"]] == ["privacy_policy"]
    login(client, "root")
    client.post(f"{API}/admin/site/legal/privacy_policy/unpublish")
    assert client.get(f"{API}/public/legal/privacy_policy").status_code == 404 and site(client)["documents"] == []


def test_consent_is_explicit_per_version_and_recorded_for_users_and_guests(client):
    login(client, "root")
    client.put(f"{API}/admin/site/legal/terms", json={"title": "Соглашение", "draft_md": "Правила v1", "require_consent": True})
    client.put(f"{API}/admin/site/legal/usage_rules", json={"draft_md": "Правила работы", "require_consent": False})
    client.post(f"{API}/admin/site/legal/terms/publish")
    client.post(f"{API}/admin/site/legal/usage_rules/publish")
    login(client, "bob")
    pend = client.get(f"{API}/legal/pending").json()["items"]
    assert [p["kind"] for p in pend] == ["terms"], "посещение сайта согласием не считается; подтверждения требует только документ с включённым параметром"
    assert client.post(f"{API}/legal/consent", json={"kinds": ["usage_rules"]}).status_code == 422
    assert client.post(f"{API}/legal/consent", json={"kinds": ["terms"]}).status_code == 200
    assert client.get(f"{API}/legal/pending").json()["items"] == []
    login(client, "root")
    client.put(f"{API}/admin/site/legal/terms", json={"draft_md": "Правила v2"})
    client.post(f"{API}/admin/site/legal/terms/publish")
    login(client, "bob")
    assert [p["version"] for p in client.get(f"{API}/legal/pending").json()["items"]] == [2], "новая редакция требует подтверждения заново"
    login(client, "root")
    cons = client.get(f"{API}/admin/site/legal/terms/consents").json()
    assert cons["total"] == 1 and cons["items"][0]["version"] == 1 and cons["items"][0]["subject_name"] == "Bob B" and cons["items"][0]["subject_type"] == "user"
    # гость: без подтверждения вход отклонён, с подтверждением — записано
    room = guest_room(client)
    started(client, room)
    r = guest_join(client, room["guest_token"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "consent_required" and r.json()["detail"]["documents"][0]["kind"] == "terms"
    assert guest_join(client, room["guest_token"], accepted_documents=["terms"]).status_code == 200
    login(client, "root")
    types = {c["subject_type"] for c in client.get(f"{API}/admin/site/legal/terms/consents").json()["items"]}
    assert types == {"user", "guest"}


def test_export_import_roundtrip_and_hostile_archives(client):
    login(client, "root")
    client.put(f"{API}/admin/settings/site", json={"name": "Экспорт", "primary_color": "#112233", "support_email": "s@example.com"})
    put_file(client, "/admin/site/assets/logo_compact", png(200, 200))
    client.put(f"{API}/admin/site/legal/terms", json={"title": "Условия", "draft_md": "Текст", "require_consent": True})
    client.post(f"{API}/admin/site/legal/terms/publish")
    r = client.get(f"{API}/admin/site/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"site.json", "assets/logo_compact.png"}
    text = z.read("site.json").decode()
    assert "logo_v" not in text and "password" not in text.lower() and "consent_" not in text, "служебные версии и чувствительное в экспорт не входят"
    client.put(f"{API}/admin/settings/site", json={"name": "Другое"})
    client.delete(f"{API}/admin/site/assets/logo_compact")
    client.post(f"{API}/admin/site/legal/terms/unpublish")
    ok = client.put(f"{API}/admin/site/import", content=r.content, headers={"content-type": "application/zip"})
    assert ok.status_code == 200, ok.text
    d = site(client)
    assert d["name"] == "Экспорт" and d["primary_color"] == "#112233" and d["assets"]["logo_compact"] and [x["kind"] for x in d["documents"]] == ["terms"]
    login(client, "alice")
    assert client.put(f"{API}/admin/site/import", content=r.content).status_code == 403
    login(client, "root")
    assert client.put(f"{API}/admin/site/import", content=b"not a zip").status_code == 422
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zz:
        zz.writestr("site.json", '{"app": "peregovorka-site", "format": 1, "settings": {"primary_color": "red"}}')
    assert client.put(f"{API}/admin/site/import", content=bad.getvalue()).status_code == 422
    bad2 = io.BytesIO()
    with zipfile.ZipFile(bad2, "w") as zz:
        zz.writestr("site.json", '{"app": "peregovorka-site", "format": 1, "settings": {}}')
        zz.writestr("assets/logo.png", b"<svg onload=alert(1)>")
    assert client.put(f"{API}/admin/site/import", content=bad2.getvalue()).status_code == 422
    assert site(client)["name"] == "Экспорт", "неудачный импорт ничего не меняет"


def test_reset_group_restores_defaults_and_removes_images(client):
    login(client, "root")
    client.put(f"{API}/admin/settings/site", json={"name": "Х", "support_email": "a@example.com"})
    put_file(client, "/admin/site/assets/logo", png())
    assert client.post(f"{API}/admin/site/reset", json={"group": "oops"}).status_code == 422
    assert client.post(f"{API}/admin/site/reset", json={"group": "brand"}).status_code == 200
    d = site(client)
    assert d["name"] == "Peregovorka" and d["assets"]["logo"] is None and d["support"] is not None, "сброс одной группы не трогает остальные"


def test_branding_survives_restart_of_the_application(tmp_path, directory):
    """Настройки в базе, изображения и документы — в каталоге данных: пересоздание приложения (как после обновления или пересоздания контейнера) их не теряет."""
    settings = make_settings(tmp_path)
    with running_app(settings, directory) as c:
        login(c, "root")
        c.put(f"{API}/admin/settings/site", json={"name": "Сохранится"})
        put_file(c, "/admin/site/assets/favicon", png(64, 64))
        c.put(f"{API}/admin/site/legal/terms", json={"draft_md": "Текст"})
        c.post(f"{API}/admin/site/legal/terms/publish")
    with running_app(make_settings(tmp_path), directory) as c2:
        d = c2.get(f"{API}/public/site").json()
        assert d["name"] == "Сохранится" and d["assets"]["favicon"] and [x["kind"] for x in d["documents"]] == ["terms"]
        assert c2.get(d["assets"]["favicon"]).status_code == 200


def test_mail_template_project_name_comes_from_site_name(client):
    import asyncio

    from app.services import mail_templates as mt

    async def name() -> str:
        async with client.app.state.session_maker() as db:
            return await mt._site_name(db)  # noqa: SLF001

    assert asyncio.run(name()) == "Peregovorka"
    login(client, "root")
    client.put(f"{API}/admin/settings/site", json={"name": "Встречи Компании"})
    assert asyncio.run(name()) == "Встречи Компании"
