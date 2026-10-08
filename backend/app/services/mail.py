"""Исходящая почта: профили SMTP, отправка и пошаговая диагностика.

Минимальный современный набор для SMTP-relay (Exchange и обычные серверы): адрес и порт, защита None / STARTTLS / SSL-TLS, аутентификация
(нет / логин и пароль), адрес и имя отправителя, таймаут. Пароль хранится зашифрованно (как остальные служебные пароли) и обратно не отдаётся.
Проверка сертификата сервера почты — по системным CA и по набору CA из раздела «Сертификаты (CA)»; отключить её можно только явной настройкой профиля.

Ошибки классифицируются по этапам: dns → connect → tls/certificate → auth → sender/recipient → data. Текст ошибки не содержит секретов.
"""
from __future__ import annotations

import email.utils
import logging
import re
import smtplib
import socket
import ssl
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MailProfile, utcnow
from .ca_bundle import CaBundleService
from .settings import SettingsError, SettingsService

log = logging.getLogger("app.mail")

_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}$|^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]{1,63}$")
SECURITY = ("none", "starttls", "ssl")
DEFAULT_PORTS = {"none": 25, "starttls": 587, "ssl": 465}


def valid_email(addr: str) -> bool:
    addr = (addr or "").strip()
    return 3 <= len(addr) <= 320 and "\n" not in addr and "\r" not in addr and bool(EMAIL_RE.match(addr))


class MailError(Exception):
    """Ошибка отправки: stage — этап, transient — стоит ли повторять."""

    def __init__(self, stage: str, message: str, transient: bool = False):
        super().__init__(message)
        self.stage, self.message, self.transient = stage, message, transient

    def short(self) -> str:
        return f"{self.stage}: {self.message}"[:600]


@dataclass
class SmtpConfig:
    host: str
    port: int
    security: str
    auth_type: str
    username: str
    password: str = field(default="", repr=False)
    from_address: str = ""
    from_name: str = ""
    timeout: int = 15
    verify_cert: bool = True
    ca_file: str = ""


def classify(exc: Exception, cfg: SmtpConfig) -> MailError:
    """Понятная причина по типу исключения smtplib/ssl/socket. Секреты в сообщение не попадают."""
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, socket.gaierror):
        return MailError("dns", f"имя «{cfg.host}» не найдено в DNS. Проверьте адрес сервера почты и DNS на сервере приложения.", True)
    if isinstance(exc, ssl.SSLCertVerificationError):
        reason = (getattr(exc, "verify_message", "") or str(exc)).lower()
        hint = ("имя сервера не совпадает с сертификатом" if "hostname" in reason or "match" in reason else "сертификат просрочен" if "expired" in reason
                else "цепочка не доверена — загрузите корневой сертификат CA в разделе «Сертификаты (CA)»")
        return MailError("certificate", f"проверка сертификата сервера почты не пройдена: {hint}.")
    if isinstance(exc, ssl.SSLError):
        return MailError("tls", f"не удалось установить защищённое соединение ({type(exc).__name__}). Проверьте режим защиты и порт: SSL/TLS — обычно 465, STARTTLS — 587 или 25.")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError("auth", f"сервер отклонил логин или пароль (код {exc.smtp_code}).")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailError("tls" if "starttls" in str(exc).lower() else "auth", f"сервер не поддерживает требуемую возможность: {str(exc)[:120]}.")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return MailError("sender_rejected", f"адрес отправителя отклонён сервером (код {exc.smtp_code}). Разрешите этому узлу отправку от {cfg.from_address}.", exc.smtp_code < 500)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = sorted({v[0] for v in exc.recipients.values()})
        return MailError("recipient_rejected", f"адрес получателя отклонён сервером (код {codes[0] if codes else '?'}). Проверьте адрес и правила relay.", bool(codes) and max(codes) < 500)
    if isinstance(exc, smtplib.SMTPDataError):
        return MailError("data", f"сервер не принял письмо (код {exc.smtp_code}).", exc.smtp_code < 500)
    if isinstance(exc, smtplib.SMTPResponseException):
        return MailError("smtp", f"ответ сервера {exc.smtp_code}.", exc.smtp_code < 500)
    if isinstance(exc, (smtplib.SMTPServerDisconnected, ConnectionError)):
        return MailError("connect", "сервер закрыл соединение. Проверьте режим защиты (SSL/TLS и STARTTLS путают чаще всего) и порт.", True)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return MailError("connect", f"сервер {cfg.host}:{cfg.port} не ответил за {cfg.timeout} с. Проверьте порт и межсетевой экран.", True)
    if isinstance(exc, OSError):
        return MailError("connect", f"нет соединения с {cfg.host}:{cfg.port} ({exc.strerror or type(exc).__name__}). Проверьте порт и межсетевой экран.", True)
    return MailError("smtp", f"{type(exc).__name__}", True)


def _context(cfg: SmtpConfig) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if cfg.ca_file:
        try:
            ctx.load_verify_locations(cafile=cfg.ca_file)
        except (OSError, ssl.SSLError):
            pass
    if not cfg.verify_cert:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _open(cfg: SmtpConfig, steps: list[dict] | None = None) -> smtplib.SMTP:
    """Соединение, защита и вход. `steps` — для пошаговой проверки."""
    def mark(stage: str, ok: bool, message: str):
        if steps is not None:
            steps.append({"stage": stage, "ok": ok, "message": message})

    try:
        socket.getaddrinfo(cfg.host, cfg.port, type=socket.SOCK_STREAM)
        mark("dns", True, f"Имя «{cfg.host}» найдено.")
    except OSError as exc:
        raise classify(exc, cfg) from None
    ctx = _context(cfg)
    try:
        server: smtplib.SMTP = (smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=ctx) if cfg.security == "ssl"
                                else smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout))
        mark("connect", True, f"Соединение с {cfg.host}:{cfg.port} установлено.")
        if cfg.security == "ssl":
            mark("tls", True, "SSL/TLS: сертификат сервера проверен." if cfg.verify_cert else "SSL/TLS: проверка сертификата отключена в настройках профиля.")
        server.ehlo()
        if cfg.security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
            mark("tls", True, "STARTTLS: сертификат сервера проверен." if cfg.verify_cert else "STARTTLS: проверка сертификата отключена в настройках профиля.")
        if cfg.auth_type == "login":
            server.login(cfg.username, cfg.password)
            mark("auth", True, "Вход выполнен.")
        else:
            mark("auth", None, "Без аутентификации (relay по адресу).")  # type: ignore[arg-type]
        return server
    except Exception as exc:  # noqa: BLE001
        err = classify(exc, cfg)
        mark(err.stage, False, err.message)
        raise err from None


def check_connection(cfg: SmtpConfig) -> dict:
    steps: list[dict] = []
    try:
        s = _open(cfg, steps)
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "stages": steps}
    except MailError as err:
        return {"ok": False, "stages": steps, "stage": err.stage, "message": err.message}


def build_message(cfg: SmtpConfig, to: str, subject: str, text: str, attachments: list[tuple[str, str, bytes]] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((cfg.from_name, cfg.from_address)) if cfg.from_name else cfg.from_address
    msg["To"] = to
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:300]
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=(cfg.from_address.rsplit("@", 1)[-1] or "localhost"))
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    for name, mime, data in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=name)
    return msg


def send(cfg: SmtpConfig, msg: EmailMessage) -> None:
    """Блокирующая отправка (вызывать в потоке)."""
    server = _open(cfg)
    try:
        server.send_message(msg, from_addr=cfg.from_address, to_addrs=[msg["To"]])
    except Exception as exc:  # noqa: BLE001
        raise classify(exc, cfg) from None
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


class MailService:
    def __init__(self, svc: SettingsService, ca: CaBundleService):
        self._svc = svc
        self._ca = ca

    # ------------------------------------------------------------------------- секрет
    def _aad(self, pid) -> bytes:
        return f"mail_profile.{pid}".encode()

    def _encrypt(self, pid, secret: str) -> str:
        if not secret:
            return ""
        if self._svc.box is None:
            raise SettingsError("APP_MASTER_KEY не задан — пароль сохранить нельзя")
        return self._svc.box.encrypt(secret, aad=self._aad(pid))

    def _decrypt(self, r: MailProfile) -> str:
        if not r.secret_enc or self._svc.box is None:
            return ""
        try:
            return self._svc.box.decrypt(r.secret_enc, aad=self._aad(r.id))
        except Exception:  # noqa: BLE001
            return ""

    def config(self, r: MailProfile) -> SmtpConfig:
        return SmtpConfig(host=r.host, port=r.port, security=r.security, auth_type=r.auth_type, username=r.username, password=self._decrypt(r),
                          from_address=r.from_address, from_name=r.from_name, timeout=r.timeout_s, verify_cert=r.verify_cert, ca_file=self._ca.current_path())

    # ---------------------------------------------------------------------- проверка
    @staticmethod
    def _clean(data: dict, *, current: MailProfile | None, partial: bool) -> dict:
        g = lambda k, d=None: data[k] if k in data else (getattr(current, k) if current is not None else d)  # noqa: E731
        out: dict = {}
        if not partial or "name" in data:
            name = str(g("name", "")).strip()
            if not name or len(name) > 120:
                raise SettingsError("Название профиля: 1–120 символов")
            out["name"] = name
        if not partial or "host" in data:
            host = re.sub(r"^smtps?://", "", str(g("host", "")).strip(), flags=re.I).split("/")[0]
            if not _HOST.match(host):
                raise SettingsError("Сервер: имя или IP без схемы и слэшей (например, smtp.example.local)")
            out["host"] = host
        if not partial or "security" in data:
            sec = str(g("security", "starttls"))
            if sec not in SECURITY:
                raise SettingsError("Защита соединения: none, starttls или ssl")
            out["security"] = sec
        if not partial or "port" in data or "security" in data:
            sec = out.get("security", g("security", "starttls"))
            try:
                port = int(g("port", None) or DEFAULT_PORTS[sec])
            except (TypeError, ValueError):
                raise SettingsError("Порт: число от 1 до 65535") from None
            if not 1 <= port <= 65535:
                raise SettingsError("Порт: число от 1 до 65535")
            out["port"] = port
        if not partial or "auth_type" in data:
            at = str(g("auth_type", "none"))
            if at not in ("none", "login"):
                raise SettingsError("Аутентификация: none или login")
            out["auth_type"] = at
        if not partial or "username" in data:
            out["username"] = str(g("username", "") or "").strip()[:320]
        if not partial or "from_address" in data:
            fa = str(g("from_address", "")).strip()
            if not valid_email(fa):
                raise SettingsError("Адрес отправителя: корректный email (например, noreply@example.local)")
            out["from_address"] = fa
        if not partial or "from_name" in data:
            fn = str(g("from_name", "") or "").strip()
            if len(fn) > 200 or re.search(r"[\r\n<>]", fn):
                raise SettingsError("Имя отправителя: до 200 символов, без переводов строки и <>")
            out["from_name"] = fn
        if not partial or "timeout_s" in data:
            try:
                t = int(g("timeout_s", 15))
            except (TypeError, ValueError):
                raise SettingsError("Таймаут: число секунд от 3 до 120") from None
            if not 3 <= t <= 120:
                raise SettingsError("Таймаут: от 3 до 120 секунд")
            out["timeout_s"] = t
        if not partial or "verify_cert" in data:
            out["verify_cert"] = bool(g("verify_cert", True))
        at_final = out.get("auth_type", g("auth_type", "none"))
        if at_final == "login" and not out.get("username", g("username", "")):
            raise SettingsError("Для входа по логину укажите имя пользователя")
        return out

    def _public(self, r: MailProfile) -> dict:
        return {"id": str(r.id), "name": r.name, "host": r.host, "port": r.port, "security": r.security, "auth_type": r.auth_type, "username": r.username,
                "secret_set": bool(r.secret_enc), "from_address": r.from_address, "from_name": r.from_name, "timeout_s": r.timeout_s,
                "verify_cert": r.verify_cert, "is_active": r.is_active}

    # ------------------------------------------------------------------------- CRUD
    async def list(self, db: AsyncSession) -> list[dict]:
        rows = (await db.execute(select(MailProfile).order_by(MailProfile.name))).scalars().all()
        return [self._public(r) for r in rows]

    async def row(self, db: AsyncSession, pid: str) -> MailProfile:
        try:
            r = await db.get(MailProfile, uuid.UUID(str(pid)))
        except ValueError:
            r = None
        if r is None:
            raise SettingsError("Профиль почты не найден")
        return r

    async def active(self, db: AsyncSession) -> MailProfile | None:
        return (await db.execute(select(MailProfile).where(MailProfile.is_active.is_(True)))).scalars().first()

    async def create(self, db: AsyncSession, data: dict, secret: str) -> dict:
        clean = self._clean(data, current=None, partial=False)
        if clean["auth_type"] == "login" and not secret:
            raise SettingsError("Для входа по логину укажите пароль")
        if (await db.execute(select(MailProfile.id).where(MailProfile.name == clean["name"]))).first():
            raise SettingsError("Профиль с таким названием уже есть")
        pid = uuid.uuid4()
        first = await self.active(db) is None
        r = MailProfile(id=pid, secret_enc=self._encrypt(pid, secret) if clean["auth_type"] == "login" else "", is_active=first, **clean)
        db.add(r)
        await db.flush()
        return self._public(r)

    async def update(self, db: AsyncSession, pid: str, data: dict, secret: str | None) -> dict:
        r = await self.row(db, pid)
        clean = self._clean(data, current=r, partial=True)
        if "name" in clean and clean["name"] != r.name and (await db.execute(select(MailProfile.id).where(MailProfile.name == clean["name"]))).first():
            raise SettingsError("Профиль с таким названием уже есть")
        for k, v in clean.items():
            setattr(r, k, v)
        if r.auth_type == "none":
            r.secret_enc = ""
        elif secret is not None:
            if secret == "":
                raise SettingsError("Пароль нельзя оставить пустым. Чтобы не менять пароль, не заполняйте поле; чтобы удалить — выберите «без аутентификации»")
            r.secret_enc = self._encrypt(r.id, secret)
        elif not r.secret_enc:
            raise SettingsError("Для входа по логину укажите пароль")
        r.updated_at = utcnow()
        await db.flush()
        return self._public(r)

    async def delete(self, db: AsyncSession, pid: str) -> MailProfile:
        r = await self.row(db, pid)
        await db.delete(r)
        await db.flush()
        if r.is_active:   # активным становится любой оставшийся профиль, чтобы почта не отключилась молча
            nxt = (await db.execute(select(MailProfile).order_by(MailProfile.created_at))).scalars().first()
            if nxt is not None:
                nxt.is_active = True
        return r

    async def activate(self, db: AsyncSession, pid: str) -> dict:
        r = await self.row(db, pid)
        await db.execute(update(MailProfile).values(is_active=False))
        r.is_active = True
        await db.flush()
        return self._public(r)
