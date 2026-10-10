"""Bitrix24 как дополнительный источник профиля (REST, входящий webhook). Только ЧТЕНИЕ сотрудников; запись в Bitrix не выполняется.

Нужны права вебхука на чтение пользователей (минимально — `user_brief`; для телефонов и e-mail — `user_basic`) и, для названий подразделений, `department`.
Секрет в адресе вебхука (`https://портал/rest/<id>/<секрет>/`) нигде не показывается и не пишется в журнал; сообщения об ошибках его не содержат.
Сопоставление с учётной записью — по e-mail (или по сохранённому идентификатору Bitrix); неоднозначное совпадение не используется (тёзки/дубли).
"""
from __future__ import annotations

import ipaddress
import re
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


_API_ERRORS = {
    "INVALID_CREDENTIALS": "webhook недействителен: проверьте номер и секрет в адресе или создайте новый входящий вебхук",
    "NO_AUTH_FOUND": "портал не принял авторизацию: проверьте адрес webhook (/rest/<номер>/<секрет>/)",
    "WRONG_AUTH_TYPE": "этот метод недоступен для входящего вебхука",
    "ACCESS_DENIED": "у вебхука нет права на этот метод: добавьте нужное право (scope) в настройках вебхука на портале",
    "INSUFFICIENT_SCOPE": "не хватает права (scope) у вебхука: добавьте «Пользователи» (и «Подразделения») в его настройках",
    "ERROR_METHOD_NOT_FOUND": "метод не найден: возможно, вебхук создан не в Bitrix24 или версия портала не поддерживает метод",
    "QUERY_LIMIT_EXCEEDED": "превышен лимит запросов Bitrix24: приложение повторит позже, ничего делать не нужно",
    "EXPIRED_TOKEN": "срок действия токена истёк: создайте новый вебхук",
    "INTERNAL_SERVER_ERROR": "внутренняя ошибка портала: повторите позже",
    "ERROR_CORE": "внутренняя ошибка портала: повторите позже",
    "OVERLOAD_LIMIT": "портал перегружен: повторите позже",
}


def explain_api_error(code: str, description: str, method: str) -> str:
    """Понятное объяснение кода ошибки REST Bitrix24 (без секретов; описание портала обрезается)."""
    key = code.strip().upper()
    base = _API_ERRORS.get(key)
    detail = re.sub(r"/rest/\S+", "/rest/…", " ".join(description.split()))[:120]       # описание портала может содержать адрес вебхука — секрет не выводим
    if base:
        return f"портал отклонил «{method}»: {base}"
    return f"портал отклонил «{method}» (код {code.strip()[:40]}){': ' + detail if detail else ''}"


def explain_http(status: int) -> str:
    if status in (401, 403):
        return f"портал отказал в доступе (HTTP {status}): проверьте webhook и ограничения по IP-адресам на портале"
    if status == 404:
        return "адрес webhook не найден (HTTP 404): проверьте адрес портала и часть /rest/<номер>/<секрет>/"
    if status == 429:
        return "портал ограничил частоту запросов (HTTP 429): приложение повторит позже"
    if status >= 500:
        return f"портал временно недоступен (HTTP {status}): повторите позже"
    return f"портал ответил HTTP {status}"


def net_reason(exc: Exception) -> str:
    """Причина сетевого сбоя по-человечески, без адресов и секретов."""
    text = f"{type(exc).__name__} {exc}".lower()
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "сертификат портала не принят: включите «Доверять корпоративному удостоверяющему центру», если портал использует внутренний сертификат, либо проверьте срок и цепочку сертификата"
    if any(w in text for w in ("getaddrinfo", "name or service", "nodename", "name resolution", "no address")):
        return "имя портала не разрешается (DNS): проверьте адрес портала и DNS на сервере приложения"
    if "refused" in text:
        return "соединение отклонено порталом (порт закрыт или сервис не запущен)"
    return f"нет соединения с порталом ({type(exc).__name__}): проверьте доступность портала с сервера приложения"


class Bitrix24Provider:
    name = "bitrix"

    def __init__(self, cfg, webhook_url: str, *, transport: httpx.AsyncBaseTransport | None = None, ca_file: str | None = None):
        self._cfg = cfg
        self._base = webhook_url.strip().rstrip("/") + "/"
        self._portal = urlsplit(cfg.portal_url.strip())
        self._transport = transport
        self._verify: bool | str = (ca_file or True) if cfg.verify_tls else False
        self._depts: dict[str, str] = {}
        self.last_match = ""                   # результат последнего поиска: found | not_found | inactive | ambiguous (для диагностики администратора)

    # ------------------------------------------------------------------ транспорт
    def _client(self) -> httpx.AsyncClient:
        t = float(self._cfg.timeout)
        return httpx.AsyncClient(timeout=httpx.Timeout(t, connect=min(t, 3.0)), follow_redirects=False, verify=self._verify, transport=self._transport)

    async def _call(self, method: str, params: dict | None = None) -> dict:
        try:
            async with self._client() as c:
                r = await c.post(f"{self._base}{method}.json", json=params or {})
        except httpx.TimeoutException:
            raise ProviderError(f"портал не ответил за {self._cfg.timeout} с: проверьте, что сервер приложения достаёт до портала по сети (файрвол, прокси)") from None
        except httpx.HTTPError as exc:
            raise ProviderError(net_reason(exc)) from None
        if 300 <= r.status_code < 400:
            raise ProviderError(f"портал перенаправляет запрос (HTTP {r.status_code}): укажите точный адрес портала и webhook с https")
        if len(r.content) > MAX_JSON:
            raise ProviderError("ответ портала слишком велик")
        try:
            data = r.json()
        except ValueError:
            hint = " (возможно, это не REST-адрес Bitrix24 или перед порталом страница защиты)" if r.status_code < 400 else ""
            raise ProviderError(f"портал вернул не JSON (HTTP {r.status_code}){hint}") from None
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(explain_api_error(str(data.get("error")), str(data.get("error_description") or ""), method))
        if r.status_code >= 400:
            raise ProviderError(explain_http(r.status_code))
        if not isinstance(data, dict):
            raise ProviderError("некорректный ответ портала")
        return data

    # ------------------------------------------------------------------ диагностика
    async def diagnose(self) -> dict:
        """Пошаговая проверка для администратора: соединение и владелец вебхука, выданные права (scope), чтение сотрудников, подразделения.
        Ничего не сохраняет. Возвращает {ok, who, scopes, steps:[{name, ok, message}], will_use:{…}}."""
        steps: list[dict] = []
        out: dict = {"ok": False, "who": None, "scopes": None, "steps": steps, "will_use": {}}

        def step(name: str, ok: bool, message: str) -> None:
            steps.append({"name": name, "ok": ok, "message": message})

        try:
            res = (await self._call("user.current")).get("result") or {}
            who = clean("display_name", " ".join(str(res.get(k) or "") for k in ("LAST_NAME", "NAME"))) or "сотрудник"
            out["who"] = who
            step("Соединение и авторизация", True, f"Webhook создан от имени: {who}.")
        except ProviderError as exc:
            step("Соединение и авторизация", False, str(exc))
            return out
        scopes: list[str] | None = None
        try:
            raw = (await self._call("scope")).get("result")
            scopes = sorted({str(s) for s in raw}) if isinstance(raw, list) else None
        except ProviderError as exc:
            step("Права вебхука (scope)", False, f"Не удалось получить список прав: {exc}. Проверка чтения ниже покажет, достаточно ли их.")
        if scopes is not None:
            out["scopes"] = scopes
            can_users = any(s in scopes for s in ("user", "user_brief", "user_basic", "user.userfield"))
            msg = f"Выданы: {', '.join(scopes) or 'нет'}."
            if not can_users:
                msg += " Нет права на пользователей: добавьте «Пользователи (user_brief или user_basic)» в настройках вебхука."
            step("Права вебхука (scope)", can_users, msg)
            out["will_use"]["department"] = "department" in scopes
        try:
            data = await self._call("user.get", {"FILTER": {"ACTIVE": True}, "start": 0})
            n = len(data.get("result") or [])
            step("Чтение сотрудников", True, f"Список сотрудников доступен (получено {n}, всего на портале {data.get('total', n)}).")
            out["will_use"]["users"] = True
        except ProviderError as exc:
            step("Чтение сотрудников", False, str(exc))
            out["will_use"]["users"] = False
        try:
            data = await self._call("department.get")
            step("Подразделения", True, f"Подразделения доступны ({len(data.get('result') or [])}). Название подразделения сотрудника будет подставляться.")
            out["will_use"]["department"] = True
        except ProviderError as exc:
            step("Подразделения", False, f"{exc}. Это не помешает остальному: подразделение просто останется пустым (или из каталога).")
            out["will_use"]["department"] = False
        out["ok"] = bool(out["will_use"].get("users")) and steps[0]["ok"]
        return out

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
        found = [r for r in (data.get("result") or []) if isinstance(r, dict)]
        rows = [r for r in found if str(r.get("ACTIVE", "Y")).upper() not in ("N", "FALSE", "0")]
        if len(rows) != 1:
            # нет такого, только неактивные или неоднозначно (дубли/тёзки) — не угадываем
            self.last_match = "ambiguous" if len(rows) > 1 else ("inactive" if found else "not_found")
            return None
        self.last_match = "found"
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
