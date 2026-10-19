"""EWS: Basic, NTLM (настоящее рукопожатие: серверная сторона — pyspnego), Negotiate/Kerberos, автоопределение, несколько серверов, права на календари, отсутствие паролей в ошибках.
Настоящего Exchange и KDC нет: NTLM проверяется встречным серверным контекстом; Kerberos — до границы системных библиотек (без KDC получить билет нельзя)."""
from __future__ import annotations

import base64
from datetime import timedelta

import httpx
import pytest

from app.models import utcnow
from app.services import ews_auth, outlook_sync as osync

from .test_outlook_sync import _TICK, _create, _run, ews_item, ews_response  # noqa: F401
from .conftest import login

PASSWORD = "Pa$$w0rd-Секрет!"
HOST = "https://mail.example.org/EWS/Exchange.asmx"


# ------------------------------------------------------------------------------------------------ чистые функции
def test_urls_are_normalized_without_hardcoded_hosts():
    assert ews_auth.normalize_urls("mail.example.org") == ["https://mail.example.org/EWS/Exchange.asmx"]
    assert ews_auth.normalize_urls("https://cas1.example.org/EWS/Exchange.asmx;\ncas2.example.org:444\nhttps://cas1.example.org") == [
        "https://cas1.example.org/EWS/Exchange.asmx", "https://cas2.example.org:444/EWS/Exchange.asmx"]
    for bad in ("http://mail.example.org/EWS/Exchange.asmx", "https://user:pw@mail.example.org/", ""):
        if bad:
            with pytest.raises(ValueError):
                ews_auth.normalize_urls(bad)
    with pytest.raises(ValueError):
        ews_auth.normalize_urls(" ".join(f"h{i}.example.org" for i in range(12)))


def test_credentials_accept_domain_forms():
    assert ews_auth.credentials("svc", "CORP")[0] == "CORP\\svc"
    assert ews_auth.credentials("CORP\\svc", "OTHER")[0] == "CORP\\svc" and ews_auth.credentials("svc@example.org", "CORP")[0] == "svc@example.org"


def test_schemes_choice_and_redaction():
    offered = ews_auth.parse_schemes(["Negotiate", "NTLM", 'Basic realm="mail.example.org"'])
    assert offered == ["negotiate", "ntlm", "basic"]
    assert ews_auth.choose(offered, "auto") == "basic" and ews_auth.choose(["negotiate", "ntlm"], "auto") == "ntlm" and ews_auth.choose(["bearer"], "auto") is None
    assert ews_auth.choose(["ntlm"], "basic") is None and ews_auth.choose(["negotiate"], "kerberos") == "kerberos"
    assert PASSWORD not in ews_auth.redact(f"ошибка входа с паролем {PASSWORD}", PASSWORD)
    assert "Zm9v" not in ews_auth.redact("Authorization: Basic Zm9vOmJhcg==", PASSWORD)


# ------------------------------------------------------------------------------------------------ подставной Exchange с NTLM
def _ntlm_user_file(tmp_path, user="svc", domain="", password=PASSWORD):
    f = tmp_path / "ntlm_users"
    f.write_text(f"{domain}:{user}:{password}\n", encoding="utf-8")        # формат Heimdal: ДОМЕН:ПОЛЬЗОВАТЕЛЬ:ПАРОЛЬ — только в тесте
    return f


class FakeExchange:
    """Один сервер EWS. offers — способы входа в WWW-Authenticate; ntlm — настоящий серверный NTLM; items — ответы на GetItem/GetFolder."""

    def __init__(self, tmp_path, monkeypatch, offers=("NTLM",), items=None, password=PASSWORD, status=200, deny_impersonation=False, deny_all=False):
        monkeypatch.setenv("NTLM_USER_FILE", str(_ntlm_user_file(tmp_path, password=password)))
        self.offers, self.items, self.status, self.seen, self.ctx = offers, items or [], status, [], None
        self.deny_impersonation, self.deny_all = deny_impersonation, deny_all
        self.authed = 0

    def challenge(self, extra=None):
        h = [("WWW-Authenticate", o) for o in self.offers]
        if extra:
            h = [("WWW-Authenticate", extra)] + [x for x in h if not x[1].upper().startswith("NTLM")]
        return httpx.Response(401, headers=h)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        import spnego

        self.seen.append(req)
        if self.status != 200:
            return httpx.Response(self.status, content=b"error")
        auth = req.headers.get("authorization", "")
        if req.method == "GET":
            return self.challenge()
        if auth.lower().startswith("basic "):
            user, _, pw = base64.b64decode(auth.split()[1]).decode().partition(":")
            if "BASIC" not in [o.upper() for o in self.offers] or pw != PASSWORD:
                return self.challenge()
        elif auth.startswith("NTLM "):
            tok = base64.b64decode(auth.split()[1])
            if self.ctx is None or tok[8:9] == b"\x01":
                self.ctx = spnego.server(protocol="ntlm")
            try:
                out = self.ctx.step(tok)
            except Exception:  # noqa: BLE001
                self.ctx = None
                return self.challenge()
            if not self.ctx.complete:
                return self.challenge("NTLM " + base64.b64encode(out).decode())
            self.ctx = None
        else:
            return self.challenge()
        self.authed += 1
        return self.soap(req)

    def soap(self, req):
        body = req.content.decode()
        if self.deny_all or (self.deny_impersonation and "ExchangeImpersonation" in body):
            return httpx.Response(200, content=ews_response("ErrorImpersonateUserDenied" if "ExchangeImpersonation" in body else "ErrorAccessDenied", "Error").encode())
        if not self.items:
            return httpx.Response(200, content=ews_response("ErrorItemNotFound", "Error").encode())
        return httpx.Response(200, content=self.items.pop(0).encode())


def _item(hours=6, subject="Планёрка", cancelled="false"):
    s = utcnow() + timedelta(hours=hours)
    return ews_response(item=ews_item(s, s + timedelta(minutes=45), subject=subject, cancelled=cancelled))


def _client(tx, **kw):
    kw.setdefault("auth", "auto")
    return osync.EwsClient(kw.pop("url", HOST), "svc", PASSWORD, tx, **kw)


async def _event(c):
    return await c.get_event("owner@example.org", None, "AAMkAGEwsId0123456789==")


@pytest.mark.asyncio
async def test_ntlm_handshake_works_and_auto_falls_back_from_basic(tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("Negotiate", "NTLM"), items=[_item()])
    c = _client(httpx.MockTransport(fx))
    ev = await _event(c)
    assert ev.exists and ev.subject == "Планёрка" and c.used_scheme == "ntlm" and fx.authed == 1
    assert any(r.headers.get("authorization", "").startswith("NTLM ") for r in fx.seen)


@pytest.mark.asyncio
async def test_explicit_ntlm_and_wrong_password_never_leaks_the_password(tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[_item()])
    ok = _client(httpx.MockTransport(fx), auth="ntlm")
    assert (await _event(ok)).exists
    bad = osync.EwsClient(HOST, "svc", "не-тот-пароль", httpx.MockTransport(FakeExchange(tmp_path, monkeypatch, offers=("NTLM",))), auth="ntlm")
    with pytest.raises(osync.CalendarError) as e:
        await _event(bad)
    assert "отклонил учётную запись" in str(e.value) and "не-тот-пароль" not in str(e.value) and "ДОМЕН" in str(e.value)


@pytest.mark.asyncio
async def test_server_not_offering_chosen_scheme_is_explained(tmp_path, monkeypatch):
    c = _client(httpx.MockTransport(FakeExchange(tmp_path, monkeypatch, offers=("NTLM",))), auth="basic")
    with pytest.raises(osync.CalendarError) as e:
        await _event(c)
    assert "не предлагает способ «Basic»" in str(e.value) and "NTLM" in str(e.value)


@pytest.mark.asyncio
async def test_basic_still_works_and_wrong_basic_password_is_not_retried_with_other_schemes(tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("Basic", "NTLM"), items=[_item()])
    c = _client(httpx.MockTransport(fx))
    assert (await _event(c)).exists and c.used_scheme == "basic"
    bad = osync.EwsClient(HOST, "svc", "wrong", httpx.MockTransport(fx), auth="auto")
    n = len(fx.seen)
    with pytest.raises(osync.CalendarError):
        await _event(bad)
    assert len(fx.seen) - n == 1                                     # одна попытка: учётную запись не блокируем повторами


@pytest.mark.asyncio
async def test_failover_across_servers_but_not_on_auth_error(tmp_path, monkeypatch):
    good = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[_item()])

    def route(req: httpx.Request) -> httpx.Response:
        if req.url.host == "cas1.example.org":
            raise httpx.ConnectError("refused")
        return good(req)
    c = _client(httpx.MockTransport(route), url="cas1.example.org\ncas2.example.org", auth="ntlm")
    assert (await _event(c)).exists and c.used_url.startswith("https://cas2.example.org")
    down = _client(httpx.MockTransport(lambda r: httpx.Response(503)), url="cas1.example.org\ncas2.example.org", auth="ntlm")
    with pytest.raises(osync.CalendarError) as e:
        await _event(down)
    assert "cas1.example.org" in str(e.value) and "cas2.example.org" in str(e.value) and "HTTP 503" in str(e.value)


@pytest.mark.asyncio
async def test_unreachable_403_404_are_distinct_messages(tmp_path, monkeypatch):
    def boom(req): raise httpx.ConnectError("x")
    for tx, needle in ((httpx.MockTransport(boom), "Exchange недоступен"), (httpx.MockTransport(lambda r: httpx.Response(403)), "HTTP 403"), (httpx.MockTransport(lambda r: httpx.Response(404)), "HTTP 404")):
        with pytest.raises(osync.CalendarError) as e:
            await _event(_client(tx, auth="ntlm"))
        assert needle in str(e.value)


@pytest.mark.asyncio
async def test_rights_are_separate_from_login_impersonation_then_delegate(tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[_item()], deny_impersonation=True)
    c = _client(httpx.MockTransport(fx))
    ev = await _event(c)                                              # имперсонация отказана → делегированный доступ («Рецензент») сработал
    assert ev.exists and "ExchangeImpersonation" not in fx.seen[-1].content.decode()
    deny = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), deny_all=True)
    with pytest.raises(osync.CalendarError) as e:
        await _event(_client(httpx.MockTransport(deny)))
    assert "Успешный вход в EWS сам по себе прав на чужие календари не даёт" in str(e.value)
    with pytest.raises(osync.CalendarError) as e2:
        await _client(httpx.MockTransport(deny)).probe("owner@example.org")
    assert "Вход выполнен" in str(e2.value) and "Рецензент" in str(e2.value)
    okp = await _client(httpx.MockTransport(FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[ews_response(item="")]))).probe("owner@example.org")
    assert "Вход выполнен (NTLM" in okp and "Права на календарь owner@example.org: есть" in okp


@pytest.mark.asyncio
async def test_detect_reports_offered_schemes_and_unreachable_hosts(tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("Negotiate", "NTLM"))

    def route(req):
        if req.url.host == "dead.example.org":
            raise httpx.ConnectError("x")
        return fx(req)
    res = await _client(httpx.MockTransport(route), url="ok.example.org\ndead.example.org").detect()
    assert res[0]["schemes"] == ["negotiate", "ntlm"] and res[0]["recommended"] == "ntlm" and res[0]["reachable"]
    assert not res[1]["reachable"] and "недоступен" in res[1]["message"].lower() or "dead.example.org" in res[1]["message"]


@pytest.mark.asyncio
async def test_kerberos_failure_is_explained_without_password(monkeypatch):
    import spnego

    def no_kdc(*a, **k):
        raise RuntimeError(f"Cannot find KDC for realm EXAMPLE.ORG (пароль {PASSWORD})")
    monkeypatch.setattr(spnego, "client", no_kdc)
    c = _client(httpx.MockTransport(lambda r: httpx.Response(401, headers=[("WWW-Authenticate", "Negotiate")])), auth="kerberos")
    with pytest.raises(osync.CalendarError) as e:
        await _event(c)
    assert "KDC" in str(e.value) and "HTTP/mail.example.org" in str(e.value) and PASSWORD not in str(e.value)


@pytest.mark.asyncio
async def test_negotiate_flow_with_a_context_that_completes_on_the_first_token(monkeypatch):
    import spnego

    class Ctx:
        complete = False
        def step(self, token=None):
            self.complete = True
            return b"ap-req"
    monkeypatch.setattr(spnego, "client", lambda *a, **k: Ctx())
    seen = []

    def h(req):
        seen.append(req.headers.get("authorization", ""))
        return httpx.Response(200, content=ews_response(item=ews_item(utcnow() + timedelta(hours=2), utcnow() + timedelta(hours=3))).encode())
    c = _client(httpx.MockTransport(h), auth="negotiate")
    assert (await _event(c)).exists and seen[0].startswith("Negotiate ")


# ------------------------------------------------------------------------------------------------ сквозная сверка через NTLM: чтение, перенос, отмена, недоступность
def test_reconcile_over_ntlm_reads_reschedules_cancels_and_survives_outages(client, tmp_path, monkeypatch):
    j, *_ = _create(client)
    fx = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[_item(hours=9, subject="Новая тема")])
    r = _run(client, httpx.MockTransport(fx), mode="ews", ews_auth="ntlm", ews_user="svc", ews_password=PASSWORD)
    assert r["rescheduled"] == 1                                       # время и тема изменены в Outlook → обновлены, ссылка прежняя
    fx2 = FakeExchange(tmp_path, monkeypatch, offers=("NTLM",), items=[_item(hours=9, cancelled="true")])
    assert _run(client, httpx.MockTransport(fx2), mode="ews", ews_auth="ntlm", ews_user="svc", ews_password=PASSWORD)["cancelled"] == 1    # событие отменено → комната отменена
    j2, *_ = _create(client)
    out = _run(client, httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))), mode="ews", ews_auth="ntlm", ews_user="svc", ews_password=PASSWORD)
    assert out["error"] >= 1 and out["cancelled"] == 0                # недоступность Exchange комнату не отменяет
    badpw = _run(client, httpx.MockTransport(FakeExchange(tmp_path, monkeypatch, offers=("NTLM",))), mode="ews", ews_auth="ntlm", ews_user="svc", ews_password="неверный")
    assert badpw["error"] >= 1 and badpw["cancelled"] == 0


def test_settings_api_validates_and_keeps_old_installs_working(client):
    login(client, "root")
    r = client.put("/api/v1/admin/settings/outlook", json={"ews_url": "mail.example.org", "ews_auth": "ntlm", "ews_domain": "CORP"})
    assert r.status_code == 200
    got = client.get("/api/v1/admin/settings/outlook").json()
    vals = got.get("values", got)
    assert vals["ews_url"] == "https://mail.example.org/EWS/Exchange.asmx" and vals["ews_auth"] == "ntlm"
    assert client.put("/api/v1/admin/settings/outlook", json={"ews_url": "http://mail.example.org/EWS/Exchange.asmx"}).status_code == 422
    assert client.put("/api/v1/admin/settings/outlook", json={"ews_auth": "digest"}).status_code == 422
    assert client.put("/api/v1/admin/settings/outlook", json={"ews_kdc": "kdc;rm -rf"}).status_code == 422
    from app.services.settings import OutlookSettings
    old = OutlookSettings(sync_mode="ews", ews_url="https://m.example.org/EWS/Exchange.asmx", ews_user="u", ews_password="p")          # настройки без новых полей
    assert old.ews_auth == "auto" and old.ews_access == "auto"


def test_admin_detect_endpoint_needs_admin_and_never_needs_credentials(client, tmp_path, monkeypatch):
    fx = FakeExchange(tmp_path, monkeypatch, offers=("Negotiate", "NTLM"))
    client.app_obj.state.test_transports = {"outlook_sync": httpx.MockTransport(fx)}
    assert client.post("/api/v1/admin/outlook/ews-detect", json={"url": "mail.example.org"}).status_code in (401, 403)
    login(client, "root")
    r = client.post("/api/v1/admin/outlook/ews-detect", json={"url": "mail.example.org"})
    assert r.status_code == 200 and r.json()["recommended"] == "ntlm" and r.json()["servers"][0]["schemes"] == ["negotiate", "ntlm"]
    assert all("authorization" not in q.headers for q in fx.seen)             # без учётных данных
    assert client.post("/api/v1/admin/outlook/ews-detect", json={"url": "http://insecure.example.org"}).status_code == 422
