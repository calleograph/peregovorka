"""Материалы встречи для LLM: стенограмма + чат + описание схемы с общей доски.

Чат — отдельный источник контекста, а не часть речи: в нём приходят ссылки, IP-адреса, имена серверов, номера задач и точные формулировки,
которые на слух легко исказить. Схема с доски (draw.io) передаётся текстовым описанием блоков и связей. Что из этого использовать в протоколе,
решает модель (инструкция в SYSTEM_PROMPT объясняет ей природу каждого источника).
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ChatAttachment, Meeting, MeetingChatMessage, MeetingWhiteboard
from . import whiteboard as wb

TRANSCRIPT_HEADER = "=== СТЕНОГРАММА (устная речь участников, распознана автоматически; возможны неточности) ==="
CHAT_HEADER = ("=== ЧАТ ВСТРЕЧИ (письменные сообщения участников; ссылки, адреса, названия серверов, номера задач "
               "и формулировки в нём — точные) ===")
BOARD_HEADER = "=== СХЕМА НА ОБЩЕЙ ДОСКЕ (текстовое описание блоков и связей; сама схема сохранена в формате draw.io) ==="

SOURCES_PROMPT = (
    "Материалы встречи могут состоять из нескольких источников: СТЕНОГРАММА (устная речь), ЧАТ (письменные сообщения) и СХЕМА НА ОБЩЕЙ ДОСКЕ. "
    "Используй их совместно. Из чата дословно переноси то, что нельзя искажать: URL, IP-адреса, имена серверов и сервисов, номера задач, "
    "точные формулировки. Если речь и чат расходятся — доверяй чату и отметь расхождение. Схему используй как пояснение к обсуждению "
    "(из каких блоков состоит система и как они связаны). Не выдумывай того, чего нет ни в одном из источников."
)


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} МБ" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} КБ"


def render_chat(messages: list[MeetingChatMessage], tz, files: dict[int, list[ChatAttachment]] | None = None) -> str:
    """Чат как текст: [ЧЧ:ММ:СС] Автор: сообщение; продолжение многострочного сообщения — с отступом; вложения — строками «[файл: имя, размер]»
    (содержимое файлов в текст и в LLM не попадает — только названия)."""
    lines: list[str] = []
    for m in messages:
        first, *rest = m.text.split("\n")
        lines.append(f"[{m.created_at.astimezone(tz):%H:%M:%S}] {m.author_name}: {first}")
        lines.extend("    " + r for r in rest)
        for a in (files or {}).get(m.id, []):
            lines.append(f"    [{'картинка' if a.kind == 'image' else 'файл'}: {a.name}, {_size(a.size)}]")
    return "\n".join(lines) + ("\n" if lines else "")


@dataclass
class Materials:
    text: str                 # полный пакет для LLM
    has_speech: bool
    chat_messages: int
    whiteboard_shapes: int

    @property
    def usable(self) -> bool:
        return self.has_speech or self.chat_messages > 0 or self.whiteboard_shapes > 0

    def meta(self) -> dict:
        return {"transcript": self.has_speech, "chat_messages": self.chat_messages, "whiteboard_shapes": self.whiteboard_shapes}


async def chat_files_map(db: AsyncSession, messages: list[MeetingChatMessage]) -> dict[int, list[ChatAttachment]]:
    out: dict[int, list[ChatAttachment]] = {}
    ids = [m.id for m in messages]
    if ids:
        rows = (await db.execute(select(ChatAttachment).where(ChatAttachment.message_id.in_(ids)).order_by(ChatAttachment.created_at))).scalars().all()
        for a in rows:
            out.setdefault(a.message_id, []).append(a)  # type: ignore[arg-type]
    return out


async def chat_messages(db: AsyncSession, meeting_id) -> list[MeetingChatMessage]:
    return list((await db.execute(select(MeetingChatMessage).where(MeetingChatMessage.meeting_id == meeting_id)
                                  .order_by(MeetingChatMessage.id))).scalars().all())


async def board_description(db: AsyncSession, meeting_id) -> tuple[str, int]:
    row = await db.get(MeetingWhiteboard, meeting_id)
    if row is None or not row.xml or row.shapes <= 0:
        return "", 0
    try:
        d = wb.describe(row.xml)
    except wb.WhiteboardError:
        return "", 0
    return d.text, row.shapes


async def build_materials(db: AsyncSession, meeting: Meeting, tz: ZoneInfo, transcript: str) -> Materials:
    has_speech = any(line.startswith("[") for line in transcript.splitlines())
    chat = await chat_messages(db, meeting.id)
    board_text, shapes = await board_description(db, meeting.id)
    if not chat and not shapes:
        return Materials(transcript, has_speech, 0, 0)  # обычная встреча без чата и доски: текст ровно как раньше
    parts = [f"{TRANSCRIPT_HEADER}\n{transcript.rstrip()}"]
    if chat:
        parts.append(f"{CHAT_HEADER}\n{render_chat(chat, tz, await chat_files_map(db, chat)).rstrip()}")
    if shapes and board_text:
        parts.append(f"{BOARD_HEADER}\n{board_text.rstrip()}")
    return Materials("\n\n".join(parts) + "\n", has_speech, len(chat), shapes)
