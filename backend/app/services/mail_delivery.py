"""Доставка материалов встречи по почте: что отправлять, кому, очередь и сборка письма.

Настройки доставки — на уровне комнаты (`rooms.mail_delivery`), их выбирает руководитель: какие материалы (протокол, стенограмма, резюме — реестр `MATERIALS`
расширяем) и кому (руководители, участники встречи, конкретные люди, свои адреса). SMTP-реквизиты и глобальные правила (разрешённые домены, предел вложений,
число повторов) — только у администратора; руководитель их не видит и не меняет.

Письма ставятся в очередь (`mail_messages`) и уходят фоновым работником с повторами — не внутри HTTP-запроса. В очереди нет ни текстов, ни документов: они
собираются в момент отправки из данных встречи; в журнал попадают только время, комната/встреча, получатель, результат и последняя ошибка.
Крупные материалы не вкладываются: в письме — ссылка на встречу в приложении (открывается после входа, доступ проверяется как обычно).
"""
from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import Meeting, MailMessage, Protocol, User, utcnow
from .export_docs import md_to_plain, to_docx, to_pdf
from .mail import MailError, MailService, build_message, send, valid_email
from .settings import MailPolicySettings, SettingsService
from .storage import safe_component

log = logging.getLogger("app.maildelivery")

FORMAT_MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf",
               "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8"}
MAX_MANUAL_EMAILS = 50
MAX_RECIPIENTS = 300


@dataclass
class Material:
    kind: str
    label: str
    title: str
    md: str


@dataclass
class MaterialDef:
    label: str
    describe: str
    load: Callable[["DeliveryService", AsyncSession, Meeting], Awaitable[Material | None]]


# ------------------------------------------------------------------------------------------------ реестр материалов
async def _protocol_like(svc: "DeliveryService", db: AsyncSession, meeting: Meeting, kind: str, label: str, default_title: str) -> Material | None:
    p = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting.id, Protocol.kind == kind, Protocol.status == "ready",
                                                 Protocol.content.is_not(None)).order_by(Protocol.updated_at.desc()))).scalars().first()
    if p is None or not (p.content or "").strip():
        return None
    return Material(kind, label, p.title or f"{default_title}: {meeting.room.name}", p.content)


async def _protocol(svc, db, meeting):
    return await _protocol_like(svc, db, meeting, "protocol", "Протокол", "Протокол совещания")


async def _summary(svc, db, meeting):
    return await _protocol_like(svc, db, meeting, "summary", "Резюме встречи", "Краткое резюме")


async def _transcript(svc, db, meeting):
    ps = svc.protocols
    text = await ps.transcript_text(db, meeting, await ps._tz(db))   # noqa: SLF001
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 5:      # одна шапка без реплик — отправлять нечего
        return None
    return Material("transcript", "Стенограмма", f"Стенограмма: {meeting.room.name}", "\n\n".join(lines))


MATERIALS: dict[str, MaterialDef] = {
    "protocol": MaterialDef("Протокол", "Официальный протокол совещания (формируется языковой моделью)", _protocol),
    "summary": MaterialDef("Резюме встречи", "Краткое резюме: суть, решения и поручения", _summary),
    "transcript": MaterialDef("Стенограмма", "Полный текст реплик участников с временем", _transcript),
}


def effective_delivery(meeting: Meeting) -> dict | None:
    """Настройки рассылки для встречи: свои (если руководитель их менял) или настройки комнаты по умолчанию."""
    return meeting.delivery_override if meeting.delivery_override else meeting.room.mail_delivery


def clean_spec(raw: dict | None) -> dict:
    """Приводит настройки доставки комнаты к безопасному виду; неверные значения — ValueError с текстом для пользователя."""
    raw = raw or {}
    spec = {"enabled": bool(raw.get("enabled")), "archive": bool(raw.get("archive")), "materials": [],
            "recipients": {"leaders": False, "participants": False, "users": [], "emails": []}}
    for k in raw.get("materials") or []:
        if k not in MATERIALS:
            raise ValueError(f"Неизвестный материал: {k}")
        if k not in spec["materials"]:
            spec["materials"].append(k)
    r = raw.get("recipients") or {}
    spec["recipients"]["leaders"] = bool(r.get("leaders"))
    spec["recipients"]["participants"] = bool(r.get("participants"))
    seen: set[str] = set()
    for u in r.get("users") or []:
        ref = str((u or {}).get("ref") or "").strip()
        if not ref or len(ref) > 64 or ref.lower() in seen:
            continue
        seen.add(ref.lower())
        em = str((u or {}).get("email") or "").strip()
        spec["recipients"]["users"].append({"ref": ref, "name": str((u or {}).get("name") or "")[:300], "email": em if valid_email(em) else ""})
    if len(spec["recipients"]["users"]) > 100:
        raise ValueError("Выбранных людей не больше 100 — для большего числа используйте участников встречи или группу руководителей")
    emails: list[str] = []
    for e in r.get("emails") or []:
        e = str(e).strip()
        if not e:
            continue
        if not valid_email(e):
            raise ValueError(f"Некорректный адрес электронной почты: {e[:80]}")
        if e.lower() not in [x.lower() for x in emails]:
            emails.append(e)
    if len(emails) > MAX_MANUAL_EMAILS:
        raise ValueError(f"Свои адреса: не больше {MAX_MANUAL_EMAILS}")
    spec["recipients"]["emails"] = emails
    if spec["enabled"] and not spec["materials"]:
        raise ValueError("Выберите хотя бы один материал для отправки")
    return spec


@dataclass
class Recipient:
    email: str
    name: str
    source: str            # leader | participant | user | manual
    problem: str | None = None   # no_email | invalid_email | domain_not_allowed

    def public(self) -> dict:
        return {"email": self.email, "name": self.name, "source": self.source, "problem": self.problem}


PROBLEM_TEXT = {"no_email": "в каталоге не указан адрес электронной почты", "invalid_email": "адрес в каталоге некорректен",
                "domain_not_allowed": "домен адреса не разрешён политикой отправки"}


class DeliveryService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], settings_svc: SettingsService, mail: MailService, protocols, directory, public_url: str, journal=None):
        self._sm = sm
        self._svc = settings_svc
        self.mail = mail
        self.protocols = protocols
        self.directory = directory
        self._public_url = public_url.rstrip("/")
        self.journal = journal

    async def policy(self, db: AsyncSession) -> MailPolicySettings:
        return await self._svc.get(db, "mail_policy")  # type: ignore[return-value]

    # ------------------------------------------------------------------------------------------- получатели
    async def _group_members(self, dn: str) -> list[dict]:
        fn = getattr(self.directory, "members", None)
        if fn is None:
            return []
        try:
            return await asyncio.to_thread(fn, dn, 300)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось получить состав группы руководителей", extra={"error": getattr(exc, "code", type(exc).__name__)})
            return []

    async def resolve(self, db: AsyncSession, room, meeting: Meeting | None, spec: dict) -> list[Recipient]:
        out: dict[str, Recipient] = {}
        pol = await self.policy(db)
        domains = pol.domains()

        def add(email: str | None, name: str, source: str) -> None:
            email = (email or "").strip()
            if not email:
                out[f"!{source}:{name}"] = Recipient("", name, source, "no_email")
                return
            key = email.lower()
            if key in out:
                return
            problem = None
            if not valid_email(email):
                problem = "invalid_email"
            elif domains and email.rsplit("@", 1)[-1].lower() not in domains:
                problem = "domain_not_allowed"
            out[key] = Recipient(email, name, source, problem)

        r = spec["recipients"]
        if r["leaders"]:
            for m in room.moderators:
                if m.subject_type == "user":
                    u = (await db.execute(select(User).where(func.lower(User.ad_guid) == m.subject_ref.lower()))).scalar_one_or_none()
                    add(u.email if u else None, (u.display_name if u else None) or m.display_name or m.subject_ref, "leader")
                else:
                    members = await self._group_members(m.subject_ref)
                    for mem in members:
                        add(mem.get("email"), mem.get("name") or mem.get("sam") or "", "leader")
        if r["participants"] and meeting is not None:
            seen: set[uuid.UUID] = set()
            for p in sorted(meeting.participants, key=lambda x: x.joined_at):
                if p.user_id in seen or p.user is None:
                    continue
                seen.add(p.user_id)
                add(p.user.email, p.user.display_name, "participant")
        for u in r["users"]:
            row = (await db.execute(select(User).where(func.lower(User.ad_guid) == u["ref"].lower()))).scalar_one_or_none()
            add((row.email if row and row.email else None) or u.get("email"), (row.display_name if row else None) or u.get("name") or u["ref"], "user")
        for e in r["emails"]:
            add(e, e, "manual")
        return list(out.values())[: MAX_RECIPIENTS + 50]

    # ------------------------------------------------------------------------------------------------ план
    async def plan(self, db: AsyncSession, meeting: Meeting, spec: dict, kinds: list[str] | None = None) -> dict:
        kinds = kinds or spec["materials"]
        mats = []
        for k in kinds:
            d = MATERIALS.get(k)
            if d is None:
                continue
            m = await d.load(self, db, meeting)
            mats.append({"kind": k, "label": d.label, "available": m is not None,
                         "reason": None if m else ("ещё не сформирован — создайте его на странице встречи" if k in ("protocol", "summary") else "в этой встрече нет реплик")})
        rec = await self.resolve(db, meeting.room, meeting, spec)
        prof = await self.mail.active(db)
        pol = await self.policy(db)
        return {"materials": mats, "recipients": [r.public() for r in rec], "mail_configured": prof is not None,
                "allowed_domains": pol.domains(), "max_attachment_mb": pol.max_attachment_mb, "attach_format": pol.attach_format}

    # ----------------------------------------------------------------------------------------------- очередь
    def _subject(self, pol: MailPolicySettings, meeting: Meeting, tz) -> str:
        when = meeting.started_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        return f"{pol.subject_prefix + ' ' if pol.subject_prefix else ''}Материалы встречи «{meeting.room.name}» от {when}"[:300]

    async def enqueue(self, db: AsyncSession, meeting: Meeting, kinds: list[str], recipients: list[Recipient], *, trigger: str, by: str, archive: bool = False) -> dict:
        pol = await self.policy(db)
        tz = await self.protocols._tz(db)   # noqa: SLF001
        batch = uuid.uuid4()
        queued = 0
        skipped: list[dict] = []
        for r in recipients:
            if r.problem:
                skipped.append({"name": r.name, "email": r.email, "reason": PROBLEM_TEXT.get(r.problem, r.problem)})
                continue
            db.add(MailMessage(batch_id=batch, meeting_id=meeting.id, room_name=meeting.room.name, recipient=r.email, recipient_name=r.name[:300],
                               subject=self._subject(pol, meeting, tz), kinds=kinds, options={"archive": True} if archive else None, trigger=trigger, requested_by=by[:300], state="queued",
                               max_attempts=pol.max_attempts, next_attempt_at=utcnow()))
            queued += 1
        await db.flush()
        return {"batch_id": str(batch), "queued": queued, "skipped": skipped}

    async def run_auto(self, meeting_id: uuid.UUID) -> None:
        """После завершения встречи: дописать недостающие протокол/резюме (если выбраны и возможно) и поставить письма в очередь. Один раз на встречу."""
        try:
            async with self._sm() as db:
                meeting = await db.get(Meeting, meeting_id)
                if meeting is None or meeting.ended_at is None:
                    return
                try:
                    spec = clean_spec(effective_delivery(meeting))      # настройки встречи (если руководитель их менял) поверх настроек комнаты
                except ValueError:
                    return
                if not spec["enabled"]:
                    return
                done = (await db.execute(select(func.count()).select_from(MailMessage).where(MailMessage.meeting_id == meeting_id, MailMessage.trigger == "auto"))).scalar_one()
                if done:
                    return
                need = [k for k in spec["materials"] if k in ("protocol", "summary") and await MATERIALS[k].load(self, db, meeting) is None]
            if need and await self.protocols.has_materials(meeting_id):
                for k in need:
                    try:
                        await self.protocols.run_protocol(await self.protocols.create_protocol_row(meeting_id, k, "auto-mail", None))
                    except Exception:  # noqa: BLE001
                        log.warning("Не удалось сформировать материал для рассылки", extra={"kind": k})
            async with self._sm() as db:
                meeting = await db.get(Meeting, meeting_id)
                plan = await self.plan(db, meeting, spec)
                have = [m["kind"] for m in plan["materials"] if m["available"]]
                recipients = [Recipient(r["email"], r["name"], r["source"], r["problem"]) for r in plan["recipients"]]
                if not have:
                    self._emit("mail_skipped", "warn", meeting, "Рассылка не выполнена: ни один из выбранных материалов не готов", {"wanted": spec["materials"]})
                    return
                res = await self.enqueue(db, meeting, have, recipients, trigger="auto", by="автоматически", archive=spec["archive"])
                for s in res["skipped"]:
                    self._emit("mail_recipient_skipped", "warn", meeting, f"{s['name'] or s['email'] or 'получатель'}: {s['reason']}", {})
                await db.commit()
                self._emit("mail_queued", "info", meeting, f"В очередь поставлено писем: {res['queued']}", {"kinds": have, "skipped": len(res["skipped"])})
        except Exception:  # noqa: BLE001
            log.exception("Ошибка автоматической рассылки материалов")

    def _emit(self, event: str, level: str, meeting: Meeting | None, message: str, data: dict) -> None:
        if self.journal is not None:
            self.journal.emit("mail", event, level=level, room=meeting.room.name if meeting else None, meeting_id=str(meeting.id) if meeting else None, message=message, data=data)

    # ----------------------------------------------------------------------------------------------- отправка
    @staticmethod
    def _zip(files: list[tuple[str, str, bytes]], name: str) -> list[tuple[str, str, bytes]]:
        """Все выбранные документы одним архивом (удобно, когда их несколько)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for fname, _mime, data in files:
                z.writestr(fname, data)
        return [(name, "application/zip", buf.getvalue())]

    async def _render_attachments(self, db: AsyncSession, meeting: Meeting, kinds: list[str], fmt: str, archive: bool = False) -> tuple[list[tuple[str, str, bytes]], list[Material]]:
        files: list[tuple[str, str, bytes]] = []
        mats: list[Material] = []
        tz = await self.protocols._tz(db)  # noqa: SLF001
        date = meeting.started_at.astimezone(tz).strftime("%Y-%m-%d")
        for k in kinds:
            m = await MATERIALS[k].load(self, db, meeting)
            if m is None:
                continue
            mats.append(m)
            data = (to_docx(m.md, m.title) if fmt == "docx" else to_pdf(m.md, m.title) if fmt == "pdf" else (md_to_plain(m.md) if fmt == "txt" else m.md).encode("utf-8"))
            files.append((f"{safe_component(m.label)} - {safe_component(meeting.room.name)} - {date}.{fmt}", FORMAT_MIME[fmt], data))
        if archive and files:
            files = self._zip(files, f"Материалы встречи - {safe_component(meeting.room.name)} - {date}.zip")
        return files, mats

    async def deliver(self, message_id: uuid.UUID) -> None:
        """Отправка одного письма из очереди (вызывает работник). Состояние и ошибка записываются в строку очереди."""
        async with self._sm() as db:
            row = await db.get(MailMessage, message_id)
            if row is None or row.state != "sending":
                return
            try:
                profile = await self.mail.active(db)
                if profile is None:
                    raise MailError("config", "нет активного профиля исходящей почты (Администрирование → Электронная почта)", True)
                cfg = self.mail.config(profile)
                meeting = await db.get(Meeting, row.meeting_id) if row.meeting_id else None
                pol = await self.policy(db)
                if meeting is None:
                    raise MailError("content", "встреча удалена — отправлять нечего")
                files, mats = await self._render_attachments(db, meeting, list(row.kinds or []), pol.attach_format, bool((row.options or {}).get("archive")))
                if not mats:
                    raise MailError("content", "выбранные материалы больше недоступны")
                total = sum(len(f[2]) for f in files)
                link = f"{self._public_url}/history/{meeting.id}"
                big = total > pol.max_attachment_mb * 1024 * 1024
                tz = await self.protocols._tz(db)  # noqa: SLF001
                body = [f"Здравствуйте{', ' + row.recipient_name if row.recipient_name and ' ' in row.recipient_name else ''}!", "",
                        f"Материалы встречи «{meeting.room.name}» от {meeting.started_at.astimezone(tz).strftime('%d.%m.%Y %H:%M')}:", ""]
                body += [f"  • {m.label}" for m in mats]
                body.append("")
                if big:
                    body += [f"Материалы большие (около {total // 1024 // 1024 or 1} МБ), поэтому не вложены в письмо. Откройте их на странице встречи (потребуется вход):", link]
                else:
                    body += ["Документы — во вложении.", f"Страница встречи (потребуется вход): {link}"]
                summary = next((m for m in mats if m.kind == "summary"), None)
                if summary and len(summary.md) <= 3000:
                    body += ["", "Краткое резюме:", "", md_to_plain(summary.md)]
                body += ["", "—", "Письмо отправлено автоматически системой Peregovorka. Отвечать на него не нужно."]
                msg = build_message(cfg, row.recipient, row.subject, "\n".join(body), None if big else files)
                await asyncio.to_thread(send, cfg, msg)
                row.state, row.sent_at, row.last_error, row.delivery = "sent", utcnow(), None, "link" if big else "attachment"
                self._emit("mail_sent", "info", meeting, f"Отправлено: {row.recipient}", {"recipient": row.recipient, "delivery": row.delivery, "trigger": row.trigger})
            except MailError as err:
                await self._fail(row, err, meeting if "meeting" in locals() else None, pol if "pol" in locals() else None)
            except Exception as exc:  # noqa: BLE001
                log.exception("Сбой подготовки письма")
                await self._fail(row, MailError("internal", f"внутренняя ошибка ({type(exc).__name__})", True), None, None)
            await db.commit()

    async def _fail(self, row: MailMessage, err: MailError, meeting: Meeting | None, pol: MailPolicySettings | None) -> None:
        row.attempts += 1
        row.last_error = err.short()
        retry_base = pol.retry_minutes if pol else 5
        if err.transient and row.attempts < row.max_attempts:
            row.state = "queued"
            row.next_attempt_at = utcnow() + timedelta(minutes=retry_base * (2 ** (row.attempts - 1)))
            self._emit("mail_retry", "warn", meeting, f"{row.recipient}: {err.short()} (повтор {row.attempts}/{row.max_attempts})", {"recipient": row.recipient, "stage": err.stage})
        else:
            row.state = "failed"
            self._emit("mail_failed", "error", meeting, f"{row.recipient}: {err.short()}", {"recipient": row.recipient, "stage": err.stage})
