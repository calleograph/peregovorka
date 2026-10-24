"""Корпоративные чаты: личные и групповые переписки сотрудников вне видеовстреч.

Правила:
  * доступ к переписке, сообщениям и файлам — только участникам; членство проверяется при каждом обращении (`require_member`), идентификаторы
    чата, сообщения и файла сверяются между собой на сервере (подмена id чужого чата даёт 404, как и несуществующий);
  * личная переписка с человеком — одна на пару (`direct_key`); повторное «написать» открывает её, а не создаёт новую;
  * повторная отправка того же сообщения (сетевой сбой, повтор клиента) — по `client_id` возвращается уже записанное, дубля нет;
  * «очистить историю» и «удалить чат» — только у себя (`cleared_before_id`, `hidden`); общие сообщения физически не удаляются;
    удалить можно своё сообщение (текст стирается, вложения удаляются);
  * доставка в реальном времени — события в личный канал каждого участника (Redis pub/sub → WebSocket любого экземпляра backend);
    после обрыва клиент догружает пропущенное по REST (`after_id`), повторы отбрасываются по id;
  * поиск: PostgreSQL — полнотекстовый индекс (`to_tsvector('russian', text)`, префиксы слов), фразы в кавычках дополнительно сверяются по тексту;
    SQLite (тесты) — по вхождению. Выдача ограничена и постранична;
  * в технические журналы текст сообщений не пишется; в аудит — состав групп, роли, удаление сообщений.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, literal_column, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chat, ChatFile, ChatFolder, ChatMember, ChatMessage, User, utcnow
from . import events

PAGE = 50
MAX_PAGE = 200
SEARCH_LIMIT = 200
PREVIEW_CHARS = 160
MAX_FOLDERS = 30
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")      # управляющие и «разворот» текста
_WORD = re.compile(r"[\w]+", re.UNICODE)
PRESENCE_TTL = 75                       # с: клиент подтверждает активность раз в 30 с
DEFAULT_PREFS = {"presence": True, "sound": True}
_RU = literal_column("'russian'::regconfig")


class ChatError(Exception):
    """Ошибка с текстом для пользователя и HTTP-кодом."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message, self.status = message, status


def prefs(u: User | None) -> dict:
    raw = (u.chat_prefs if u is not None else None) or {}
    return {k: bool(raw.get(k, v)) for k, v in DEFAULT_PREFS.items()}


def clean_text(raw, limit: int, *, allow_empty: bool = False) -> str:
    text = _CTRL.sub("", str(raw or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text and not allow_empty:
        raise ChatError("Пустое сообщение")
    if len(text) > limit:
        raise ChatError(f"Сообщение длиннее {limit} символов")
    return text


def direct_key(a: uuid.UUID, b: uuid.UUID) -> str:
    x, y = sorted((str(a), str(b)))
    return f"{x}:{y}"


# ------------------------------------------------------------------------------------------------ участники и доступ
async def membership(db: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID) -> ChatMember | None:
    return (await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id))).scalar_one_or_none()


async def require_member(db: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Chat, ChatMember]:
    """Чат и членство. Чужой и несуществующий чат отвечают одинаково (404) — существование чужих переписок не раскрывается."""
    m = await membership(db, chat_id, user_id)
    chat = await db.get(Chat, chat_id) if m is not None else None
    if m is None or chat is None:
        raise ChatError("Чат не найден", 404)
    return chat, m


async def member_ids(db: AsyncSession, chat_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars())


def require_admin(chat: Chat, m: ChatMember) -> None:
    if chat.kind != "group" or m.role != "admin":
        raise ChatError("Изменять группу могут только её администраторы", 403)


async def _active_users(db: AsyncSession, ids) -> dict[uuid.UUID, User]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    return {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids), User.is_active.is_(True)))).scalars()}


async def user_from_directory(db: AsyncSession, entry: dict) -> User:
    """Сотрудник из каталога, ещё ни разу не входивший: заводится запись по objectGUID (при первом входе её дополнит обычная синхронизация профиля)."""
    guid = str(entry.get("ref") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f-]{32,36}", guid):
        raise ChatError("Сотрудник не найден в каталоге", 404)
    u = (await db.execute(select(User).where(func.lower(User.ad_guid) == guid))).scalar_one_or_none()
    if u is not None:
        return u
    u = User(ad_guid=guid, sam_account_name=str(entry.get("sam") or guid)[:256], display_name=(str(entry.get("name") or "").strip() or str(entry.get("sam") or "Сотрудник"))[:300],
             email=(str(entry.get("email") or "").strip().lower() or None), auth_source="ad")
    db.add(u)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        u = (await db.execute(select(User).where(func.lower(User.ad_guid) == guid))).scalar_one()
    return u


# ------------------------------------------------------------------------------------------------ создание переписок
async def open_direct(db: AsyncSession, me: uuid.UUID, other_id: uuid.UUID) -> tuple[Chat, bool]:
    """Личная переписка с сотрудником: существующая (у себя снова видна) или новая. Сам с собой — нельзя."""
    if other_id == me:
        raise ChatError("Нельзя начать переписку с самим собой")
    if other_id not in await _active_users(db, [other_id]):
        raise ChatError("Сотрудник не найден или учётная запись отключена", 404)
    key = direct_key(me, other_id)
    chat = (await db.execute(select(Chat).where(Chat.direct_key == key))).scalar_one_or_none()
    if chat is None:
        chat = Chat(kind="direct", direct_key=key, created_by_user_id=me)
        db.add(chat)
        try:
            await db.flush()
            db.add_all([ChatMember(chat_id=chat.id, user_id=me), ChatMember(chat_id=chat.id, user_id=other_id)])
            await db.commit()
            return chat, True
        except IntegrityError:                          # оба одновременно нажали «написать»: берём созданную другим
            await db.rollback()
            chat = (await db.execute(select(Chat).where(Chat.direct_key == key))).scalar_one()
    m = await membership(db, chat.id, me)
    if m is None:                                       # участника удаляли из базы — восстанавливаем пару
        db.add(ChatMember(chat_id=chat.id, user_id=me))
    elif m.hidden:
        m.hidden = False
    await db.commit()
    return chat, False


async def create_group(db: AsyncSession, me: User, title: str, members: list[uuid.UUID], cfg) -> Chat:
    title = " ".join(_CTRL.sub("", str(title or "")).split())[:200]
    if not title:
        raise ChatError("Укажите название группы")
    users = await _active_users(db, [x for x in members if x != me.id])
    if len(users) + 1 > cfg.max_group_members:
        raise ChatError(f"В группе не больше {cfg.max_group_members} участников")
    chat = Chat(kind="group", title=title, created_by_user_id=me.id)
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=me.id, role="admin"))
    db.add_all([ChatMember(chat_id=chat.id, user_id=uid) for uid in users])
    await system_message(db, chat, me, f"{me.display_name} создаёт группу «{title}»")
    await db.commit()
    return chat


async def system_message(db: AsyncSession, chat: Chat, actor: User | None, text: str) -> ChatMessage:
    """Служебная строка в ленте группы (кто добавил, кто вышел). Без коммита."""
    msg = ChatMessage(chat_id=chat.id, author_id=actor.id if actor else None, author_name=actor.display_name if actor else "", kind="system", text=text[:500])
    db.add(msg)
    await db.flush()
    chat.last_message_id, chat.last_message_at = msg.id, msg.created_at
    return msg


async def add_members(db: AsyncSession, chat: Chat, actor: User, ids: list[uuid.UUID], cfg) -> list[User]:
    have = set(await member_ids(db, chat.id))
    users = [u for uid, u in (await _active_users(db, ids)).items() if uid not in have]
    if len(have) + len(users) > cfg.max_group_members:
        raise ChatError(f"В группе не больше {cfg.max_group_members} участников")
    for u in users:
        db.add(ChatMember(chat_id=chat.id, user_id=u.id))
    if users:
        await system_message(db, chat, actor, f"{actor.display_name} добавляет: " + ", ".join(u.display_name for u in users))
    await db.commit()
    return users


async def remove_member(db: AsyncSession, chat: Chat, actor: User, user_id: uuid.UUID) -> User | None:
    """Исключение (администратор) или выход (сам). Последний администратор при выходе передаёт роль самому давнему участнику."""
    m = await membership(db, chat.id, user_id)
    if m is None:
        return None
    u = await db.get(User, user_id)
    await db.delete(m)
    await db.flush()
    rest = (await db.execute(select(ChatMember).where(ChatMember.chat_id == chat.id).order_by(ChatMember.joined_at, ChatMember.id))).scalars().all()
    if rest and not any(x.role == "admin" for x in rest):
        rest[0].role = "admin"
    name = u.display_name if u else "участник"
    await system_message(db, chat, actor, f"{name} выходит из группы" if actor.id == user_id else f"{actor.display_name} исключает: {name}")
    await db.commit()
    return u


# ------------------------------------------------------------------------------------------------ сообщения
def message_out(m: ChatMessage, files: list[ChatFile] | None = None, reply: ChatMessage | None = None) -> dict:
    deleted = m.deleted_at is not None
    out = {"id": m.id, "chat_id": str(m.chat_id), "author_id": str(m.author_id) if m.author_id else None, "author_name": m.author_name, "kind": m.kind,
           "text": "" if deleted else m.text, "created_at": m.created_at.isoformat(), "edited_at": m.edited_at.isoformat() if m.edited_at else None,
           "deleted": deleted, "client_id": m.client_id, "forwarded": m.forwarded, "reply_to": None,
           "files": [] if deleted else [file_out(f) for f in files or []]}
    if m.reply_to_id:
        out["reply_to"] = {"id": m.reply_to_id, "author_name": reply.author_name if reply else "", "text": "" if reply is None or reply.deleted_at else reply.text[:PREVIEW_CHARS],
                           "deleted": reply is None or reply.deleted_at is not None}
    return out


def file_out(f: ChatFile) -> dict:
    return {"id": str(f.id), "name": f.name, "mime": f.mime, "size": f.size, "kind": f.kind, "missing": f.file_state == "missing"}


async def _files_for(db: AsyncSession, ids: list[int]) -> dict[int, list[ChatFile]]:
    out: dict[int, list[ChatFile]] = {}
    if ids:
        for f in (await db.execute(select(ChatFile).where(ChatFile.message_id.in_(ids)).order_by(ChatFile.created_at))).scalars():
            out.setdefault(f.message_id, []).append(f)
    return out


async def render(db: AsyncSession, rows: list[ChatMessage]) -> list[dict]:
    files = await _files_for(db, [m.id for m in rows])
    want = {m.reply_to_id for m in rows if m.reply_to_id} - {m.id for m in rows}
    replies = {m.id: m for m in rows}
    if want:
        replies.update({r.id: r for r in (await db.execute(select(ChatMessage).where(ChatMessage.id.in_(want)))).scalars()})
    return [message_out(m, files.get(m.id), replies.get(m.reply_to_id) if m.reply_to_id else None) for m in rows]


async def history(db: AsyncSession, chat: Chat, me: ChatMember, *, before_id: int | None = None, after_id: int | None = None, around_id: int | None = None,
                  limit: int = PAGE) -> dict:
    """Страница переписки (старые — по прокрутке вверх, новые — после переподключения, окно вокруг найденного сообщения). Очищенное у себя не показывается."""
    limit = max(1, min(limit, MAX_PAGE))
    base = select(ChatMessage).where(ChatMessage.chat_id == chat.id, ChatMessage.id > me.cleared_before_id)
    if after_id is not None:
        rows = list((await db.execute(base.where(ChatMessage.id > after_id).order_by(ChatMessage.id).limit(limit + 1))).scalars())
        more_after, rows = len(rows) > limit, rows[:limit]
        return {"messages": await render(db, rows), "has_more_before": None, "has_more_after": more_after}
    if around_id is not None:
        half = limit // 2
        older = list((await db.execute(base.where(ChatMessage.id < around_id).order_by(ChatMessage.id.desc()).limit(half + 1))).scalars())
        newer = list((await db.execute(base.where(ChatMessage.id >= around_id).order_by(ChatMessage.id).limit(half + 1))).scalars())
        rows = list(reversed(older[:half])) + newer[:half]
        return {"messages": await render(db, rows), "has_more_before": len(older) > half, "has_more_after": len(newer) > half}
    if before_id is not None:
        base = base.where(ChatMessage.id < before_id)
    rows = list((await db.execute(base.order_by(ChatMessage.id.desc()).limit(limit + 1))).scalars())
    more, rows = len(rows) > limit, list(reversed(rows[:limit]))
    return {"messages": await render(db, rows), "has_more_before": more, "has_more_after": False}


@dataclass
class Posted:
    message: ChatMessage
    created: bool


async def post(db: AsyncSession, chat: Chat, author: User, *, text: str, client_id: str | None, reply_to: int | None = None, file_ids: list[uuid.UUID] = (),
               forwarded: dict | None = None, max_files: int = 10) -> Posted:
    """Новое сообщение. Повтор с тем же `client_id` (сеть оборвалась после записи) возвращает записанное, без дубля."""
    if client_id is not None and not CLIENT_ID.match(client_id):
        raise ChatError("Некорректный идентификатор сообщения")
    chat_id, author_id = chat.id, author.id                 # после отката (гонка повторов) объекты ORM устаревают — дальше только эти значения
    if client_id:
        old = await _by_client(db, chat_id, author_id, client_id)
        if old is not None:
            return Posted(old, False)
    if len(file_ids) > max_files:
        raise ChatError(f"В одном сообщении не больше {max_files} файлов")
    files: list[ChatFile] = []
    for fid in dict.fromkeys(file_ids):
        f = await db.get(ChatFile, fid)
        # прикрепить можно только своё, загруженное в ЭТОТ чат и ещё не отправленное вложение
        if f is None or f.chat_id != chat.id or f.message_id is not None or f.uploader_id != author.id:
            raise ChatError("Вложение не найдено или уже отправлено")
        files.append(f)
    if not text and not files and not forwarded:
        raise ChatError("Пустое сообщение")
    if reply_to is not None:
        r = await db.get(ChatMessage, reply_to)
        if r is None or r.chat_id != chat.id:
            raise ChatError("Сообщение для ответа не найдено")
    msg = ChatMessage(chat_id=chat.id, author_id=author.id, author_name=author.display_name, text=text, client_id=client_id or None, reply_to_id=reply_to,
                      forwarded=forwarded)
    db.add(msg)
    try:
        await db.flush()
    except IntegrityError:                              # два одинаковых запроса одновременно (в том числе в разные экземпляры): второй получает записанное первым
        await db.rollback()
        old = await _by_client(db, chat_id, author_id, client_id) if client_id else None
        if old is None:
            raise
        return Posted(old, False)
    for f in files:
        f.message_id = msg.id
    chat.last_message_id, chat.last_message_at = msg.id, msg.created_at
    await db.execute(update(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.hidden.is_(True)).values(hidden=False))     # «удалённый у себя» чат вернётся
    me = await membership(db, chat.id, author.id)
    if me is not None:
        me.last_read_id = msg.id                       # своё сообщение прочитано
    await db.commit()
    return Posted(msg, True)


async def _by_client(db: AsyncSession, chat_id: uuid.UUID, author_id: uuid.UUID, client_id: str) -> ChatMessage | None:
    return (await db.execute(select(ChatMessage).where(ChatMessage.chat_id == chat_id, ChatMessage.author_id == author_id,
                                                       ChatMessage.client_id == client_id))).scalar_one_or_none()


async def own_message(db: AsyncSession, chat: Chat, me: ChatMember, mid: int) -> ChatMessage:
    m = await db.get(ChatMessage, mid)
    if m is None or m.chat_id != chat.id or m.id <= me.cleared_before_id:
        raise ChatError("Сообщение не найдено", 404)
    if m.author_id != me.user_id or m.kind != "text":
        raise ChatError("Изменять и удалять можно только свои сообщения", 403)
    if m.deleted_at is not None:
        raise ChatError("Сообщение уже удалено", 409)
    return m


async def visible_message(db: AsyncSession, chat: Chat, me: ChatMember, mid: int) -> ChatMessage:
    m = await db.get(ChatMessage, mid)
    if m is None or m.chat_id != chat.id or m.id <= me.cleared_before_id:
        raise ChatError("Сообщение не найдено", 404)
    return m


async def mark_read(db: AsyncSession, chat: Chat, me: ChatMember, last_id: int) -> bool:
    """Прочитано до `last_id` (не дальше последнего сообщения, назад не откатывается)."""
    top = min(int(last_id), chat.last_message_id or 0)
    if top <= me.last_read_id:
        return False
    me.last_read_id = top
    await db.commit()
    return True


async def receipts(db: AsyncSession, chat: Chat, msg: ChatMessage) -> list[dict]:
    """Кто прочитал сообщение (по отметке «прочитано до»). Для личной переписки — собеседник, для группы — участники кроме автора."""
    rows = (await db.execute(select(ChatMember.user_id, ChatMember.last_read_id, User.display_name).join(User, User.id == ChatMember.user_id)
                             .where(ChatMember.chat_id == chat.id, ChatMember.user_id != msg.author_id))).all()
    return sorted(({"user_id": str(r.user_id), "name": r.display_name, "read": r.last_read_id >= msg.id} for r in rows), key=lambda x: (not x["read"], x["name"].lower()))


# ------------------------------------------------------------------------------------------------ список переписок
async def chat_list(db: AsyncSession, me: uuid.UUID, *, include_hidden: bool = False) -> list[dict]:
    """Все переписки пользователя с его личными настройками, последним сообщением, числом непрочитанных и собеседником. Постоянное число запросов."""
    cond = [ChatMember.user_id == me] + ([] if include_hidden else [ChatMember.hidden.is_(False)])
    rows = (await db.execute(select(Chat, ChatMember).join(ChatMember, ChatMember.chat_id == Chat.id).where(*cond))).all()
    if not rows:
        return []
    ids = [c.id for c, _ in rows]
    last_ids = [c.last_message_id for c, _ in rows if c.last_message_id]
    last = {m.id: m for m in (await db.execute(select(ChatMessage).where(ChatMessage.id.in_(last_ids)))).scalars()} if last_ids else {}
    mine = select(ChatMember.chat_id, ChatMember.last_read_id, ChatMember.cleared_before_id).where(ChatMember.user_id == me, ChatMember.chat_id.in_(ids)).subquery()
    unread = dict((await db.execute(
        select(ChatMessage.chat_id, func.count(ChatMessage.id)).join(mine, mine.c.chat_id == ChatMessage.chat_id)
        .where(ChatMessage.id > mine.c.last_read_id, ChatMessage.id > mine.c.cleared_before_id, ChatMessage.deleted_at.is_(None),
               or_(ChatMessage.author_id.is_(None), ChatMessage.author_id != me), ChatMessage.kind == "text")
        .group_by(ChatMessage.chat_id))).all())
    counts = dict((await db.execute(select(ChatMember.chat_id, func.count()).where(ChatMember.chat_id.in_(ids)).group_by(ChatMember.chat_id))).all())
    direct = [c.id for c, _ in rows if c.kind == "direct"]
    peers: dict = {}
    if direct:
        for cid, u in (await db.execute(select(ChatMember.chat_id, User).join(User, User.id == ChatMember.user_id)
                                        .where(ChatMember.chat_id.in_(direct), ChatMember.user_id != me))).all():
            peers[cid] = u
    out = []
    for c, m in rows:
        lm = last.get(c.last_message_id) if c.last_message_id and c.last_message_id > m.cleared_before_id else None
        peer = peers.get(c.id)
        out.append({
            "id": str(c.id), "kind": c.kind, "title": c.title if c.kind == "group" else (peer.display_name if peer else "Сотрудник"),
            "avatar_url": chat_avatar_url(c) if c.kind == "group" else (user_avatar_url(peer) if peer else None),
            "peer": {"id": str(peer.id), "name": peer.display_name, "department": peer.department, "active": peer.is_active} if peer else None,
            "members": counts.get(c.id, 0), "role": m.role,
            "last": {"id": lm.id, "author_id": str(lm.author_id) if lm.author_id else None, "author_name": lm.author_name, "kind": lm.kind,
                     "text": "" if lm.deleted_at else _preview(lm.text), "deleted": lm.deleted_at is not None, "at": lm.created_at.isoformat()} if lm else None,
            "last_at": (c.last_message_at or c.created_at).isoformat(), "unread": int(unread.get(c.id, 0)), "last_read_id": m.last_read_id,
            "muted": m.muted, "pinned": m.pinned_at is not None, "pinned_at": m.pinned_at.isoformat() if m.pinned_at else None, "archived": m.archived,
            "folder_id": str(m.folder_id) if m.folder_id else None, "hidden": m.hidden,
        })
    out.sort(key=lambda x: (not x["pinned"], -(datetime.fromisoformat(x["pinned_at"]).timestamp() if x["pinned_at"] else 0),
                            -datetime.fromisoformat(x["last_at"]).timestamp()))
    return out


def _preview(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= PREVIEW_CHARS else t[: PREVIEW_CHARS - 1] + "…"


def user_avatar_url(u: User | None) -> str | None:
    return f"/api/v1/users/{u.id}/avatar?v={int(u.avatar_updated_at.timestamp())}" if u and u.avatar_mime and u.avatar_updated_at else None


def chat_avatar_url(c: Chat) -> str | None:
    return f"/api/v1/chats/{c.id}/avatar?v={int(c.avatar_updated_at.timestamp())}" if c.avatar_mime and c.avatar_updated_at else None


async def unread_total(db: AsyncSession, me: uuid.UUID) -> int:
    """Число непрочитанных во всех переписках, кроме тех, где уведомления выключены (для значка на кнопке «Чаты»)."""
    m = ChatMember
    n = (await db.execute(select(func.count(ChatMessage.id)).select_from(ChatMessage).join(m, m.chat_id == ChatMessage.chat_id)
                          .where(m.user_id == me, m.muted.is_(False), m.hidden.is_(False), ChatMessage.id > m.last_read_id, ChatMessage.id > m.cleared_before_id,
                                 ChatMessage.deleted_at.is_(None), ChatMessage.kind == "text", or_(ChatMessage.author_id.is_(None), ChatMessage.author_id != me)))).scalar_one()
    return int(n or 0)


async def chat_info(db: AsyncSession, chat: Chat, me: ChatMember) -> dict:
    rows = (await db.execute(select(ChatMember, User).join(User, User.id == ChatMember.user_id).where(ChatMember.chat_id == chat.id)
                             .order_by(ChatMember.role != "admin", func.lower(User.display_name)))).all()
    return {"id": str(chat.id), "kind": chat.kind, "title": chat.title, "avatar_url": chat_avatar_url(chat), "created_at": chat.created_at.isoformat(), "my_role": me.role,
            "members": [{"id": str(u.id), "name": u.display_name, "department": u.department, "title": u.title, "email": u.email, "role": m.role,
                         "avatar_url": user_avatar_url(u), "active": u.is_active} for m, u in rows]}


# ------------------------------------------------------------------------------------------------ личные настройки и папки
async def set_member_prefs(db: AsyncSession, me: ChatMember, body: dict) -> None:
    if "muted" in body:
        me.muted = bool(body["muted"])
    if "pinned" in body:
        me.pinned_at = utcnow() if body["pinned"] else None
    if "archived" in body:
        me.archived = bool(body["archived"])
    if "folder_id" in body:
        fid = body["folder_id"]
        if fid in (None, ""):
            me.folder_id = None
        else:
            try:
                f = await db.get(ChatFolder, uuid.UUID(str(fid)))
            except ValueError:
                f = None
            if f is None or f.user_id != me.user_id:
                raise ChatError("Папка не найдена", 404)
            me.folder_id = f.id
    await db.commit()


async def clear_history(db: AsyncSession, chat: Chat, me: ChatMember) -> None:
    """Очистка только у себя: сообщения до последнего становятся невидимы этому участнику; у остальных переписка не меняется."""
    me.cleared_before_id = max(me.cleared_before_id, chat.last_message_id or 0)
    me.last_read_id = max(me.last_read_id, me.cleared_before_id)
    await db.commit()


async def folders(db: AsyncSession, me: uuid.UUID) -> list[dict]:
    rows = (await db.execute(select(ChatFolder).where(ChatFolder.user_id == me).order_by(ChatFolder.position, ChatFolder.created_at))).scalars().all()
    return [{"id": str(f.id), "name": f.name, "position": f.position} for f in rows]


def _folder_name(raw) -> str:
    name = " ".join(_CTRL.sub("", str(raw or "")).split())[:60]
    if not name:
        raise ChatError("Укажите название папки")
    return name


async def create_folder(db: AsyncSession, me: uuid.UUID, name) -> ChatFolder:
    name = _folder_name(name)
    n = (await db.execute(select(func.count()).select_from(ChatFolder).where(ChatFolder.user_id == me))).scalar_one()
    if n >= MAX_FOLDERS:
        raise ChatError(f"Не больше {MAX_FOLDERS} папок")
    f = ChatFolder(user_id=me, name=name, position=n)
    db.add(f)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ChatError("Папка с таким названием уже есть", 409) from None
    return f


async def own_folder(db: AsyncSession, me: uuid.UUID, folder_id: uuid.UUID) -> ChatFolder:
    f = await db.get(ChatFolder, folder_id)
    if f is None or f.user_id != me:
        raise ChatError("Папка не найдена", 404)
    return f


async def rename_folder(db: AsyncSession, f: ChatFolder, name) -> None:
    f.name = _folder_name(name)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ChatError("Папка с таким названием уже есть", 409) from None


async def delete_folder(db: AsyncSession, f: ChatFolder) -> None:
    """Папка удаляется, переписки из неё возвращаются в общий список (сами чаты не удаляются)."""
    await db.execute(update(ChatMember).where(ChatMember.folder_id == f.id).values(folder_id=None))
    await db.delete(f)
    await db.commit()


# ------------------------------------------------------------------------------------------------ поиск
def parse_query(q: str) -> tuple[list[str], list[str]]:
    """Слова и фразы в кавычках; слова — буквы и цифры (всё остальное отбрасывается: в запрос к базе попадают только безопасные части)."""
    q = (q or "")[:200]
    phrases = [" ".join(p.split()) for p in re.findall(r'"([^"]+)"', q) if p.strip()]
    words = _WORD.findall(re.sub(r'"[^"]*"', " ", q))
    return [w.lower() for w in words if len(w) >= 1][:8], [p for p in phrases if len(p) >= 2][:3]


def _match(dialect: str, words: list[str], phrases: list[str]):
    """Условие для PostgreSQL (полнотекстовый индекс + сверка фраз). Для SQLite (тесты, стенд) — None: там отбор делает Python (`_py_match`),
    потому что lower()/LIKE в SQLite не знают кириллицу."""
    if dialect != "postgresql":
        return None
    conds = []
    terms = words + [w.lower() for p in phrases for w in _WORD.findall(p)]
    if terms:
        tsq = " & ".join(f"{t}:*" for t in terms)         # только буквы и цифры: синтаксис tsquery не внедрить; «:*» — поиск по началу слова
        conds.append(func.to_tsvector(_RU, ChatMessage.text).op("@@")(func.to_tsquery(_RU, tsq)))      # то же выражение, что в индексе GIN
    conds += [ChatMessage.text.ilike(f"%{_like(p)}%", escape="\\") for p in phrases]
    return and_(*conds) if conds else None


def _py_match(text: str, words: list[str], phrases: list[str]) -> bool:
    low = text.casefold()
    return all(w.casefold() in low for w in words) and all(p.casefold() in low for p in phrases)


SQLITE_SCAN = 5000          # shortcut: в SQLite поиск — перебором последних сообщений; рабочая база — PostgreSQL с индексом


async def _find(db: AsyncSession, stmt, words: list[str], phrases: list[str], limit: int) -> list[ChatMessage]:
    cond = _match(db.bind.dialect.name, words, phrases)
    if cond is not None:
        return list((await db.execute(stmt.where(cond).order_by(ChatMessage.id.desc()).limit(limit))).scalars())
    rows = (await db.execute(stmt.order_by(ChatMessage.id.desc()).limit(SQLITE_SCAN))).scalars()
    return [m for m in rows if _py_match(m.text, words, phrases)][:limit]


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_messages(db: AsyncSession, chat: Chat, me: ChatMember, q: str, *, author_id: uuid.UUID | None = None, date_from: datetime | None = None,
                          date_to: datetime | None = None, before_id: int | None = None) -> dict:
    """Поиск в одной переписке: совпадения от новых к старым, не больше SEARCH_LIMIT за раз (дальше — `before_id`)."""
    words, phrases = parse_query(q)
    if not words and not phrases:
        return {"items": [], "total": 0, "more": False, "terms": []}
    stmt = select(ChatMessage).where(ChatMessage.chat_id == chat.id, ChatMessage.id > me.cleared_before_id, ChatMessage.deleted_at.is_(None),
                                     ChatMessage.kind == "text")
    if author_id:
        stmt = stmt.where(ChatMessage.author_id == author_id)
    if date_from:
        stmt = stmt.where(ChatMessage.created_at >= date_from)
    if date_to:
        stmt = stmt.where(ChatMessage.created_at < date_to)
    if before_id:
        stmt = stmt.where(ChatMessage.id < before_id)
    rows = await _find(db, stmt, words, phrases, SEARCH_LIMIT + 1)
    more, rows = len(rows) > SEARCH_LIMIT, rows[:SEARCH_LIMIT]
    return {"items": [{"id": m.id, "author_name": m.author_name, "created_at": m.created_at.isoformat(), "text": _snippet(m.text, words + phrases)} for m in rows],
            "total": len(rows), "more": more, "terms": words + phrases}


def _snippet(text: str, terms: list[str], width: int = 180) -> str:
    low = text.lower()
    pos = min((i for i in (low.find(t.lower()) for t in terms) if i >= 0), default=0)
    start = max(0, pos - width // 3)
    s = " ".join(text[start:start + width].split())
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")


async def search_all(db: AsyncSession, me: uuid.UUID, q: str, limit: int = 30) -> dict:
    """Общий поиск слева: переписки по названию и собеседнику, сообщения во всех своих переписках (только видимые этому пользователю)."""
    words, phrases = parse_query(q)
    if not words and not phrases:
        return {"chats": [], "messages": []}
    needle = (phrases[0] if phrases else " ".join(words)).casefold()
    chats = [c for c in await chat_list(db, me, include_hidden=True) if needle in (c["title"] or "").casefold()][:20]
    mine = select(ChatMember.chat_id, ChatMember.cleared_before_id).where(ChatMember.user_id == me).subquery()
    rows = await _find(db, select(ChatMessage).join(mine, and_(mine.c.chat_id == ChatMessage.chat_id, ChatMessage.id > mine.c.cleared_before_id))
                       .where(ChatMessage.deleted_at.is_(None), ChatMessage.kind == "text"), words, phrases, limit)
    return {"chats": chats, "messages": [{"id": m.id, "chat_id": str(m.chat_id), "author_name": m.author_name, "created_at": m.created_at.isoformat(),
                                          "text": _snippet(m.text, words + phrases)} for m in rows], "terms": words + phrases}


# ------------------------------------------------------------------------------------------------ события и присутствие
async def notify(redis, user_ids, event: dict) -> None:
    await events.publish_users(redis, user_ids, event)


def _pkey(uid) -> str:
    return f"presence:{uid}"


async def touch_presence(redis, uid: uuid.UUID, visible: bool) -> bool:
    """Пользователь активен (вкладка видна, было действие): ключ живёт PRESENCE_TTL. Скрывший статус не отмечается вовсе. True — только что «появился в сети»."""
    if not visible:
        await redis.delete(_pkey(uid))
        return False
    came = await redis.set(_pkey(uid), "1", ex=PRESENCE_TTL, nx=True)
    if not came:
        await redis.expire(_pkey(uid), PRESENCE_TTL)
    return bool(came)


async def online(redis, ids) -> set[str]:
    ids = [str(i) for i in ids]
    if not ids:
        return set()
    vals = await redis.mget([_pkey(i) for i in ids])
    return {i for i, v in zip(ids, vals) if v}


async def watch_presence(redis, watcher: uuid.UUID, target: uuid.UUID) -> None:
    """«Сообщить, когда появится в сети»: одноразовая подписка на неделю. История активности не хранится."""
    k = f"presence:watch:{target}"
    await redis.sadd(k, str(watcher))
    await redis.expire(k, 7 * 86400)


async def unwatch_presence(redis, watcher: uuid.UUID, target: uuid.UUID) -> None:
    await redis.srem(f"presence:watch:{target}", str(watcher))


async def watching(redis, watcher: uuid.UUID, target: uuid.UUID) -> bool:
    return bool(await redis.sismember(f"presence:watch:{target}", str(watcher)))


async def came_online(redis, user: User) -> None:
    """Сообщить подписчикам и снять подписки (одноразово)."""
    k = f"presence:watch:{user.id}"
    watchers = await redis.smembers(k)
    if not watchers:
        return
    await redis.delete(k)
    await notify(redis, [w.decode() if isinstance(w, bytes) else w for w in watchers], {"type": "chat_presence_online", "user_id": str(user.id), "name": user.display_name})


# ------------------------------------------------------------------------------------------------ хранение
async def purge_expired(db: AsyncSession, files_svc, days: int, *, batch: int = 500) -> int:
    """Срок хранения переписки (настройка «Чаты»): сообщения старше и их файлы удаляются пачками. 0 — без срока."""
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    total = 0
    while True:
        ids = list((await db.execute(select(ChatMessage.id).where(ChatMessage.created_at < cutoff).order_by(ChatMessage.id).limit(batch))).scalars())
        if not ids:
            break
        old = list((await db.execute(select(ChatFile).where(ChatFile.message_id.in_(ids)))).scalars())
        if old:
            await files_svc.remove_files(db, old)
        await db.execute(delete(ChatMessage).where(ChatMessage.id.in_(ids)))
        await db.commit()
        total += len(ids)
    return total


def dumps(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, default=str)
