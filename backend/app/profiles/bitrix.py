"""Bitrix24 как дополнительный источник профиля (REST, входящий webhook). Только ЧТЕНИЕ сотрудников; запись в Bitrix не выполняется.

Нужны права вебхука на чтение пользователей (минимально — `user_brief`; для телефонов и e-mail — `user_basic`) и, для названий подразделений, `department`.
Секрет в адресе вебхука (`https://портал/rest/<id>/<секрет>/`) нигде не показывается и не пишется в журнал; сообщения об ошибках его не содержат.
Сопоставление с учётной записью — по e-mail (или по сохранённому идентификатору Bitrix); неоднозначное совпадение не используется (тёзки/дубли).
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import httpx

from .base import ProviderError, SourceProfile, clean

MAX_JSON = 2 * 1024 * 1024
MAX_PHOTO = 5 * 1024 * 1024


def webhook_ok(portal_url: str, webhook_url: str) -> str | None:
    """Описание проблемы с адресами или None, если всё допустимо (веб-хук обязан принадлежать указанному порталу)."""
    p, w = urlsplit(portal_url.strip()), urlsplit(webhook_url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        return "Адрес портала: https://… (например, https://portal.example.com)"
    if w.scheme not in ("http", "https") or not w.hostname:
        return "Webhook: полный адрес вида https://портал/rest/<номер>/<секрет>/"
    if (w.hostname or "").lower() != (p.hostname or "").lower() or (w.port or 0) != (p.port or 0):
        return "Webhook должен принадлежать указанному порталу"
    if "/rest/" not in w.path:
        return "Webhook: в адресе должен быть фрагмент /rest/<номер>/<секрет>/"
    try:                                       # прямой адрес loopback/link-local (метаданные облака) не допускаем: это не корпоративный портал
        ip = ipaddress.ip_address(p.hostname or "")
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return "Недопустимый адрес портала"
    except ValueError:
        pass
    return None


class Bitrix24Provider:
    name = "bitrix"

    def __init__(self, cfg, webhook_url: str, *, transport: httpx.AsyncBaseTransport | None = None, ca_file: str | None = None):
        self._cfg = cfg
        self._base = webhook_url.strip().rstrip("/") + "/"
        self._portal = urlsplit(cfg.portal_url.strip())
        self._transport = transport
        self._verify: bool | str = (ca_file or True) if cfg.verify_tls else False
        self._depts: dict[str, str] = {}

    # ------------------------------------------------------------------ транспорт
    def _client(self) -> httpx.AsyncClient:
        t = float(self._cfg.timeout)
        return httpx.AsyncClient(timeout=httpx.Timeout(t, connect=min(t, 3.0)), follow_redirects=False, verify=self._verify, transport=self._transport)

    async def _call(self, method: str, params: dict | None = None) -> dict:
        try:
            async with self._client() as c:
                r = await c.post(f"{self._base}{method}.json", json=params or {})
        except httpx.TimeoutException:
            raise ProviderError(f"портал не ответил за {self._cfg.timeout} с") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"нет соединения с порталом ({type(exc).__name__})") from None
        if len(r.content) > MAX_JSON:
            raise ProviderError("ответ портала слишком велик")
        try:
            data = r.json()
        except ValueError:
            raise ProviderError(f"портал вернул не JSON (HTTP {r.status_code})") from None
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"портал отклонил запрос: {str(data.get('error'))[:60]}")
        if r.status_code >= 400:
            raise ProviderError(f"портал ответил HTTP {r.status_code}")
        if not isinstance(data, dict):
            raise ProviderError("некорректный ответ портала")
        return data

    # ------------------------------------------------------------------ операции
    async def check(self) -> str:
        """Проверка соединения и прав: `user.current` (кто владелец вебхука)."""
        data = await self._call("user.current")
        res = data.get("result") or {}
        who = clean("display_name", " ".join(str(res.get(k) or "") for k in ("LAST_NAME", "NAME"))) or "сотрудник"
        return f"Соединение установлено. Webhook создан от имени: {who}."

    async def _department_name(self, dep_id: str) -> str | None:
        if dep_id in self._depts:
            return self._depts[dep_id] or None
        try:
            data = await self._call("department.get", {"ID": [dep_id]})
            rows = data.get("result") or []
            name = clean("department", rows[0].get("NAME")) if rows and isinstance(rows[0], dict) else None
        except ProviderError:
            name = None                      # нет права на подразделения — остаёмся без названия, остальное работает
        self._depts[dep_id] = name or ""
        return name

    async def fetch(self, *, email: str | None, external_id: str | None) -> SourceProfile | None:
        if external_id:
            flt = {"ID": str(external_id)}
        elif email:
            flt = {"EMAIL": email}
        else:
            return None
        data = await self._call("user.get", {"FILTER": flt})
        rows = [r for r in (data.get("result") or []) if isinstance(r, dict) and str(r.get("ACTIVE", "Y")).upper() not in ("N", "FALSE", "0")]
        if len(rows) != 1:
            return None                        # нет такого или неоднозначно (дубли/тёзки) — не угадываем
        u = rows[0]
        cfg = self._cfg
        fields: dict[str, str] = {}
        if u.get("EMAIL"):
            fields["email"] = clean("email", u["EMAIL"]) or ""
        name = clean("display_name", " ".join(str(u.get(k) or "") for k in ("LAST_NAME", "NAME", "SECOND_NAME")))
        if name:
            fields["display_name"] = name
        if cfg.use_title and u.get("WORK_POSITION"):
            fields["title"] = clean("title", u["WORK_POSITION"]) or ""
        if cfg.use_phone:
            ph = clean("phone", u.get("WORK_PHONE") or u.get("PERSONAL_MOBILE") or u.get("PERSONAL_PHONE"))
            if ph:
                fields["phone"] = ph
        if cfg.use_department:
            deps = u.get("UF_DEPARTMENT")
            deps = deps if isinstance(deps, list) else ([deps] if deps else [])
            if deps:
                nm = await self._department_name(str(deps[0]))
                if nm:
                    fields["department"] = nm
        prof = SourceProfile(fields={k: v for k, v in fields.items() if v}, external_id=str(u.get("ID") or "") or None)
        if cfg.use_photos and isinstance(u.get("PERSONAL_PHOTO"), str) and u["PERSONAL_PHOTO"]:
            prof.photo_url = u["PERSONAL_PHOTO"]
            prof.photo = await self._photo(u["PERSONAL_PHOTO"])
        return prof

    async def _photo(self, url: str) -> bytes | None:
        """Фото с портала: только с того же узла, что указан в настройках, без редиректов, ≤5 МБ, только image/*. Любой сбой — просто без фото."""
        full = url if "://" in url else f"{self._portal.scheme}://{self._portal.netloc}{url if url.startswith('/') else '/' + url}"
        sp = urlsplit(full)
        if (sp.hostname or "").lower() != (self._portal.hostname or "").lower() or sp.scheme not in ("http", "https"):
            return None
        try:
            async with self._client() as c:
                r = await c.get(full)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or len(r.content) > MAX_PHOTO or not r.headers.get("content-type", "").lower().startswith("image/"):
            return None
        return r.content
