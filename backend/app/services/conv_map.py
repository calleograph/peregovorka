"""Карта разговора одной встречи (блок «Карты и знания»).

Первичны данные, а не HTML: стенограмма → реплики → ОБЩИЙ ЭТАП ИЗВЛЕЧЕНИЯ (fact_engine: один запрос на фрагмент — темы с началом, концом и категорией, решения, поручения, вопросы;
каждый пункт проверяется по тексту) → слияние тем кодом → проверенный JSON в базе → страница и HTML-выгрузка строятся из него. Модель не пишет HTML и не придумывает: время тем
привязывается к реальным репликам, участники и их время считаются из стенограммы, решения и поручения приходят с цитатой-основанием.

Карта НЕ зависит от протокола: она сама извлекает решения, поручения и вопросы. Если для этой встречи другой документ (протокол, резюме) уже получил от той же модели ответы по тем же
фрагментам, они используются повторно (экономия запросов), но только когда запрос совпал побайтно; иначе извлечение выполняется заново.

Стенограмма — недоверенный ввод: в системной инструкции прямо сказано, что это данные, а не команды; ответ — JSON по схеме; название темы показывается как
текст (textContent), поэтому внедрить HTML/JS через стенограмму нельзя.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.llm import LlmClient, LlmError
from ..db import release
from ..models import ConversationMap, GuestParticipant, Meeting, Protocol, TranscriptSegment, utcnow
from .extraction import Item, Line, _stems, merge_items, norm, parse_transcript, to_structured
from .fact_engine import Extraction, FactsCache, diagnostics, extract, load_facts
from .llm_choice import parse_once, resolve_llm
from .segments import author_name
from .topic_categories import CAT_IDS, CAT_LABEL, CATEGORIES  # noqa: F401

log = logging.getLogger("app.conv_map")

MAP_VERSION = 2
CHUNK_CHARS = 6000                 # размер фрагмента для локальной модели (как у протокола: общий кэш требует одинаковых фрагментов)
MIN_TOPICS = 12                    # сколько тем карта показывает самое меньшее; для длинной встречи — больше (см. max_topics)
MIN_SEGMENT_S = 15                 # отрезок короче — шум
MERGE_GAP_S = 60                   # отрезки одной темы ближе этого сливаются в один
SIMILAR = 0.6                      # похожесть названий (по основам слов), с которой темы считаются одной
MAX_SOURCES = 6
QUOTE_LEN = 220
OTHER_ID = "t_other"


def max_topics(duration_s: int) -> int:
    """Сколько тем показываем: растёт с длительностью встречи (примерно одна тема на 2,5 минуты), но не меньше 12 и не больше 40. Остальные короткие — в «Прочих обсуждениях» с перечнем."""
    return max(MIN_TOPICS, min(40, duration_s // 150))


@dataclass
class MapLine:
    """Реплика с привязкой к стенограмме: `line` — как её видит модель (часы на стене), `rel` и `end_rel` — секунды от начала встречи, id записи — для перехода к первоисточнику."""
    line: Line
    rel: int
    end_rel: int
    seg_id: int
    user_key: str


@dataclass
class MapRun:
    rejected: int = 0              # темы и отрезки, отвергнутые проверкой
    rejected_reasons: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class MapError(Exception):
    pass


def clock(sec: int) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# --------------------------------------------------------------------------------------------- реплики
def build_lines(rows: list[TranscriptSegment], started: datetime, tz, mask: bool = False) -> list[MapLine]:
    """Реплики встречи в том виде, в каком их видит модель при составлении протокола: время — часы на стене в часовом поясе стенограммы, текст в одну строку.
    Так фрагменты карты и протокола совпадают, и ответы модели можно использовать повторно. Для карты к ним добавлены секунды от начала встречи (реплики до начала прижимаются к нулю)."""
    from . import profanity  # noqa: PLC0415

    out: list[MapLine] = []
    for r in sorted(rows, key=lambda r: (r.started_at, r.id)):
        text = " ".join((profanity.maybe(r.text, mask) or "").split())
        if not text:
            continue
        wall = r.started_at.astimezone(tz)
        abs_s = wall.hour * 3600 + wall.minute * 60 + wall.second
        rel = max(0, int((r.started_at - started).total_seconds()))
        end = max(rel, int((r.ended_at - started).total_seconds()))
        who = author_name(r) or "Неизвестный участник"
        out.append(MapLine(Line(len(out), clock(abs_s), abs_s, who, text), rel, end, r.id, who))
    return out


def speaker_seconds(lines: list[MapLine]) -> dict[str, int]:
    """Сколько каждый говорил (по длительностям реплик из стенограммы; не по оценке модели)."""
    t: dict[str, int] = {}
    for ml in lines:
        t[ml.line.speaker] = t.get(ml.line.speaker, 0) + max(1, ml.end_rel - ml.rel)
    return t


# --------------------------------------------------------------------------------------------- тексты и идентификаторы
_WS = re.compile(r"\s+")


def clean_text(s: object, limit: int) -> str:
    """Текст от модели: без управляющих символов и разметки-обёрток, ограниченной длины. Показывается как обычный текст."""
    t = _WS.sub(" ", str(s or "")).strip(" \t\"'«»`*#-—")
    t = re.sub(r"[<>]", "", t)
    return t[:limit].rstrip()


def title_key(title: str) -> frozenset[str]:
    return frozenset(w for w in _stems(title) if len(w) >= 3)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    inter = len(a & b)
    return inter / len(a | b) >= SIMILAR or (inter / min(len(a), len(b)) >= 0.99 and min(len(a), len(b)) >= 2)


def topic_id(title: str) -> str:
    """Устойчивый идентификатор темы внутри карты: по нормализованному названию. Правки пользователя привязаны к нему и переживают пересоздание карты."""
    base = " ".join(sorted(title_key(title))) or norm(title)
    return "t_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]


def topics_from_items(items: list[Item], lines: list[MapLine], clock_offset: int, run: MapRun) -> list[dict]:
    """Темы, найденные общим этапом извлечения (уже проверенные: название, время начала и конца — реальные реплики), → записи для слияния с секундами от начала встречи.
    Конец темы — конец названной реплики; не назван — перед началом следующей темы (но не дольше двух минут)."""
    topics = sorted((i for i in items if i.kind == "topic"), key=lambda i: i.sec)
    by_idx = {m.line.idx: m for m in lines}
    out: list[dict] = []
    for n, it in enumerate(topics):
        first = by_idx.get(it.sources[0].get("idx", -1)) if it.sources else None
        if first is None:
            run.rejected += 1
            run.rejected_reasons["время вне стенограммы"] = run.rejected_reasons.get("время вне стенограммы", 0) + 1
            continue
        last = by_idx.get(it.end_idx) if it.end_idx is not None else None
        if last is None or last.rel < first.rel:
            nxt = topics[n + 1].sec if n + 1 < len(topics) else None
            cand = [m for m in lines if m.rel >= first.rel and (nxt is None or m.line.sec < nxt) and m.rel <= first.rel + 120]
            last = cand[-1] if cand else first
        title = clean_text(it.text, 80)
        if len(title) < 3:
            run.rejected += 1
            run.rejected_reasons["без названия"] = run.rejected_reasons.get("без названия", 0) + 1
            continue
        out.append({"title": title, "summary": clean_text(it.detail, 420), "category": it.category if it.category in CAT_LABEL else "general",
                    "first": first.line.idx, "last": last.line.idx, "start_s": first.rel, "end_s": max(last.end_rel, last.rel)})
    return out


# --------------------------------------------------------------------------------------------- слияние и построение карты
def merge_topics(found: list[dict]) -> list[dict]:
    """Одна и та же тема в разных местах (в т.ч. в разных фрагментах) — ОДНА тема с несколькими отрезками."""
    groups: list[dict] = []
    for t in found:
        key = title_key(t["title"])
        g = next((g for g in groups if similar(g["key"], key)), None)
        if g is None:
            g = {"key": key, "title": t["title"], "summaries": [], "cats": {}, "segs": []}
            groups.append(g)
        if t["summary"] and t["summary"] not in g["summaries"]:
            g["summaries"].append(t["summary"])
        dur = max(1, t["end_s"] - t["start_s"])
        g["cats"][t["category"]] = g["cats"].get(t["category"], 0) + dur
        g["segs"].append({"start_s": t["start_s"], "end_s": t["end_s"], "first": t["first"], "last": t["last"]})
    return groups


def resolve_overlaps(groups: list[dict], run: MapRun) -> None:
    """Темы на шкале не накладываются. Модель часто называет широкую тему и узкие внутри неё: на каждом участке времени остаётся самый узкий из покрывающих
    его отрезков (конкретное важнее общего), широкая тема при этом делится. Мелкие участки (шум) удаляются, соседние участки одной темы сливаются."""
    segs = [(s["start_s"], s["end_s"], gi, s) for gi, g in enumerate(groups) for s in g["segs"] if s["end_s"] > s["start_s"]]
    for g in groups:
        g["segs"] = []
    points = sorted({p for a, b, _g, _s in segs for p in (a, b)})
    pieces: list[tuple[int, int, int, dict]] = []
    for a, b in zip(points, points[1:]):
        cover = [x for x in segs if x[0] <= a and x[1] >= b]
        if cover:
            best = min(cover, key=lambda x: (x[1] - x[0], x[0]))
            pieces.append((a, b, best[2], best[3]))
    for g in groups:
        gi = groups.index(g)
        mine = sorted((p for p in pieces if p[2] == gi), key=lambda p: p[0])
        others = [p for p in pieces if p[2] != gi]
        merged: list[dict] = []
        for a, b, _gi, src in mine:
            # соседние участки одной темы сливаются, только если между ними нет участков других тем
            if merged and a - merged[-1]["end_s"] <= MERGE_GAP_S and not any(o[0] < a and o[1] > merged[-1]["end_s"] for o in others):
                merged[-1]["end_s"] = b
                merged[-1]["last"] = max(merged[-1]["last"], src["last"])
            else:
                merged.append({"start_s": a, "end_s": b, "first": src["first"], "last": src["last"]})
        kept = [m for m in merged if m["end_s"] - m["start_s"] >= MIN_SEGMENT_S]
        dropped = len(merged) - len(kept)
        if dropped:
            run.rejected += dropped
            run.rejected_reasons["слишком короткий участок"] = run.rejected_reasons.get("слишком короткий участок", 0) + dropped
        g["segs"] = kept
    groups[:] = [g for g in groups if g["segs"]]


def cap_topics(groups: list[dict], run: MapRun, limit: int = MIN_TOPICS) -> None:
    """Самые короткие темы сверх предела собираются в «Прочие обсуждения». Ничего не теряется: у «Прочих» остаются все отрезки времени, а их перечень (название, время, суть) сохранён в `members`."""
    if len(groups) <= limit:
        return
    ranked = sorted(groups, key=lambda g: -sum(s["end_s"] - s["start_s"] for s in g["segs"]))
    keep, rest = ranked[:limit - 1], ranked[limit - 1:]
    members = [{"title": g["title"], "summary": " ".join(g["summaries"][:1])[:220], "start_s": min(s["start_s"] for s in g["segs"]), "end_s": max(s["end_s"] for s in g["segs"])}
               for g in sorted(rest, key=lambda g: min(s["start_s"] for s in g["segs"]))]
    other = {"key": frozenset(), "title": "Прочие обсуждения", "summaries": ["Короткие темы, не вошедшие в основной перечень: " + "; ".join(m["title"] for m in members[:12]) + ("…" if len(members) > 12 else "") + "."],
             "cats": {"general": 1}, "segs": [s for g in rest for s in g["segs"]], "other": True, "members": members}
    run.warnings.append(f"Тем получилось больше {limit}: {len(rest)} самых коротких собраны в «Прочие обсуждения» (их перечень виден в карточке).")
    order = {id(g): i for i, g in enumerate(groups)}
    groups[:] = sorted(keep, key=lambda g: order[id(g)]) + [other]


def _inside(sec: int, segs: list[dict]) -> bool:
    return any(s["start_s"] <= sec < s["end_s"] for s in segs)


def build_map(meeting: Meeting, tz_name: str, lines: list[MapLine], groups: list[dict], structured: dict | None, clock_offset: int | None, unprocessed: list[dict] | None = None) -> dict:
    """Итоговый JSON карты. Время участников и источники берутся из стенограммы; решения, поручения и вопросы — из проверенных пунктов общего этапа извлечения."""
    topics: list[dict] = []
    for g in groups:
        segs = sorted(g["segs"], key=lambda s: s["start_s"])
        total = sum(s["end_s"] - s["start_s"] for s in segs)
        sp: dict[str, int] = {}
        sources: list[dict] = []
        for s in segs:
            for ml in lines:
                if s["start_s"] <= ml.rel < s["end_s"]:
                    sp[ml.line.speaker] = sp.get(ml.line.speaker, 0) + max(1, ml.end_rel - ml.rel)
            first = next((m for m in lines if m.rel >= s["start_s"]), None)
            if first is not None and len(sources) < MAX_SOURCES:
                sources.append({"sec": first.rel, "speaker": first.line.speaker, "quote": first.line.text[:QUOTE_LEN], "segment_id": first.seg_id})
        cat = max(g["cats"], key=lambda c: g["cats"][c]) if g["cats"] else "general"
        title = g["title"]
        summary = " ".join(g["summaries"][:3])[:520]
        t = {"id": OTHER_ID if g.get("other") else topic_id(title), "ai_title": title, "title": title, "summary": summary, "category": cat, "ai_category": cat, "total_s": total,
             "segments": [{"start_s": s["start_s"], "end_s": s["end_s"]} for s in segs],
             "speakers": [{"name": n, "seconds": v} for n, v in sorted(sp.items(), key=lambda kv: -kv[1])],
             "sources": sources, "decisions": [], "tasks": [], "questions": [], "related": [], "basis": "ai"}
        if g.get("members"):
            t["members"] = g["members"]
        topics.append(t)
    # одинаковые идентификаторы (редкая коллизия) различаем суффиксом
    seen: dict[str, int] = {}
    for t in topics:
        n = seen.get(t["id"], 0)
        seen[t["id"]] = n + 1
        if n:
            t["id"] += f"_{n + 1}"
    # соседи по времени: тема непосредственно до/после
    spans = sorted(((s["start_s"], s["end_s"], t["id"]) for t in topics for s in t["segments"]))
    rel: dict[str, list[str]] = {t["id"]: [] for t in topics}
    for (a0, a1, ai), (b0, b1, bi) in zip(spans, spans[1:]):
        if ai != bi and b0 - a1 <= 120:
            for x, y in ((ai, bi), (bi, ai)):
                if y not in rel[x]:
                    rel[x].append(y)
    for t in topics:
        t["related"] = rel[t["id"]][:4]
    unassigned = {"decisions": [], "tasks": [], "questions": []}
    if structured and clock_offset is not None:
        _attach_items(topics, structured, clock_offset, unassigned)
    total_dur = max((m.end_rel for m in lines), default=0)
    if meeting.ended_at:
        total_dur = max(total_dur, int((meeting.ended_at - meeting.started_at).total_seconds()))
    covered = sum(t["total_s"] for t in topics)
    spk = speaker_seconds(lines)
    spk_total = sum(spk.values()) or 1
    used = sorted({t["category"] for t in topics}, key=CAT_IDS.index)
    return {"version": MAP_VERSION,
            "meeting": {"title": meeting.room.name, "started_at": meeting.started_at.isoformat(), "ended_at": meeting.ended_at.isoformat() if meeting.ended_at else None,
                        "duration_s": total_dur, "timezone": tz_name, "participants": len(spk)},
            "categories": [{"id": c, "label": CAT_LABEL[c]} for c in used],
            "topics": topics,
            "speakers": [{"name": n, "seconds": v, "share": round(v / spk_total, 4)} for n, v in sorted(spk.items(), key=lambda kv: -kv[1])],
            "uncovered_s": max(0, total_dur - covered),
            "unprocessed": unprocessed or [],
            "unassigned": unassigned,
            "items_ready": bool(structured is not None)}


def to_offset(ts: str, clock_offset: int) -> int | None:
    """Время реплики из протокола (часы на стене, ЧЧ:ММ:СС) → секунды от начала встречи. clock_offset — секунды суток начала встречи в часовом поясе стенограммы."""
    m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", (ts or "").strip())
    if not m:
        return None
    return (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) - clock_offset) % 86400


def _attach_items(topics: list[dict], st: dict, clock_offset: int, unassigned: dict) -> None:
    """Решения, поручения и открытые вопросы (каждый пункт уже проверен по тексту и имеет реплику-основание) → к темам по времени реплики."""
    def put(kind: str, item: dict, sec: int | None) -> None:
        if sec is None:
            return
        for t in topics:
            if _inside(sec, t["segments"]):
                t[kind].append(item)
                return
        unassigned[kind].append(item)

    for d in st.get("decisions") or []:
        src = d.get("source") or {}
        put("decisions", {"text": clean_text(d.get("text"), 300), "sec": to_offset(src.get("ts", ""), clock_offset), "speaker": src.get("speaker"),
                          "quote": clean_text(src.get("quote"), QUOTE_LEN), "basis": "source"}, to_offset(src.get("ts", ""), clock_offset))
    for k in st.get("tasks") or []:
        src = k.get("source") or {}
        sec = to_offset(src.get("ts", ""), clock_offset)
        put("tasks", {"text": clean_text(k.get("task") or k.get("text"), 300), "assignee": k.get("assignee") if k.get("assignee_verified", True) else None,
                      "deadline": k.get("deadline_phrase") or k.get("deadline"), "sec": sec, "speaker": src.get("speaker"),
                      "quote": clean_text(src.get("quote"), QUOTE_LEN), "basis": "source"}, sec)
    for q in st.get("open_questions") or []:
        src = q.get("source") or {}
        sec = to_offset(src.get("ts", ""), clock_offset)
        put("questions", {"text": clean_text(q.get("text"), 300), "sec": sec, "speaker": src.get("speaker"), "quote": clean_text(src.get("quote"), QUOTE_LEN),
                          "basis": "source"}, sec)
    for kind in ("decisions", "tasks", "questions"):
        unassigned[kind] = unassigned[kind][:30]


async def analyse(llm: LlmClient, lines: list[MapLine], *, header: list[str], clock_offset: int, model: str, cache: FactsCache | None = None, limit: int = CHUNK_CHARS,
                  local: bool = True, cap: int | None = None, duration_s: int = 0) -> tuple[list[dict], MapRun, Extraction, dict]:
    """Чистая часть конвейера (без базы): реплики → общий этап извлечения → темы с отрезками + проверенные решения/поручения/вопросы. Проверяется тестами с поддельной моделью."""
    run = MapRun()
    ext = await extract(llm, header=header, lines=[m.line for m in lines], limit=limit, model=model, cache=cache, local=local, cap=cap)
    if ext.res.ok_fragments == 0:
        raise MapError("Модель не вернула разбираемый ответ ни для одного фрагмента стенограммы")
    found = topics_from_items(ext.items, lines, clock_offset, run)
    groups = merge_topics(found)
    resolve_overlaps(groups, run)
    cap_topics(groups, run, max_topics(duration_s or max((m.end_rel for m in lines), default=0)))
    if not groups:
        raise MapError("Не удалось выделить темы: все ответы модели отвергнуты проверкой")
    structured = to_structured(merge_items(ext.items))
    if ext.res.failed or ext.uncovered:
        n = sum(u["lines"] for u in ext.uncovered)
        run.warnings.append(f"Не удалось разобрать около {round(100 * n / max(1, len(lines)))}% стенограммы — на карте возможны пропуски (они отмечены на шкале).")
    if run.rejected:
        why = ", ".join(f"{k} — {v}" for k, v in run.rejected_reasons.items())
        run.warnings.append(f"Проверка отвергла {run.rejected} тем и отрезков ({why}).")
    return groups, run, ext, structured


def unprocessed_ranges(ext: Extraction, lines: list[MapLine]) -> list[dict]:
    """Участки стенограммы, которые не удалось разобрать, — в секундах от начала встречи (отметка на шкале карты)."""
    by_ts: dict[str, MapLine] = {}
    for m in lines:
        by_ts.setdefault(m.line.ts, m)
    out = []
    for u in ext.uncovered:
        a, b = by_ts.get(u["from"]), by_ts.get(u["to"])
        if a is not None and b is not None:
            out.append({"start_s": a.rel, "end_s": max(b.end_rel, b.rel), "why": u["why"]})
    return out


# --------------------------------------------------------------------------------------------- пользовательские правки
def apply_edits(data: dict, edits: dict | None) -> dict:
    """Карта для показа: ответ модели + правки пользователя (хранятся отдельно, ответ модели не затирается)."""
    out = {**data, "topics": []}
    edits = edits or {}
    for t in data.get("topics", []):
        e = edits.get(t["id"]) or {}
        t2 = dict(t)
        if e.get("title"):
            t2["title"] = e["title"]
        if e.get("category") in CAT_LABEL:
            t2["category"] = e["category"]
        if e.get("note"):
            t2["note"] = e["note"]
        t2["edited"] = bool(e)
        out["topics"].append(t2)
    used = sorted({t["category"] for t in out["topics"]}, key=CAT_IDS.index)
    out["categories"] = [{"id": c, "label": CAT_LABEL[c]} for c in used]
    return out


def clean_edit(raw: dict) -> dict:
    """Проверка правки от клиента: название до 120 знаков, категория из списка, заметка до 1000. Пустое значение снимает правку."""
    out: dict = {}
    if "title" in raw:
        v = clean_text(raw["title"], 120)
        out["title"] = v or None
    if "category" in raw:
        if raw["category"] not in CAT_LABEL and raw["category"] is not None:
            raise ValueError("Категория: одна из " + ", ".join(CAT_IDS))
        out["category"] = raw["category"]
    if "note" in raw:
        v = _WS.sub(" ", str(raw["note"] or "")).strip()[:1000]
        out["note"] = v or None
    return out


# --------------------------------------------------------------------------------------------- сервис
def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


class MapService:
    """Формирование карт в фоне. Тяжёлые задачи идут по одной (очередь): сервер без GPU не должен считать десять карт одновременно."""

    def __init__(self, ps):
        self.ps = ps                      # ProtocolService: сессии, настройки, выбор модели, фоновые задачи
        self._sem = asyncio.Semaphore(1)
        self._active: set[uuid.UUID] = set()      # карты, которые этот процесс сейчас ставил в очередь или строит
        self.journal = None

    # ------------------------------------------------------------------ состояние
    async def get(self, db: AsyncSession, meeting_id: uuid.UUID) -> ConversationMap | None:
        return (await db.execute(select(ConversationMap).where(ConversationMap.meeting_id == meeting_id))).scalars().first()

    async def request(self, db: AsyncSession, meeting_id: uuid.UUID, actor: str, once: str | None = None) -> ConversationMap:
        """Создаёт (или сбрасывает) запись «в очереди». Пользовательские правки и ПРЕЖНЯЯ готовая карта сохраняются: пока строится новая, видна старая,
        а при сбое остаётся она (`_fail`). `once` — модель на один раз (local:… | profile:…), выбранная руководителем. Запуск — start()."""
        rec = await self.get(db, meeting_id)
        now = utcnow()
        if rec is None:
            rec = ConversationMap(meeting_id=meeting_id, status="pending", created_by=actor)
            db.add(rec)
        else:
            keep = self._keep(rec)
            rec.status, rec.error, rec.created_by = "pending", None, actor
            rec.meta = {"requested_at": now.isoformat(), **({"keep": keep} if keep else {}), **({"llm_once": once} if once else {})}
            return await self._commit_request(db, meeting_id, rec)
        rec.meta = {"requested_at": now.isoformat(), **({"llm_once": once} if once else {})}
        return await self._commit_request(db, meeting_id, rec)

    @staticmethod
    def _keep(rec: ConversationMap) -> dict | None:
        """Метаданные последней ГОТОВОЙ карты (если у записи есть данные): возвращаются, если новое построение не удалось."""
        m = rec.meta or {}
        if m.get("keep"):
            return m["keep"]
        return {k: v for k, v in m.items() if k not in ("previous", "regen_failed")} if rec.data and rec.status == "ready" else None

    async def _commit_request(self, db: AsyncSession, meeting_id: uuid.UUID, rec: ConversationMap) -> ConversationMap:
        try:
            await db.commit()
        except IntegrityError:                                 # два одновременных запроса «построить карту»: запись уже создана другим — используем её (не 500)
            await db.rollback()
            rec = await self.get(db, meeting_id)
            if rec is None:
                raise
        return rec

    async def start(self, map_id: uuid.UUID) -> None:
        if map_id in self._active or not await self.ps.claim(f"map-{map_id}"):     # уже запущено здесь или в другом процессе: второго задания не создаём
            return
        self._active.add(map_id)
        self.ps.spawn(self.run(map_id), f"map-{map_id}")

    async def refresh(self, db: AsyncSession, rec: ConversationMap | None) -> ConversationMap | None:
        """Запись «в очереди/строится», задания которой нет ни в одном процессе (сервис перезапускали), — не вечное «Обрабатывается», а понятная ошибка."""
        if rec is not None and rec.status in ("pending", "running") and rec.id not in self._active and not await self.ps.job_alive(f"map-{rec.id}"):
            msg = "Формирование прервано перезапуском сервиса. Нажмите «Переформировать»."
            keep = (rec.meta or {}).get("keep")
            if keep and rec.data:                      # прежняя карта остаётся готовой
                rec.status, rec.error, rec.meta = "ready", None, {**keep, "regen_failed": {"error": msg, "at": utcnow().isoformat(), "interrupted": True}}
            else:
                rec.status, rec.error = "failed", msg
                rec.meta = {**(rec.meta or {}), "failed": True, "interrupted": True}
            await db.commit()
        return rec

    async def maybe_auto(self, meeting_id: uuid.UUID) -> None:
        """После завершения встречи: если для комнаты (или системно) включено автоформирование — ставит карту в очередь."""
        async with self.ps._sm() as db:
            meeting = await db.get(Meeting, meeting_id)
            if meeting is None or meeting.ended_at is None:
                return
            mode = meeting.room.auto_map_mode
            want = mode == "on" or (mode != "off" and bool((await self.ps._svc.get(db, "protocol")).auto_map))      # type: ignore[attr-defined]
            if not want or not await self.ps.has_materials(meeting_id):
                return
            rec = await self.request(db, meeting_id, "auto")
        await self.start(rec.id)

    # ------------------------------------------------------------------ выполнение
    async def run(self, map_id: uuid.UUID) -> None:
        try:
            async with self.ps.heavy("map"):
                await self._run(map_id)
        finally:
            self._active.discard(map_id)
        await self._notify(map_id)

    async def _notify(self, map_id: uuid.UUID) -> None:
        """События document.ready / document.failed для подписчиков публичного API; сбой уведомления карту не затрагивает."""
        hook = self.ps.on_document
        if hook is None:
            return
        try:
            async with self.ps._sm() as db:
                rec = await db.get(ConversationMap, map_id)
                meeting = await db.get(Meeting, rec.meeting_id) if rec else None
                if rec is not None and meeting is not None and rec.status in ("ready", "failed"):
                    hook("map", rec.id, meeting.id, meeting.room_id, rec.status, rec.error)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось передать событие карты")

    async def _run(self, map_id: uuid.UUID) -> None:
        """Три коротких этапа с базой и один долгий БЕЗ неё: (1) пометить «в работе»; (2) прочитать всё нужное и ОТПУСТИТЬ соединение; (3) запрос к языковой модели — без транзакции;
        (4) новая короткая транзакция: сохранить результат. Раньше сессия жила весь этап LLM (минуты) и держала соединение PostgreSQL «idle in transaction»."""
        async with self.ps._sm() as db:
            rec = await db.get(ConversationMap, map_id)
            if rec is None:
                return
            requested = rec.meta.get("requested_at") if rec.meta else None
            carry = {k: rec.meta[k] for k in ("keep", "llm_once") if rec.meta and rec.meta.get(k)}      # прежняя карта и модель «на один раз» переживают смену статусов
            await release(db)
        async with self._sem:                                    # очередь: одна карта за раз
            started = datetime.now(timezone.utc)
            async with self.ps._sm() as db:
                rec = await db.get(ConversationMap, map_id)
                if rec is None:
                    return
                rec.status = "running"
                rec.meta = {"requested_at": requested, "started_at": started.isoformat(), **carry}
                meeting_id = rec.meeting_id
                await db.commit()
            info: dict = {}
            try:
                async with self.ps._sm() as db:                  # этап чтения: всё нужное — в обычные объекты, затем сессия закрывается
                    prep = await self._prepare(db, meeting_id, info, carry.get("llm_once"))
                data, meta = await self._compute(prep, info)    # LLM: ни одной транзакции PostgreSQL
                meta.update(requested_at=requested, started_at=started.isoformat(), finished_at=utcnow().isoformat(), llm_once=carry.get("llm_once"))
                meta["duration_s"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
                async with self.ps._sm() as db:
                    rec = await db.get(ConversationMap, map_id)
                    if rec is None:
                        return
                    if rec.data and carry.get("keep"):                    # предыдущую готовую карту можно вернуть («Вернуть прежнюю карту»)
                        meta["previous"] = {"data": rec.data, "meta": carry["keep"]}
                    rec.data, rec.meta, rec.status, rec.error = data, meta, "ready", None
                    await db.commit()
                self._emit("map_ready", rec, level="info", data={k: meta.get(k) for k in ("model", "chunks", "retries", "duration_s", "topics")})
            except (LlmError, MapError, ValueError) as exc:
                msg = exc.describe() if isinstance(exc, LlmError) else str(exc)
                await self._fail(map_id, msg[:480], {**self._base_meta(requested, started, info), "failed": True}, "map_failed", carry.get("keep"))
            except Exception:  # noqa: BLE001
                log.exception("Ошибка формирования карты разговора", extra={"map": str(map_id)})
                await self._fail(map_id, "Внутренняя ошибка (см. журнал сервера)", {**self._base_meta(requested, started, info), "failed": True}, "map_failed", carry.get("keep"))

    async def _fail(self, map_id: uuid.UUID, error: str, meta: dict, event: str, keep: dict | None = None) -> None:
        async with self.ps._sm() as db:
            rec = await db.get(ConversationMap, map_id)
            if rec is None:
                return
            if keep and rec.data:        # новое построение не удалось, а прежняя карта есть: она остаётся готовой, сбой показывается рядом (и в диагностике)
                rec.status, rec.error, rec.meta = "ready", None, {**keep, "regen_failed": {"error": error, "at": utcnow().isoformat(), "model": meta.get("model"), "llm_profile": meta.get("llm_profile")}}
            else:
                rec.status, rec.error, rec.meta = "failed", error, meta
            await db.commit()
        self._emit(event, rec, level="error", message=error)

    @staticmethod
    def _base_meta(requested: str | None, started: datetime, info: dict) -> dict:
        m = {k: info[k] for k in ("model", "model_title", "llm_local", "llm_profile") if k in info}
        return {**m, "requested_at": requested, "started_at": started.isoformat(), "finished_at": utcnow().isoformat(),
                "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 1)}

    def _emit(self, event: str, rec: ConversationMap, *, level: str = "info", message: str | None = None, data: dict | None = None) -> None:
        if self.journal is not None:
            self.journal.emit("llm", event, level=level, meeting_id=str(rec.meeting_id), message=message or event, data={"kind": "map", **(data or {})})

    async def _prepare(self, db: AsyncSession, meeting_id: uuid.UUID, info: dict, once: str | None = None) -> dict:
        """Чтение из базы для построения карты: возвращает обычные данные (без привязки к сессии). После возврата сессия закрывается, соединение свободно."""
        ps = self.ps
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            raise MapError("Встреча не найдена")
        choice = await resolve_llm(ps.profiles, ps.local_llm, db, meeting.room, meeting, "map", once=parse_once(once))
        if not choice.available:
            raise LlmError("unavailable", choice.reason or "")
        eff, is_local = ps.local_llm.effective(choice.settings)
        lm = ps.local_llm.limits(choice.settings)
        info.update(model=eff.model, model_title=lm.title if lm else choice.name, llm_local=is_local, llm_profile=choice.name)
        # Внешняя модель + включённое обезличивание: имена в тексте заменяются, а карта строится по именам участников — пока не поддерживаем (fail closed).
        mode = meeting.room.anonymize_mode if meeting.room.anonymize_mode in ("inherit", "on", "off") else "inherit"
        an = None if mode == "off" else await ps.profiles.resolve(db, "anonymizer", meeting.room)
        if mode == "on" or (mode == "inherit" and not is_local and an is not None and bool(an.settings.enabled)):
            raise MapError("Для этой комнаты включено обезличивание, а карта разговора пока строится только без него (локальной моделью или внешней при выключенном обезличивании).")
        rows = (await db.execute(select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
                                 .order_by(TranscriptSegment.started_at, TranscriptSegment.id))).scalars().unique().all()
        tz = await ps._tz(db)
        lines = build_lines(list(rows), meeting.started_at, tz, meeting.room.mask_profanity)
        if not lines:
            raise MapError("В стенограмме нет реплик — карту строить не из чего")
        start_local = meeting.started_at.astimezone(tz)
        clock_offset = start_local.hour * 3600 + start_local.minute * 60 + start_local.second
        # шапка — та же, что у протокола (название, дата, список участников): от неё зависят запрос к модели и возможность повторно использовать ответы
        from .protocols import render_transcript  # noqa: PLC0415

        names = await ps.participant_names(db, meeting)
        header, _ = parse_transcript(render_transcript(meeting.room.name, meeting.started_at, meeting.ended_at, names, [], tz))
        pr_cfg = await ps._svc.get(db, "protocol")
        limit = min(pr_cfg.max_input_chars, lm.max_input_chars) if lm else pr_cfg.max_input_chars       # type: ignore[attr-defined]
        out_cap, _note = eff.output_limit("map")
        from .protocols import external_fragment_chars  # noqa: PLC0415

        frag_limit = limit if is_local else external_fragment_chars(eff.context_window or 0, out_cap, limit)
        cache = FactsCache(await load_facts(db, meeting_id, eff.model))
        llm = ps.local_llm.client(choice.settings, ca_file=ps._ca(), transport=ps._transports.get("llm"), purpose="map")
        _ = meeting.room, meeting.started_at                         # подгрузить связанное до закрытия сессии
        await release(db)
        return {"meeting": meeting, "choice": choice, "eff": eff, "is_local": is_local, "lines": lines, "tz": tz, "clock_offset": clock_offset, "llm": llm, "header": header,
                "limit": frag_limit, "cache": cache, "cap": min(out_cap, 6000) if not is_local else None}

    async def _compute(self, prep: dict, info: dict) -> tuple[dict, dict]:
        """Обращение к модели и сборка карты — без базы данных."""
        meeting, choice, eff, is_local, lines, tz, clock_offset, llm = (prep[k] for k in ("meeting", "choice", "eff", "is_local", "lines", "tz", "clock_offset", "llm"))
        cache: FactsCache = prep["cache"]
        t0 = datetime.now(timezone.utc)
        duration = max(max((m.end_rel for m in lines), default=0), int((meeting.ended_at - meeting.started_at).total_seconds()) if meeting.ended_at else 0)
        try:
            groups, run, ext, structured = await analyse(llm, lines, header=prep["header"], clock_offset=clock_offset, model=eff.model, cache=cache, limit=prep["limit"], local=is_local,
                                                         cap=prep["cap"], duration_s=duration)
        finally:
            info["llm_s"] = round((datetime.now(timezone.utc) - t0).total_seconds(), 1)
        data = build_map(meeting, getattr(tz, "key", "UTC"), lines, groups, structured, clock_offset, unprocessed_ranges(ext, lines))
        diag = diagnostics(ext, cache, llm=llm)
        st = llm.stats
        res = ext.res
        meta = {"model": eff.model, "model_title": info["model_title"], "llm_profile": choice.name, "llm_source": choice.source, "llm_local": is_local,
                "chunks": ext.frags, "llm_calls": res.calls, "retries": diag["retries"]["length"] + diag["retries"]["format"], "failed_chunks": res.failed,
                "length_hits": st["length"], "finish": dict(st["finish"]), "input_chars": sum(len(m.line.render()) + 1 for m in lines),
                "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens, "tokens_per_s": diag["tokens_per_s"], "llm_s": info["llm_s"],
                "topics": len(data["topics"]), "warnings": run.warnings, "rejected": run.rejected_reasons, "coverage_pct": diag["coverage_pct"], "cache_hits": cache.hits,
                "diag": diag, "facts": res.facts or None}
        return data, meta
