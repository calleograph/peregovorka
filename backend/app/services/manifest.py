"""Паспорт встречи рядом с её материалами (`manifest.json`): по нему администратор после переноса хранилища находит встречу, комнату, участников и права и проверяет, что файлы целы.

Файл — машиночитаемый и **подписанный**: подпись HMAC-SHA256 ключом, выведенным из APP_MASTER_KEY (HKDF). Каталог с материалами можно изменить руками, поэтому простой JSON
доказательством прав не считается: без верной подписи (ключа нет или он другой) восстановление относится к «предполагаемым» данным и права не выдаёт.
В паспорте — идентификаторы (встречи, комнаты, сотрудников, GUID из каталога), контрольные суммы и сроки хранения; паролей, токенов, ключей, текстов и ФИО нет.

Паспорт пишется в каждую папку, где у встречи есть файлы (Audio, Transcripts, Protocols, Chat, Boards): сканер находит его в любой из них, а в паспорт входят только файлы своей папки.
Обновляется в фоне после завершения встречи, формирования протокола и выгрузки записей (отметка `manifest:dirty` в Redis; одну встречу обрабатывает один процесс за раз).
Ошибка записи паспорта никогда не отменяет встречу, протокол или выгрузку.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import hmac
import json
import logging
import posixpath
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, MeetingGrant, MeetingParticipant, Protocol, Recording, Room, RoomAcl, RoomModerator, User, utcnow
from .storage import LocalStorage, StorageBackend, StorageError, StorageNotFound

log = logging.getLogger("app.manifest")
FORMAT = "peregovorka-meeting-manifest"
VERSION = 1
FILENAME = "manifest.json"
DIRTY = "manifest:dirty"
PER_TICK = 3                                  # встреч за один проход фонового цикла (раз в несколько секунд): нагрузка на диск и сеть малая и равномерная
CHUNK = 1 << 20

# имя файла в папке встречи → (вид материала, формат)
KNOWN: dict[str, tuple[str, str]] = {
    "protocol.txt": ("transcript", "txt"), "official-protocol.md": ("protocol", "md"), "summary.md": ("summary", "md"),
    "chat.txt": ("chat", "txt"), "whiteboard.drawio": ("board", "drawio"), "Общая запись встречи.m4a": ("audio_mix", "m4a"),
}
FOLDERS = ("Audio", "Video", "Transcripts", "Protocols", "Chat", "Boards")


def classify(name: str) -> tuple[str, str] | None:
    """Вид и формат материала по имени файла; чужие файлы (в том числе сам паспорт и временные файлы записи) — None."""
    if name in KNOWN:
        return KNOWN[name]
    low = name.lower()
    if low.endswith(".wav") and not low.endswith(".tmp"):
        return "audio_participant", "wav"
    if re.fullmatch(r"video-\d{1,4}\.mp4", low):
        return "video_mix", "mp4"
    return None


# ------------------------------------------------------------------------------------------------ ключи и подпись
def _key(master_b64: str, info: bytes) -> bytes | None:
    """HKDF-SHA256 из APP_MASTER_KEY; None — ключа нет (подпись невозможна)."""
    try:
        master = base64.b64decode(master_b64, validate=True)
    except (ValueError, TypeError):
        return None
    if len(master) < 16:
        return None
    prk = hmac.new(b"peregovorka-manifest-v1", master, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def instance_id(master_b64: str) -> str:
    """Постоянный идентификатор этой установки: одинаков, пока тот же APP_MASTER_KEY (его и так сохраняют в резервной копии). Ключ по нему не восстановить."""
    k = _key(master_b64, b"instance-id")
    return base64.b32encode(k[:10]).decode().lower() if k else ""


def canonical(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(doc: dict, master_b64: str) -> dict:
    k = _key(master_b64, b"manifest-signing")
    out = {k2: v for k2, v in doc.items() if k2 != "signature"}
    out["signature"] = {"alg": "HMAC-SHA256", "value": hmac.new(k, canonical(out), hashlib.sha256).hexdigest()} if k else {"alg": "none", "value": ""}
    return out


def verify(doc: Any, master_b64: str) -> tuple[str, str]:
    """(состояние, пояснение): `valid` — подпись верна ключом этой установки; `unsigned` — подписи нет; `foreign` — подпись есть, но ключ другой (чужая установка или ключ утрачен);
    `invalid` — это не паспорт или содержимое изменено после подписи (подпись не сходится при том же формате)."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("version"), int):
        return "invalid", "Файл не является паспортом встречи Peregovorka."
    sig = doc.get("signature") or {}
    if sig.get("alg") != "HMAC-SHA256" or not sig.get("value"):
        return "unsigned", "Паспорт без подписи: содержимое не подтверждено."
    k = _key(master_b64, b"manifest-signing")
    if k is None:
        return "foreign", "На этом сервере нет ключа шифрования, подпись проверить нельзя."
    good = hmac.compare_digest(hmac.new(k, canonical(doc), hashlib.sha256).hexdigest(), str(sig["value"]))
    if good:
        return "valid", "Подпись верна."
    if doc.get("instance") and doc.get("instance") != instance_id(master_b64):
        return "foreign", "Паспорт подписан ключом другой установки."
    return "invalid", "Подпись не сходится: паспорт изменён после создания."


def iso(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


# ------------------------------------------------------------------------------------------------ хэш файла
def hash_file(storage: StorageBackend, rel: str, *, throttle: float = 0.0) -> tuple[int, str]:
    """Размер и SHA-256 потоком (файл целиком в память не читается). Блокирующая: запускать в потоке."""
    import time  # noqa: PLC0415

    size = storage.size_of(rel)
    h = hashlib.sha256()
    if size:
        for chunk in storage.read_range(rel, 0, size - 1):
            h.update(chunk)
            if throttle:
                time.sleep(throttle)
    return size, h.hexdigest()


# ------------------------------------------------------------------------------------------------ сборка паспорта
async def meeting_context(db: AsyncSession, meeting: Meeting) -> dict:
    room = meeting.room
    acl = [{"type": a.subject_type, "ref": (a.subject_ref or "").lower()} for a in (await db.execute(select(RoomAcl).where(RoomAcl.room_id == room.id))).scalars()]
    mods = [{"type": m.subject_type, "ref": (m.subject_ref or "").lower()} for m in (await db.execute(select(RoomModerator).where(RoomModerator.room_id == room.id))).scalars()]
    rows = (await db.execute(select(MeetingParticipant.user_id, User.ad_guid, User.auth_source, MeetingParticipant.joined_at, MeetingParticipant.left_at)
                             .join(User, User.id == MeetingParticipant.user_id).where(MeetingParticipant.meeting_id == meeting.id).order_by(MeetingParticipant.joined_at))).all()
    seen: set = set()
    people = []
    for uid, guid, src, j, left in rows:                      # один человек мог входить несколько раз
        if uid in seen:
            continue
        seen.add(uid)
        people.append({"user_id": str(uid), "ad_guid": (guid or "").lower() if src == "ad" else None, "joined_at": iso(j), "left_at": iso(left)})
    grants = [{"user_id": str(uid), "ad_guid": (guid or "").lower() if src == "ad" else None}
              for uid, guid, src in (await db.execute(select(MeetingGrant.user_id, User.ad_guid, User.auth_source).join(User, User.id == MeetingGrant.user_id)
                                                       .where(MeetingGrant.meeting_id == meeting.id))).all()]
    return {
        "meeting": {"id": str(meeting.id), "started_at": iso(meeting.started_at), "ended_at": iso(meeting.ended_at), "end_reason": meeting.end_reason,
                    "transcription": bool(meeting.transcription_enabled), "record_audio": bool(meeting.record_audio)},
        "room": {"id": str(room.id), "slug": room.slug, "name": room.name, "lifetime": room.lifetime, "history_access": room.history_access},
        "access": {"acl": acl, "moderators": mods, "grants": grants},
        "participants": people,
        "retention": {"text_days": room.text_retention_days, "audio_days": room.audio_retention_days},
    }


def build(ctx: dict, *, instance: str, folder: str, directory: str, files: list[dict], now: datetime) -> dict:
    return {"format": FORMAT, "version": VERSION, "instance": instance, "created_at": iso(now), "folder": folder, "dir": directory, **ctx, "files": files}


class ManifestService:
    """Фоновая запись паспортов. `files` — FileStore (профили хранилищ), `recordings_path` — локальный каталог записей."""

    def __init__(self, files, recordings_path: str, master_key: str, redis, journal=None):
        self._files, self._rec_path, self._key, self._redis, self.journal = files, recordings_path, master_key, redis, journal

    async def mark(self, meeting_id) -> None:
        try:
            await self._redis.sadd(DIRTY, str(meeting_id))
        except Exception:  # noqa: BLE001 — паспорт не должен мешать основному действию
            log.warning("Не удалось отметить встречу для паспорта", extra={"meeting_id": str(meeting_id)})

    async def tick(self, db: AsyncSession) -> int:
        """Один проход фонового цикла: несколько отмеченных встреч. Встреча, которую не удалось обработать, возвращается в очередь не сразу (при следующей отметке)."""
        done = 0
        for _ in range(PER_TICK):
            mid = await self._redis.spop(DIRTY)
            if not mid:
                break
            try:
                await self.refresh(db, uuid.UUID(mid if isinstance(mid, str) else mid.decode()))
                done += 1
            except Exception:  # noqa: BLE001
                log.exception("Не удалось записать паспорт встречи", extra={"meeting_id": str(mid)})
        return done

    async def _targets(self, db: AsyncSession, meeting: Meeting) -> list[tuple[str, str, StorageBackend, str]]:
        """Куда писать паспорта: (папка, каталог встречи внутри папки, хранилище, группа настроек). При прежней «плоской» раскладке без профиля все папки — один каталог:
        паспорт для него пишется один раз."""
        out: list[tuple[str, str, StorageBackend, str]] = []
        prots = (await db.execute(select(Protocol).where(Protocol.meeting_id == meeting.id))).scalars().all()
        dirs = {p.meta.get("dir") for p in prots if p.meta and p.meta.get("dir")}
        for folder, group, legacy in (("Transcripts", "storage", ""), ("Protocols", "storage", ""), ("Chat", "storage", ""), ("Boards", "storage", "")):
            try:
                st = await self._files.backend(db, group, folder, legacy_prefix=legacy)
            except StorageError:
                st = None
            if st is None:
                continue
            for d in sorted(dirs):
                out.append((folder, d, st, group))
        recs = (await db.execute(select(Recording).where(Recording.meeting_id == meeting.id))).scalars().all()
        adirs = {posixpath.dirname(r.path) for r in recs if r.path and posixpath.dirname(r.path) and not (r.kind == "mix_video" and r.export_area != "audio")}
        vdirs = {posixpath.dirname(r.path) for r in recs if r.path and posixpath.dirname(r.path) and r.kind == "mix_video" and r.export_area != "audio"}
        local_done: set[str] = set()
        for folder, dirs, getter, group in (("Audio", adirs, lambda: self._files.backend(db, "audio_storage", "Audio", legacy_prefix="audio"), "audio_storage"),
                                            ("Video", vdirs, lambda: self._files.video_backend(db), "video")):
            if not dirs:
                continue
            try:
                ast = await getter()
            except StorageError:
                ast = None
            for d in sorted(dirs):
                if ast is not None:
                    out.append((folder, d, ast, group))
                if d not in local_done and Path(self._rec_path, d).is_dir():   # локальная копия (если её хранят): паспорт и рядом с ней
                    local_done.add(d)
                    out.append((folder, d, LocalStorage(self._rec_path), "local"))
        return out

    async def refresh(self, db: AsyncSession, meeting_id: uuid.UUID) -> dict:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.ended_at is None:
            return {"written": 0, "reason": "встреча не найдена или ещё идёт"}
        ctx = await meeting_context(db, meeting)
        targets = await self._targets(db, meeting)
        from ..db import release  # noqa: PLC0415

        await release(db)                                     # файловые операции идут без транзакции PostgreSQL
        inst = instance_id(self._key)
        written = 0
        seen: set = set()
        for folder, directory, storage, group in targets:
            try:
                n = await asyncio.to_thread(self._write_one, storage, folder, directory, ctx, inst, group, seen)
                written += n
            except StorageError as exc:
                log.warning("Паспорт встречи не записан", extra={"meeting_id": str(meeting_id), "folder": folder, "error": str(exc)[:200]})
                if self.journal is not None:
                    self.journal.emit("storage", "manifest_failed", level="warn", meeting_id=str(meeting_id), message=str(exc)[:300], data={"folder": folder})
        return {"written": written}

    def _write_one(self, storage: StorageBackend, folder: str, directory: str, ctx: dict, inst: str, group: str, seen: set) -> int:
        names = [n for n in storage.list_dir(directory) if classify(n)]
        key = (group, directory, tuple(sorted(names)))
        if not names or key in seen:
            return 0
        seen.add(key)
        prev: dict[str, dict] = {}
        try:
            old = json.loads(storage.read_bytes(f"{directory}/{FILENAME}").decode("utf-8"))
            if verify(old, self._key)[0] == "valid":
                prev = {f["name"]: f for f in old.get("files", []) if isinstance(f, dict) and "name" in f}
        except (StorageNotFound, StorageError, ValueError, UnicodeDecodeError):
            prev = {}
        files = []
        for name in names:
            kind, fmt = classify(name)  # type: ignore[misc]
            size = storage.size_of(f"{directory}/{name}")
            old_f = prev.get(name)
            if old_f and old_f.get("size") == size and old_f.get("sha256"):
                digest = old_f["sha256"]                          # тот же размер, что в подписанном паспорте: сумму не пересчитываем (нагрузка на диск)
            else:
                size, digest = hash_file(storage, f"{directory}/{name}", throttle=0.002)
            files.append({"name": name, "kind": kind, "format": fmt, "size": size, "sha256": digest})
        doc = sign(build(ctx, instance=inst, folder=folder, directory=directory, files=files, now=utcnow()), self._key)
        data = (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
        storage.write_bytes(f"{directory}/{FILENAME}", data)
        return 1
