"""SIP-телефония через LiveKit SIP Service: профили (транки), синхронизация с LiveKit, исходящие звонки из комнаты, маршрутизация входящих, диагностика.

Схема: АТС (Asterisk/PJSIP или другая) ↔ SIP-транк ↔ LiveKit SIP Service ↔ комната LiveKit Peregovorka. Собственного аудиомоста нет: телефонный абонент —
обычный участник комнаты (identity `p-…` для исходящих, `sip_…` для входящих), его микрофон попадает в стенограмму и запись так же, как браузерный.
Интерфейс называется «SIP-телефония»; Asterisk — первый проверенный вариант, но модель (профиль, транк, правило, участник) принадлежит LiveKit и не привязана к АТС.

Профиль хранит всё, что нужно LiveKit: адрес и порт, транспорт, учётные данные (пароль зашифрован и обратно не отдаётся), caller ID, допустимые номера, кодеки, шифрование
медиа, разрешённые адреса АТС для входящих. Для PJSIP это соответствует связке endpoint/auth/aor и сопоставлению входящих по IP (поле «разрешённые адреса»):
пара «логин+пароль» не обязательна. Входящие звонки на внутренний номер комнаты направляются постоянным правилом LiveKit (dispatch rule) в комнату ИДУЩЕЙ встречи.
Секреты в журнал и аудит не попадают.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import socket
import ssl
import time
import uuid
from typing import Any, Callable

from livekit import api as lk
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import GuestParticipant, Meeting, Room, SipProfile, utcnow
from .settings import SettingsError, SettingsService

log = logging.getLogger("app.sip")

DIRECTIONS = ("outbound", "inbound", "both")
TRANSPORTS = ("udp", "tcp", "tls")
ENCRYPTION = ("disable", "allow", "require")
CODEC_RATES = {"PCMU": 8000, "PCMA": 8000, "G722": 16000, "G729": 8000, "OPUS": 48000}
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_NUMBER = re.compile(r"^[0-9+*#]{1,32}$")
_USER = re.compile(r"^[^\s<>\"']{0,200}$")
_DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
FIELDS = ("name", "enabled", "is_default", "direction", "host", "port", "transport", "username", "realm", "caller_id", "allowed_numbers", "inbound_numbers",
          "allowed_addresses", "codecs", "media_encryption", "ring_timeout_s")


# ------------------------------------------------------------------------------------------------ чистые функции
def mask_number(n: str) -> str:
    """Номер для журнала: середина скрыта (+7•••••1234)."""
    n = n or ""
    return n if len(n) <= 5 else f"{n[:2]}{'•' * (len(n) - 6)}{n[-4:]}"


def normalize_number(raw: str) -> str:
    """Номер для набора: пробелы, скобки и дефисы убираются; допустимы цифры, + * #."""
    s = re.sub(r"[\s().\-]", "", str(raw or ""))
    if not _NUMBER.match(s):
        raise ValueError("Номер: только цифры и символы + * # (до 32 знаков)")
    return s


def number_allowed(profile_patterns: list[str] | None, number: str) -> bool:
    """Номер разрешён, если список шаблонов пуст либо номер начинается с одного из префиксов («+7», «8», «1xx»; * в конце шаблона — любое продолжение)."""
    pats = [p.strip() for p in (profile_patterns or []) if p and p.strip()]
    if not pats:
        return True
    return any(number.startswith(p.rstrip("*")) for p in pats)


def explain_sip_status(code: int | None, reason: str | None = None) -> str:
    """Понятная причина по SIP-коду. Не пытается скрыть сырой код: он показывается рядом."""
    table = {
        400: "АТС не поняла запрос (проверьте формат номера).", 401: "Нужна авторизация: логин или пароль транка неверны либо не заданы.",
        403: "АТС отказала: номер или учётная запись транка не имеет права на этот вызов (проверьте права транка, caller ID и маршруты на АТС).",
        404: "Номер не найден на АТС (нет такого абонента или маршрута).", 407: "АТС требует авторизацию на прокси: проверьте логин и пароль транка.",
        408: "Нет ответа от АТС за отведённое время (проверьте адрес, порт, транспорт и файрвол).", 480: "Абонент временно недоступен.",
        484: "Номер набран не полностью.", 486: "Абонент занят.", 487: "Вызов отменён до ответа.", 488: "Стороны не согласовали кодеки или шифрование медиа (проверьте список кодеков и режим шифрования).",
        500: "Внутренняя ошибка АТС.", 502: "Ошибка шлюза на стороне АТС.", 503: "АТС или маршрут временно недоступны (перегрузка, нет свободных линий).",
        504: "АТС не получила ответ от следующего узла.", 603: "Абонент отклонил вызов.", 604: "Такого абонента не существует.",
    }
    base = table.get(code or 0) or ("Вызов не удался." if not code else (f"Ответ АТС {code}." + ("" if 400 <= code < 700 else "")))
    tail = f" (SIP {code}{' ' + reason if reason else ''})" if code else ""
    return base + tail


def explain_livekit_error(exc: BaseException) -> tuple[str, int | None]:
    """(сообщение, SIP-код) по исключению LiveKit SDK."""
    code = getattr(exc, "sip_status_code", None)
    if code:
        return explain_sip_status(code, getattr(exc, "sip_status", None)), code
    text = str(getattr(exc, "message", "") or exc)
    low = text.lower()
    if "sip" in low and ("not enabled" in low or "no sip" in low or "unavailable" in low or "service" in low and "not" in low):
        return "Служба LiveKit SIP не запущена или не подключена к LiveKit/Redis. Включите телефонию на сервере (Администрирование → SIP-телефония) и проверьте состояние.", None
    if "trunk" in low and ("not found" in low or "no matching" in low):
        return "Транк LiveKit не найден: нажмите «Синхронизировать» в профиле.", None
    if "timeout" in low or "deadline" in low:
        return "LiveKit SIP не ответил вовремя (служба недоступна или АТС не отвечает).", None
    return f"Ошибка LiveKit: {type(exc).__name__}", None


def sip_options_probe(host: str, port: int, transport: str, timeout: float = 3.0) -> dict:
    """Реальная проверка достижимости АТС с сервера приложения: SIP OPTIONS (UDP/TCP) либо рукопожатие TLS. Блокирующая (вызывать в потоке).
    Любой SIP-ответ (в том числе 401/403/404/405) означает «АТС отвечает»; отсутствие ответа по UDP часто значит, что OPTIONS режется файрволом или АТС его игнорирует."""
    started = time.monotonic()

    def done(ok: bool | None, message: str, code: int | None = None) -> dict:
        return {"ok": ok, "message": message, "status": code, "ms": int((time.monotonic() - started) * 1000)}

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM)
    except OSError:
        return done(False, f"Имя «{host}» не найдено в DNS на сервере приложения. Проверьте адрес АТС и DNS.")
    family, stype, proto, _canon, addr = infos[0]
    branch = "z9hG4bK" + secrets.token_hex(6)
    msg = (f"OPTIONS sip:{host}:{port} SIP/2.0\r\nVia: SIP/2.0/{'UDP' if transport == 'udp' else 'TCP'} 0.0.0.0:5060;branch={branch};rport\r\n"
           f"Max-Forwards: 70\r\nFrom: <sip:peregovorka@invalid>;tag={secrets.token_hex(4)}\r\nTo: <sip:{host}:{port}>\r\nCall-ID: {secrets.token_hex(8)}@peregovorka\r\n"
           "CSeq: 1 OPTIONS\r\nContact: <sip:peregovorka@0.0.0.0>\r\nAccept: application/sdp\r\nContent-Length: 0\r\n\r\n").encode()
    try:
        if transport == "udp":
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(msg, addr)
                data, _ = s.recvfrom(4096)
        else:
            with socket.create_connection(addr[:2], timeout=timeout) as raw:
                conn: Any = raw
                if transport == "tls":
                    ctx = ssl.create_default_context()
                    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE        # проверяется достижимость и TLS, а не доверие к сертификату АТС
                    conn = ctx.wrap_socket(raw, server_hostname=host)
                    return done(True, f"TLS-соединение с {host}:{port} установлено ({conn.version()}).")
                raw.settimeout(timeout)
                raw.sendall(msg)
                data = raw.recv(4096)
    except socket.timeout:
        return done(False, "АТС не ответила на SIP OPTIONS за 3 с. Для UDP это часто значит: порт закрыт файрволом или АТС игнорирует OPTIONS; проверьте адрес, порт и транспорт.")
    except ConnectionRefusedError:
        return done(False, f"Соединение с {host}:{port} отклонено: на АТС никто не слушает этот порт/транспорт.")
    except OSError as exc:
        return done(False, f"Нет соединения с {host}:{port} ({exc.strerror or type(exc).__name__}). Проверьте адрес, порт и файрвол.")
    m = re.match(rb"SIP/2\.0\s+(\d{3})\s*([^\r\n]*)", data or b"")
    if not m:
        return done(False, "Получен ответ, не похожий на SIP: проверьте, что по этому порту работает именно SIP.")
    code = int(m.group(1))
    reason = m.group(2).decode("utf-8", "replace")[:60]
    extra = {401: " (требует авторизацию — это нормально)", 403: " (доступ запрещён по адресу — проверьте список разрешённых адресов на АТС)", 404: "", 405: " (OPTIONS не поддерживается, но АТС отвечает)"}.get(code, "")
    return done(True if code < 500 else None, f"АТС отвечает: SIP {code} {reason}{extra}", code)


# ------------------------------------------------------------------------------------------------ профили
class SipService:
    def __init__(self, svc: SettingsService):
        self._svc = svc

    # секрет
    def _aad(self, pid) -> bytes:
        return f"sip_profile.{pid}".encode()

    def encrypt(self, pid, secret: str) -> str:
        if not secret:
            return ""
        if self._svc.box is None:
            raise SettingsError("APP_MASTER_KEY не задан — пароль сохранить нельзя")
        return self._svc.box.encrypt(secret, aad=self._aad(pid))

    def decrypt(self, row: SipProfile) -> str:
        if not row.secret_enc or self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(row.secret_enc, aad=self._aad(row.id))
        except Exception:  # noqa: BLE001
            return ""

    # проверка и очистка значений
    @staticmethod
    def clean(data: dict, *, partial: bool, current: SipProfile | None = None) -> dict:
        out: dict = {}
        g = lambda k, d=None: data[k] if k in data else (getattr(current, k) if current is not None else d)  # noqa: E731

        def need(k: str) -> bool:
            return not partial or k in data

        if need("name"):
            name = str(g("name", "")).strip()
            if not name or len(name) > 120:
                raise SettingsError("Название: 1–120 символов")
            out["name"] = name
        if need("host"):
            host = re.sub(r"^sips?:", "", str(g("host", "")).strip(), flags=re.I).split("/")[0].split(";")[0]
            if ":" in host and host.count(":") == 1:
                host, _, p = host.partition(":")
                if p.isdigit():
                    out["port"] = int(p)
            if not _HOST.match(host):
                raise SettingsError("Адрес АТС: имя или IP без схемы и слэшей (например, pbx.example.local)")
            out["host"] = host
        if "port" not in out and need("port"):
            try:
                out["port"] = int(g("port", 5060))
            except (TypeError, ValueError):
                raise SettingsError("Порт: число от 1 до 65535") from None
        if "port" in out and not 1 <= out["port"] <= 65535:
            raise SettingsError("Порт: число от 1 до 65535")
        for key, allowed, label in (("direction", DIRECTIONS, "Направление: входящий, исходящий или двусторонний"), ("transport", TRANSPORTS, "Транспорт: UDP, TCP или TLS"),
                                    ("media_encryption", ENCRYPTION, "Шифрование медиа: отключено, допускается или обязательно")):
            if need(key):
                v = str(g(key, "both" if key == "direction" else "udp" if key == "transport" else "disable"))
                if v not in allowed:
                    raise SettingsError(label)
                out[key] = v
        if need("username"):
            u = str(g("username", "") or "").strip()
            if not _USER.match(u):
                raise SettingsError("Логин: без пробелов и кавычек")
            out["username"] = u
        if need("realm"):
            r = str(g("realm", "") or "").strip()
            if r and not _DOMAIN.match(r):
                raise SettingsError("Realm/домен: латиница, цифры, точка, дефис")
            out["realm"] = r
        if need("caller_id"):
            c = str(g("caller_id", "") or "").strip()
            if c:
                try:
                    c = normalize_number(c)
                except ValueError:
                    raise SettingsError("Caller ID / номер: только цифры и + * #") from None
            out["caller_id"] = c
        for key in ("allowed_numbers", "inbound_numbers"):
            if need(key):
                vals = g(key, []) or []
                if isinstance(vals, str):
                    vals = re.split(r"[\s,;]+", vals)
                clean: list[str] = []
                for v in vals:
                    v = str(v).strip()
                    if not v:
                        continue
                    if not re.match(r"^[0-9+*#]{1,32}$", v):
                        raise SettingsError(f"«{v[:40]}»: допустимы цифры и + * # (для допустимых номеров — префикс, например +7 или 8*)")
                    if v not in clean:
                        clean.append(v)
                if len(clean) > 100:
                    raise SettingsError("Не больше 100 шаблонов номеров")
                out[key] = clean
        if need("allowed_addresses"):
            vals = g("allowed_addresses", []) or []
            if isinstance(vals, str):
                vals = re.split(r"[\s,;]+", vals)
            nets: list[str] = []
            for v in vals:
                v = str(v).strip()
                if not v:
                    continue
                try:
                    nets.append(str(ipaddress.ip_network(v, strict=False)))
                except ValueError:
                    raise SettingsError(f"Адрес «{v[:60]}» не похож на IP или подсеть (например, 192.0.2.10 или 192.0.2.0/24)") from None
            out["allowed_addresses"] = nets[:50]
        if need("codecs"):
            vals = g("codecs", []) or []
            if isinstance(vals, str):
                vals = re.split(r"[\s,;]+", vals)
            codecs: list[str] = []
            for v in vals:
                v = str(v).strip().upper()
                if not v:
                    continue
                if v not in CODEC_RATES:
                    raise SettingsError(f"Кодек «{v[:20]}» не поддерживается. Доступны: {', '.join(CODEC_RATES)}")
                if v not in codecs:
                    codecs.append(v)
            out["codecs"] = codecs
        if need("ring_timeout_s"):
            try:
                t = int(g("ring_timeout_s", 45))
            except (TypeError, ValueError):
                raise SettingsError("Время ожидания ответа: число секунд от 5 до 120") from None
            if not 5 <= t <= 120:
                raise SettingsError("Время ожидания ответа: от 5 до 120 секунд")
            out["ring_timeout_s"] = t
        for k in ("enabled", "is_default"):
            if need(k):
                out[k] = bool(g(k, k == "enabled"))
        return out

    def public(self, r: SipProfile) -> dict:
        d = {k: getattr(r, k) for k in FIELDS}
        d.update(id=str(r.id), secret_set=bool(r.secret_enc), synced=bool(r.lk_outbound_trunk_id or r.lk_inbound_trunk_id),
                 last_check=r.last_check, last_check_at=r.last_check_at.isoformat() if r.last_check_at else None)
        for k in ("allowed_numbers", "inbound_numbers", "allowed_addresses", "codecs"):
            d[k] = list(d.get(k) or [])
        return d

    async def list(self, db: AsyncSession) -> list[SipProfile]:
        return list((await db.execute(select(SipProfile).order_by(SipProfile.name))).scalars().all())

    async def row(self, db: AsyncSession, pid: str) -> SipProfile:
        try:
            r = await db.get(SipProfile, uuid.UUID(str(pid)))
        except ValueError:
            r = None
        if r is None:
            raise SettingsError("SIP-профиль не найден")
        return r

    async def _only_default(self, db: AsyncSession, keep: uuid.UUID) -> None:
        await db.execute(update(SipProfile).where(SipProfile.id != keep).values(is_default=False))

    async def create(self, db: AsyncSession, data: dict, secret: str) -> SipProfile:
        clean = self.clean(data, partial=False)
        if (await db.execute(select(SipProfile.id).where(SipProfile.name == clean["name"]))).first():
            raise SettingsError("Профиль с таким названием уже есть")
        pid = uuid.uuid4()
        first = not (await db.execute(select(func.count()).select_from(SipProfile))).scalar_one()
        r = SipProfile(id=pid, secret_enc=self.encrypt(pid, secret), **{**clean, "is_default": clean.get("is_default", False) or first})
        db.add(r)
        await db.flush()
        if r.is_default:
            await self._only_default(db, r.id)
        return r

    async def update(self, db: AsyncSession, pid: str, data: dict, secret: str | None) -> SipProfile:
        r = await self.row(db, pid)
        clean = self.clean(data, partial=True, current=r)
        if "name" in clean and clean["name"] != r.name and (await db.execute(select(SipProfile.id).where(SipProfile.name == clean["name"]))).first():
            raise SettingsError("Профиль с таким названием уже есть")
        for k, v in clean.items():
            setattr(r, k, v)
        if secret is not None:
            r.secret_enc = self.encrypt(r.id, secret) if secret else ""
        r.updated_at = utcnow()
        await db.flush()
        if clean.get("is_default"):
            await self._only_default(db, r.id)
        return r

    async def default(self, db: AsyncSession, need: str | None = None) -> SipProfile | None:
        """Профиль по умолчанию (включённый; need — outbound|inbound); если помеченного нет — единственный подходящий."""
        rows = [r for r in await self.list(db) if r.enabled and (need is None or r.direction in (need, "both"))]
        return next((r for r in rows if r.is_default), rows[0] if len(rows) == 1 else None)

    async def for_room(self, db: AsyncSession, room: Room, need: str | None = None) -> SipProfile | None:
        if room.sip_mode == "off":
            return None
        if room.sip_mode == "profile" and room.sip_profile_id:
            r = await db.get(SipProfile, room.sip_profile_id)
            return r if r is not None and r.enabled and (need is None or r.direction in (need, "both")) else None
        return await self.default(db, need)


# ------------------------------------------------------------------------------------------------ шлюз к LiveKit
class SipGateway:
    """Все обращения к LiveKit SIP API. `factory` подменяется в тестах (подставной клиент)."""

    def __init__(self, settings: Settings, factory: Callable[[], Any] | None = None):
        self._s = settings
        self._factory = factory or (lambda: lk.LiveKitAPI(settings.livekit_http_url, settings.livekit_api_key, settings.livekit_api_secret))

    @staticmethod
    def _media(row: SipProfile) -> lk.SIPMediaConfig:
        enc = {"disable": lk.SIP_MEDIA_ENCRYPT_DISABLE, "allow": lk.SIP_MEDIA_ENCRYPT_ALLOW, "require": lk.SIP_MEDIA_ENCRYPT_REQUIRE}[row.media_encryption]
        codecs = [lk.SIPCodec(name=c, rate=CODEC_RATES.get(c, 8000)) for c in (row.codecs or [])]
        return lk.SIPMediaConfig(only_listed_codecs=bool(codecs), codecs=codecs, encryption=enc)

    async def sync_profile(self, row: SipProfile, password: str) -> dict:
        """Создаёт/обновляет транки LiveKit для профиля (исходящий и/или входящий). Возвращает {outbound, inbound} идентификаторы."""
        transport = lk.SIPTransport.Value({"udp": "SIP_TRANSPORT_UDP", "tcp": "SIP_TRANSPORT_TCP", "tls": "SIP_TRANSPORT_TLS"}[row.transport])
        out_id, in_id = row.lk_outbound_trunk_id, row.lk_inbound_trunk_id
        async with self._factory() as api:
            if row.enabled and row.direction in ("outbound", "both"):
                info = lk.SIPOutboundTrunkInfo(name=f"peregovorka:{row.name}"[:120], address=f"{row.host}:{row.port}", transport=transport,
                                               numbers=[row.caller_id] if row.caller_id else [], auth_username=row.username, auth_password=password,
                                               media_encryption=self._media(row).encryption, media=self._media(row))
                if out_id:
                    try:
                        out_id = (await api.sip.update_sip_outbound_trunk(out_id, info)).sip_trunk_id
                    except Exception:  # noqa: BLE001 — транк удалён вручную: создаём заново
                        out_id = (await api.sip.create_sip_outbound_trunk(lk.CreateSIPOutboundTrunkRequest(trunk=info))).sip_trunk_id
                else:
                    out_id = (await api.sip.create_sip_outbound_trunk(lk.CreateSIPOutboundTrunkRequest(trunk=info))).sip_trunk_id
            elif out_id:
                await self._del(api, out_id)
                out_id = None
            if row.enabled and row.direction in ("inbound", "both"):
                info_in = lk.SIPInboundTrunkInfo(name=f"peregovorka:{row.name}"[:120], numbers=list(row.inbound_numbers or []), allowed_addresses=list(row.allowed_addresses or []),
                                                 auth_username=row.username, auth_password=password, auth_realm=row.realm or "", media_encryption=self._media(row).encryption,
                                                 media=self._media(row), ringing_timeout=_duration(row.ring_timeout_s))
                if in_id:
                    try:
                        in_id = (await api.sip.update_sip_inbound_trunk(in_id, info_in)).sip_trunk_id
                    except Exception:  # noqa: BLE001
                        in_id = (await api.sip.create_sip_inbound_trunk(lk.CreateSIPInboundTrunkRequest(trunk=info_in))).sip_trunk_id
                else:
                    in_id = (await api.sip.create_sip_inbound_trunk(lk.CreateSIPInboundTrunkRequest(trunk=info_in))).sip_trunk_id
            elif in_id:
                await self._del(api, in_id)
                in_id = None
        return {"outbound": out_id, "inbound": in_id}

    @staticmethod
    async def _del(api: Any, trunk_id: str) -> None:
        try:
            await api.sip.delete_sip_trunk(lk.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось удалить транк LiveKit", extra={"error": type(exc).__name__})

    async def delete_trunks(self, row: SipProfile) -> None:
        async with self._factory() as api:
            for tid in (row.lk_outbound_trunk_id, row.lk_inbound_trunk_id):
                if tid:
                    await self._del(api, tid)

    async def list_trunk_ids(self) -> set[str]:
        async with self._factory() as api:
            a = await api.sip.list_sip_outbound_trunk(lk.ListSIPOutboundTrunkRequest())
            b = await api.sip.list_sip_inbound_trunk(lk.ListSIPInboundTrunkRequest())
        return {t.sip_trunk_id for t in list(a.items) + list(b.items)}

    async def ensure_rule(self, room: Room, trunk_id: str | None, livekit_room: str) -> str:
        """Постоянное правило для входящих на внутренний номер комнаты → комната идущей встречи. Создаётся при первой встрече и обновляется при следующих."""
        rule = lk.SIPDispatchRule(dispatch_rule_direct=lk.SIPDispatchRuleDirect(room_name=livekit_room))
        info = lk.SIPDispatchRuleInfo(rule=rule, name=f"peregovorka:{room.slug}"[:120], trunk_ids=[trunk_id] if trunk_id else [],
                                      inbound_numbers=[room.sip_extension] if room.sip_extension else [], hide_phone_number=False)
        async with self._factory() as api:
            if room.sip_dispatch_rule_id:
                try:
                    return (await api.sip.update_sip_dispatch_rule(room.sip_dispatch_rule_id, info)).sip_dispatch_rule_id
                except Exception:  # noqa: BLE001 — правило удалено вручную
                    pass
            return (await api.sip.create_sip_dispatch_rule(lk.CreateSIPDispatchRuleRequest(dispatch_rule=info))).sip_dispatch_rule_id

    async def drop_rule(self, rule_id: str) -> None:
        async with self._factory() as api:
            try:
                await api.sip.delete_sip_dispatch_rule(lk.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id))
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось удалить правило входящих", extra={"error": type(exc).__name__})

    async def dial(self, *, trunk_id: str, number: str, room_name: str, identity: str, display_name: str, ring_s: int, wait: bool, timeout: float | None = None) -> Any:
        req = lk.CreateSIPParticipantRequest(sip_trunk_id=trunk_id, sip_call_to=number, room_name=room_name, participant_identity=identity, participant_name=display_name,
                                             wait_until_answered=wait, play_ringtone=True, ringing_timeout=_duration(ring_s))
        async with self._factory() as api:
            return await api.sip.create_sip_participant(req, timeout=timeout)

    async def delete_room(self, room_name: str) -> None:
        async with self._factory() as api:
            await api.room.delete_room(lk.DeleteRoomRequest(room=room_name))

    async def hangup(self, room_name: str, identity: str) -> None:
        async with self._factory() as api:
            await api.room.remove_participant(lk.RoomParticipantIdentity(room=room_name, identity=identity))


def _duration(seconds: int):
    from google.protobuf.duration_pb2 import Duration  # noqa: PLC0415

    return Duration(seconds=int(seconds))


# ------------------------------------------------------------------------------------------------ состояние сервиса
async def service_status(settings: Settings, gateway: SipGateway, profiles: list[SipProfile], transport=None) -> dict:
    """Состояние телефонии: включена ли на сервере, запущена ли служба SIP, связь с LiveKit и Redis, активные транки, порты. Без секретов."""
    import httpx  # noqa: PLC0415

    enabled = str(settings.sip_enabled).strip().lower() in ("yes", "true", "1", "on")
    ports = {"signaling_port": settings.sip_signaling_port, "rtp_start": settings.sip_rtp_start, "rtp_end": settings.sip_rtp_end,
             "media_ip": settings.sip_media_ip or None, "allowed_cidrs": [c for c in re.split(r"[\s,;]+", settings.sip_allowed_cidrs or "") if c]}
    out: dict = {"enabled_on_server": enabled, "ports": ports, "service": {"running": None, "detail": ""}, "livekit": {"ok": None, "detail": ""}, "redis": {"ok": None, "detail": ""},
                 "trunks": {"profiles": len(profiles), "enabled": sum(1 for p in profiles if p.enabled), "synced": sum(1 for p in profiles if p.lk_outbound_trunk_id or p.lk_inbound_trunk_id)}}
    if not enabled:
        out["service"]["detail"] = "Телефония выключена на сервере (SIP_ENABLED=no): служба SIP не запускается, порты не открыты."
        return out
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0), transport=transport) as c:
            r = await c.get(f"{settings.sip_health_url.rstrip('/')}/")
        out["service"] = {"running": r.status_code < 500, "detail": "служба LiveKit SIP отвечает" if r.status_code < 500 else f"служба ответила HTTP {r.status_code}"}
    except httpx.HTTPError as exc:
        out["service"] = {"running": False, "detail": f"служба LiveKit SIP недоступна ({type(exc).__name__}) — проверьте контейнер livekit-sip"}
    try:
        ids = await gateway.list_trunk_ids()
        out["livekit"] = {"ok": True, "detail": "LiveKit принимает SIP-запросы (список транков получен)"}
        out["redis"] = {"ok": True, "detail": "общая шина LiveKit ↔ SIP (Redis) работает"}
        out["trunks"]["in_livekit"] = len(ids)
        out["trunks"]["missing"] = [p.name for p in profiles if p.enabled and ((p.lk_outbound_trunk_id and p.lk_outbound_trunk_id not in ids) or (p.lk_inbound_trunk_id and p.lk_inbound_trunk_id not in ids))]
    except Exception as exc:  # noqa: BLE001
        msg, _ = explain_livekit_error(exc)
        out["livekit"] = {"ok": False, "detail": msg}
        out["redis"] = {"ok": None, "detail": "не проверялось: нет связи с LiveKit"}
    return out


# ------------------------------------------------------------------------------------------------ звонки из комнаты и маршрутизация
class SipRouting:
    """Исходящие звонки и входящая маршрутизация для комнат."""

    def __init__(self, svc: SipService, gateway: SipGateway, session_maker, journal=None):
        self.svc, self.gateway, self._sm, self.journal = svc, gateway, session_maker, journal

    def _emit(self, event: str, level: str, room: Room | None, message: str, data: dict | None = None, meeting_id: str | None = None) -> None:
        if self.journal is not None:
            self.journal.emit("sip", event, level=level, room=room.name if room else None, meeting_id=meeting_id, message=message, data=data or {})

    async def call(self, db: AsyncSession, room: Room, meeting: Meeting, number_raw: str, *, by: str) -> dict:
        if not room.sip_allow_outbound:
            raise SipError("Исходящие звонки в этой комнате не разрешены (Настройки комнаты → Телефония).", 403)
        profile = await self.svc.for_room(db, room, "outbound")
        if profile is None or not profile.lk_outbound_trunk_id:
            raise SipError("Для комнаты не выбран исходящий SIP-профиль или он не синхронизирован с LiveKit (Администрирование → SIP-телефония).", 409)
        try:
            number = normalize_number(number_raw)
        except ValueError as exc:
            raise SipError(str(exc), 422) from None
        if not number_allowed(profile.allowed_numbers, number):
            raise SipError("Этот номер не входит в список допустимых для SIP-профиля.", 403)
        if meeting.ended_at is not None:
            raise SipError("Встреча завершена.", 409)
        guest = GuestParticipant(meeting_id=meeting.id, room_id=room.id, participant_type="phone", display_name=f"Телефон: {number}")
        db.add(guest)
        await db.flush()
        identity = guest.livekit_identity
        try:
            await self.gateway.dial(trunk_id=profile.lk_outbound_trunk_id, number=number, room_name=meeting.livekit_room, identity=identity,
                                    display_name=guest.display_name, ring_s=profile.ring_timeout_s, wait=True, timeout=profile.ring_timeout_s + 10)
        except Exception as exc:  # noqa: BLE001
            msg, code = explain_livekit_error(exc)
            await db.delete(guest)
            await db.flush()
            self._emit("call_failed", "warn", room, f"{mask_number(number)}: {msg}", {"sip_status": code, "profile": profile.name}, str(meeting.id))
            raise SipError(msg, 502, code) from None
        guest.connected_at = utcnow()
        self._emit("call_answered", "info", room, f"Исходящий звонок: {mask_number(number)} ответил", {"profile": profile.name}, str(meeting.id))
        return {"guest_id": str(guest.id), "identity": identity, "display_name": guest.display_name, "profile": profile.name}

    async def hangup(self, db: AsyncSession, room: Room, meeting: Meeting, guest_id: uuid.UUID) -> bool:
        g = await db.get(GuestParticipant, guest_id)
        if g is None or g.meeting_id != meeting.id or not g.is_phone:
            return False
        await self.gateway.hangup(meeting.livekit_room, g.livekit_identity)
        g.left_at = g.left_at or utcnow()
        self._emit("call_hangup", "info", room, f"Телефонный участник отключён: {g.display_name}", {}, str(meeting.id))
        return True

    # входящие
    async def on_meeting_started(self, meeting_id: uuid.UUID) -> None:
        try:
            async with self._sm() as db:
                meeting = await db.get(Meeting, meeting_id)
                if meeting is None or meeting.ended_at is not None:
                    return
                room = meeting.room
                if not (room.sip_allow_inbound and room.sip_extension):
                    return
                profile = await self.svc.for_room(db, room, "inbound")
                if profile is None or not profile.lk_inbound_trunk_id:
                    self._emit("inbound_skipped", "warn", room, "Входящие звонки не включены: нет синхронизированного входящего профиля", {}, str(meeting.id))
                    return
                rule_id = await self.gateway.ensure_rule(room, profile.lk_inbound_trunk_id, meeting.livekit_room)
                room.sip_dispatch_rule_id = rule_id
                await db.commit()
                self._emit("inbound_ready", "info", room, f"Входящие звонки на внутренний номер {room.sip_extension} направляются в эту встречу", {}, str(meeting.id))
        except Exception as exc:  # noqa: BLE001
            msg, _ = explain_livekit_error(exc)
            log.warning("Не удалось подготовить входящие звонки", extra={"error": type(exc).__name__})
            self._emit("inbound_failed", "warn", None, msg, {})

    async def on_meeting_ended(self, meeting_id: uuid.UUID) -> None:
        """После встречи правило входящих снимается: звонок на номер комнаты без встречи отклоняется, а не попадает в «мёртвую» комнату."""
        try:
            async with self._sm() as db:
                meeting = await db.get(Meeting, meeting_id)
                if meeting is None:
                    return
                room = meeting.room
                if room.sip_dispatch_rule_id:
                    await self.gateway.drop_rule(room.sip_dispatch_rule_id)
                    room.sip_dispatch_rule_id = None
                    await db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось снять правило входящих", extra={"error": type(exc).__name__})

    async def register_inbound(self, db: AsyncSession, meeting_id: uuid.UUID, identity: str, attrs: dict[str, str] | None, name: str = "") -> GuestParticipant | None:
        """Входящий телефонный участник вошёл в комнату LiveKit (webhook): заводим запись участника, чтобы он отображался и попадал в стенограмму."""
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.ended_at is not None:
            return None
        found = (await db.execute(select(GuestParticipant).where(GuestParticipant.meeting_id == meeting_id, GuestParticipant.lk_identity == identity))).scalars().first()
        if found is not None:
            found.connected_at = found.connected_at or utcnow()
            found.left_at = None
            return found
        num = (attrs or {}).get("sip.phoneNumber") or (attrs or {}).get("sip.callerNumber") or name or identity.removeprefix("sip_")
        num = re.sub(r"[^0-9+*#A-Za-z_.-]", "", str(num))[:40]
        g = GuestParticipant(meeting_id=meeting_id, room_id=meeting.room_id, participant_type="phone", display_name=f"Телефон: {num}"[:120], lk_identity=identity[:160],
                             connected_at=utcnow())
        db.add(g)
        meeting.empty_since = None
        await db.flush()
        self._emit("inbound_joined", "info", meeting.room, f"Входящий звонок: {mask_number(num)}", {}, str(meeting.id))
        return g


class SipError(Exception):
    def __init__(self, message: str, status: int = 400, sip_status: int | None = None):
        super().__init__(message)
        self.message, self.status, self.sip_status = message, status, sip_status
