"""Управление ключами шифрования хранилища и пакетные операции над уже записанными данными (см. security/storage_crypto.py).

* `init` — при запуске: вывести ключ-обёртку из APP_MASTER_KEY, при необходимости создать первый ключ данных, загрузить ключи в память процесса.
* `rotate` — новый активный ключ; прежние остаются, чтобы читать старые записи.
* `reseal` — пакетно (по комнате или по всем): зашифровать незашифрованные записи, а при `rotate_all` — переписать записи под активным ключом.
  Идёт порциями и безопасна к повторному запуску: каждая запись обновляется независимо.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Meeting, MeetingChatMessage, Room, StorageKey, TranscriptSegment, crypto_events, utcnow
from ..security import storage_crypto as sc

log = logging.getLogger("app.storage_encryption")
BATCH = 500
_kek: bytes | None = None


async def init(session_maker, master_key_b64: str | None) -> bool:
    """Загрузить ключи. Без мастер-ключа шифрование недоступно (комнаты с режимом не смогут писать, включить режим нельзя)."""
    global _kek
    sc.keyring.clear()
    try:
        _kek = sc.derive_kek(master_key_b64 or "")
    except sc.StorageCryptoError:
        _kek = None
        return False
    async with session_maker() as db:
        await _load(db)
        if not sc.keyring.ready:
            await _new_key(db)
            await _load(db)
    crypto_events.invalidate()
    return sc.keyring.ready


async def _load(db: AsyncSession) -> None:
    rows = (await db.execute(select(StorageKey).order_by(StorageKey.id))).scalars().all()
    items = []
    for r in rows:
        try:
            items.append((r.id, sc.unwrap_key(_kek, r.id, r.wrapped), bool(r.active)))
        except sc.StorageCryptoError as exc:
            log.error("Ключ хранилища не загружен", extra={"key_id": r.id, "error": str(exc)})
    sc.keyring.load(items)


async def _new_key(db: AsyncSession) -> int:
    row = StorageKey(wrapped="", active=True)
    db.add(row)
    await db.flush()                                  # нужен номер: он входит в AAD обёртки
    row.wrapped = sc.wrap_key(_kek, row.id, os.urandom(32))
    await db.execute(update(StorageKey).where(StorageKey.id != row.id, StorageKey.active.is_(True)).values(active=False, retired_at=utcnow()))
    await db.commit()
    return row.id


async def rotate(db: AsyncSession) -> int:
    if _kek is None:
        raise sc.StorageCryptoError("APP_MASTER_KEY не задан: ключи хранилища недоступны")
    kid = await _new_key(db)
    await _load(db)
    return kid


def ready() -> bool:
    return sc.keyring.ready


async def ensure_ready(app) -> bool:
    """Ключи могли не загрузиться при запуске (база ещё не была обновлена) — пробуем снова, когда они нужны. Каждый экземпляр приложения загружает ключи своей базы."""
    if not ready() or not getattr(app.state, "storage_keys_loaded", False):
        try:
            await init(app.state.session_maker, app.state.settings.app_master_key)
            app.state.storage_keys_loaded = ready()
        except Exception:  # noqa: BLE001
            log.warning("Ключи шифрования хранилища не загружены", exc_info=True)
    return ready()


def default_for_new(general_cfg) -> bool:
    """Новая комната создаётся с шифрованием хранилища, если так задано в общих настройках и ключи доступны."""
    return bool(getattr(general_cfg, "encrypt_new_rooms", False)) and ready()


async def keys(db: AsyncSession) -> list[dict]:
    return [{"id": r.id, "active": bool(r.active), "created_at": r.created_at, "retired_at": r.retired_at}
            for r in (await db.execute(select(StorageKey).order_by(StorageKey.id.desc()))).scalars().all()]


def _tables():
    return ((MeetingChatMessage, lambda r: sc.chat_aad(r.meeting_id)), (TranscriptSegment, lambda r: sc.segment_aad(r.meeting_id, r.segment_uid)))


async def counts(db: AsyncSession) -> dict:
    out: dict = {}
    for model, _ in _tables():
        total = (await db.execute(select(func.count()).select_from(model))).scalar_one()
        sealed = (await db.execute(select(func.count()).select_from(model).where(model.text.like(sc.MAGIC + "%")))).scalar_one()
        out[model.__tablename__] = {"total": total, "sealed": sealed}
    out["rooms_encrypted"] = (await db.execute(select(func.count()).select_from(Room).where(Room.encrypt_storage.is_(True)))).scalar_one()
    return out


async def reseal(db: AsyncSession, *, room_id=None, rotate_all: bool = False, progress=None) -> dict:
    """Зашифровать записи комнаты (или всех комнат с включённым режимом, если `room_id` не задан). `rotate_all` — переписать и уже зашифрованное под активным ключом."""
    if not sc.keyring.ready:
        raise sc.StorageCryptoError("Ключ шифрования хранилища не загружен")
    active = sc.keyring.active
    done = skipped = 0
    for model, aad_of in _tables():
        t = model.__table__
        q = select(model.id).join(Meeting, Meeting.id == model.meeting_id).join(Room, Room.id == Meeting.room_id)
        q = q.where(Room.id == room_id) if room_id is not None else q.where(Room.encrypt_storage.is_(True))
        ids = [i for (i,) in (await db.execute(q.order_by(model.id))).all()]
        for start in range(0, len(ids), BATCH):
            chunk = ids[start:start + BATCH]
            cols = [t.c.id, t.c.text, t.c.meeting_id] + ([t.c.segment_uid] if model is TranscriptSegment else [])
            for r in (await db.execute(select(*cols).where(t.c.id.in_(chunk)))).all():       # сырые значения (без событий ORM): видно, что реально лежит в базе
                cur = r.text
                aad = aad_of(type("Row", (), {"meeting_id": r.meeting_id, "segment_uid": getattr(r, "segment_uid", None)}))
                if sc.is_sealed(cur):
                    if not rotate_all or int(cur.split(":", 2)[1]) == active:
                        skipped += 1
                        continue
                    plain = sc.keyring.open_(cur, aad)
                else:
                    plain = cur
                await db.execute(update(t).where(t.c.id == r.id).values(text=sc.keyring.seal(plain, aad)))
                done += 1
            await db.commit()
            if progress:
                await progress(done, skipped)
    crypto_events.invalidate()
    return {"sealed": done, "skipped": skipped}
