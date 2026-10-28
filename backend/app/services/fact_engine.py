"""Общий этап извлечения фактов из стенограммы — для протокола, резюме и карты разговора.

Одна логическая задача — один согласованный результат — минимум обращений к модели:

  стенограмма → фрагменты (по размеру, который помещается в контекст модели) → ОДИН запрос на фрагмент: темы (название, суть, категория, начало, конец), решения и предложения,
  поручения (ответственный + что + срок одним объектом), открытые вопросы → проверка каждого пункта по тексту (extraction.verify_pass) → слияние кодом → документ.

Что отличает от прежнего устройства:
  * Ответ ограничен СХЕМОЙ (число пунктов и длина строк): ответ не может бесконечно расти, а потолок ответа заведомо выше худшего случая — оборванных ответов практически не бывает.
    Прежняя карта просила «1–5 тем по 420 знаков» с потолком в 700 токенов: ответ упирался в потолок, не разбирался, фрагмент повторялся по половинам, и снова упирался.
  * Если ответ всё-таки оборван, принимается то, что успело закончиться (`salvage_json`), а заново обрабатывается только НЕРАЗОБРАННЫЙ ХВОСТ фрагмента, а не весь фрагмент и не обе половины.
  * Не разобранный по формату ответ повторяется один раз по половинам; что не удалось, сообщается как непокрытый участок стенограммы, а не скрывается.
  * Проверенные ответы модели по фрагментам сохраняются вместе с документом (`meta.facts`) и используются повторно другим документом той же встречи, если его запрос побайтно совпал
    (тот же текст фрагмента, участники, схема и модель). Ни один документ не зависит от другого: нет кэша — извлечение выполняется само.
  * Диагностика (обращения, повторы по причинам, токены, покрытие) собирается в `Result.diag` и показывается только администратору.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field

from ..integrations.llm import LlmClient, LlmError
from .deadlines import meeting_date
from .extraction import NONE_ID, SUMMARY_MAX, TASKS, Item, Line, Person, Result, _assemble, _parse, participants_of, parse_transcript, people_of, system_for, verify_pass
from .job_budget import TAIL_RESERVE_S, Budget, JobTimeout
from .topic_categories import CATEGORIES, CAT_IDS

log = logging.getLogger("app.fact_engine")

SCHEMA_VERSION = 3            # меняется вместе со схемой/промптом: старые сохранённые ответы перестают подходить
LOCAL_OUT_CAP = 2200          # потолок ответа на фрагмент для локальной модели: выше худшего случая по схеме, контекст 8192 это допускает
MAX_SPLIT_DEPTH = 2           # деление пополам / досчёт хвоста — не более двух уровней подряд
MIN_TAIL_LINES = 3            # хвост короче не стоит отдельного обращения
MIN_SPLIT_LINES = 4
CALLS_LOG_MAX = 300
FACTS_KEEP_BYTES = 200_000    # больше в документ не сохраняем: кэш — удобство, а не обязательство; meta читается при каждом показе списка документов
FACTS_SOURCES = 3             # у скольких последних документов встречи берём сохранённые ответы


# --------------------------------------------------------------------------------------------- схема и промпт
@dataclass(frozen=True)
class Limits:
    topics: int
    items: int
    tasks: int
    questions: int


def limits_for(chars: int) -> Limits:
    """Сколько пунктов каждого вида просим у модели на фрагмент данного размера: растёт с размером, но ограничено (иначе ответ бесконечно длинный)."""
    return Limits(topics=min(30, max(2, 1 + chars // 1500)), items=min(40, max(3, 1 + chars // 1200)), tasks=min(40, max(3, 1 + chars // 1000)), questions=min(20, max(2, 1 + chars // 2000)))


def combined_schema(ids: list[str], lim: Limits) -> dict:
    """Одна схема на все четыре списка: фрагмент обрабатывается ОДНИМ обращением. Число пунктов и длина строк ограничены — ответ заведомо укладывается в потолок."""
    def s(n: int) -> dict:
        return {"type": "string", "maxLength": n}

    def arr(props: dict, n: int) -> dict:
        return {"type": "array", "maxItems": n, "items": {"type": "object", "properties": props, "required": list(props)}}

    body = {
        "topics": arr({"title": s(90), "summary": s(SUMMARY_MAX), "category": {"type": "string", "enum": CAT_IDS}, "ts": s(12), "end": s(12)}, lim.topics),
        "items": arr({"status": {"type": "string", "enum": ["decision", "proposal"]}, "text": s(200), "ts": s(12)}, lim.items),
        "tasks": arr({"assignee": {"type": "string", "enum": ids + [NONE_ID]}, "task": s(200), "deadline": s(60), "ts": s(12)}, lim.tasks),
        "questions": arr({"text": s(200), "ts": s(12)}, lim.questions),
    }
    return {"type": "object", "properties": body, "required": list(body)}


_CATS = "; ".join(f"{i} ({d})" for i, _l, d in CATEGORIES)


def combined_task(lim: Limits) -> str:
    t = TASKS
    topics = ("topics: темы, которые обсуждались, в порядке обсуждения. title — название темы, 2–6 слов; summary — одно законченное предложение: о чём говорили (технические значения — точно как в репликах); "
              f"category — одна из: {_CATS}; ts — время реплики, где тема началась; end — время последней реплики этой темы.")
    return ("ЗАДАНИЕ: по этому фрагменту заполни четыре списка (нет данных — пустой список). Отвечай кратко, без повторов, только самое существенное: "
            f"не больше {lim.topics} тем, {lim.items} решений и предложений, {lim.tasks} поручений, {lim.questions} вопросов. "
            f"(1) {topics} (2) items: {t['decisions'][0].removeprefix('ЗАДАНИЕ: ')} (3) tasks: {t['tasks'][0].removeprefix('ЗАДАНИЕ: ')} (4) questions: {t['questions'][0].removeprefix('ЗАДАНИЕ: ')}")


# --------------------------------------------------------------------------------------------- спасение оборванного JSON (переехало из extraction)
def salvage_json(text: str) -> dict | None:
    """Оборванный по длине JSON → объект с тем, что успело закончиться. Неполный хвост отбрасывается по последнему закрытому элементу,
    скобки закрываются; пункты, дописанные до обрыва, не пропадают."""
    s = text.strip()
    start = s.find("{")
    if start < 0:
        return None
    s = s[start:]
    stack: list[str] = []
    in_str = esc = False
    cut: tuple[int, list[str]] | None = None
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
            cut = (i + 1, list(stack))
    if cut is None:
        return None
    end, closers = cut
    try:
        d = json.loads(s[:end] + "".join(reversed(closers)))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def rows_of(raw: dict) -> int:
    return sum(len(raw.get(k) or []) for k in ("topics", "items", "tasks", "questions"))


def _sec_of(ts: object) -> int | None:
    m = re.match(r"^\[?(\d{1,2}):(\d{2}):(\d{2})\]?$", str(ts or "").strip())
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def covered_until(raw: dict) -> int | None:
    """До какого времени (секунды суток) дошёл оборванный ответ: наибольшее время среди его пунктов."""
    best: int | None = None
    for k in ("topics", "items", "tasks", "questions"):
        for row in raw.get(k) or []:
            if isinstance(row, dict):
                for f in ("ts", "end"):
                    v = _sec_of(row.get(f))
                    if v is not None and (best is None or v > best):
                        best = v
    return best


def full_lists_until(raw: dict, lim: Limits) -> int | None:
    """Если какой-то список заполнен до предела схемы, модель, вероятно, хотела назвать больше, но грамматика её остановила (лишнее молча теряется с конца).
    Тогда уверенно разобрано только то, что до времени последнего пункта самого короткого из заполненных списков: дальше фрагмент разбирается ещё раз (только хвост)."""
    caps = {"topics": lim.topics, "items": lim.items, "tasks": lim.tasks, "questions": lim.questions}
    until: int | None = None
    for k, cap in caps.items():
        rows = [r for r in raw.get(k) or [] if isinstance(r, dict)]
        if len(rows) < cap:
            continue
        times = [t for r in rows for t in (_sec_of(r.get("ts")), _sec_of(r.get("end"))) if t is not None]
        if times:
            last = max(times)
            until = last if until is None else min(until, last)
    return until


# --------------------------------------------------------------------------------------------- общий кэш проверенных ответов
class FactsCache:
    """Ответы модели по фрагментам. `known` — то, что сохранили другие документы этой встречи; `used` — что пригодилось или появилось в этом запуске (его и сохраняем)."""

    def __init__(self, known: dict | None = None):
        self.known: dict[str, dict] = known or {}
        self.used: dict[str, dict] = {}
        self.hits = 0

    def get(self, key: str) -> dict | None:
        e = self.known.get(key) or self.used.get(key)
        if e is not None:
            self.used[key] = e
        return e

    def put(self, key: str, raw: dict, partial: bool) -> None:
        self.used[key] = {"raw": raw, "partial": partial}

    def export(self, model: str) -> dict | None:
        if not self.used:
            return None
        out = {"v": SCHEMA_VERSION, "model": model, "chunks": self.used}
        return out if len(json.dumps(out, ensure_ascii=False)) <= FACTS_KEEP_BYTES else None


async def load_facts(db, meeting_id, model: str) -> dict[str, dict]:
    """Сохранённые ответы других документов этой встречи, полученные той же моделью по той же схеме. Читается только `meta.facts`; доступ к самим документам не расширяется."""
    from sqlalchemy import select  # noqa: PLC0415

    from ..models import ConversationMap, Protocol  # noqa: PLC0415

    merged: dict[str, dict] = {}
    rows = (await db.execute(select(Protocol.meta).where(Protocol.meeting_id == meeting_id, Protocol.status == "ready").order_by(Protocol.created_at.desc()).limit(FACTS_SOURCES))).scalars().all()
    rows += (await db.execute(select(ConversationMap.meta).where(ConversationMap.meeting_id == meeting_id, ConversationMap.status == "ready"))).scalars().all()
    for meta in rows:
        f = (meta or {}).get("facts")
        if isinstance(f, dict) and f.get("v") == SCHEMA_VERSION and f.get("model") == model and isinstance(f.get("chunks"), dict):
            merged.update({k: v for k, v in f["chunks"].items() if isinstance(v, dict) and isinstance(v.get("raw"), dict)})
    return merged


# --------------------------------------------------------------------------------------------- извлечение
@dataclass
class _Ctx:
    llm: LlmClient
    system: str
    people: list[Person]
    ids: list[str]
    res: Result
    stats: dict
    base_date: object
    cache: FactsCache
    model: str
    cap: int
    budget: Budget | None
    local: bool
    covered: set = field(default_factory=set)         # idx реплик, по которым получен проверенный ответ
    uncovered: list = field(default_factory=list)     # непокрытые участки: [{"from": ts, "to": ts, "lines": n, "why": …}]
    total_lines: int = 0

    def key(self, user: str) -> str:
        h = hashlib.sha256()
        for part in (str(SCHEMA_VERSION), self.model, self.system, user):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:32]


async def _extract_chunk(cx: _Ctx, frag: list[Line], frag_no: int, label: str, depth: int = 0) -> list[Item]:
    chars = sum(len(x.render()) + 1 for x in frag)
    lim = limits_for(chars)
    user = f"Фрагмент {label}:\n\n" + "\n".join(x.render() for x in frag) + f"\n\n=== {combined_task(lim)}"
    key = cx.key(user)
    raw: dict | None = None
    partial = False
    cached = cx.cache.get(key)
    if cached is not None:
        raw, partial = cached["raw"], bool(cached.get("partial"))
        cx.cache.hits += 1
    else:
        truncated = False
        try:
            r = await cx.llm.complete(cx.system, user, max_tokens=cx.cap, json_schema=combined_schema(cx.ids, lim), temperature=0.0)
            cx.res.calls += 1
            cx.res.prompt_tokens += r.prompt_tokens or 0
            cx.res.completion_tokens += r.completion_tokens or 0
            if r.completion_tokens and r.duration_ms > 0:
                cx.res.tokens_per_s = r.completion_tokens / (r.duration_ms / 1000)
            if len(cx.res.calls_log) < CALLS_LOG_MAX:
                cx.res.calls_log.append({"stage": "extract", "depth": depth, "chars": chars, "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                                         "s": round(r.duration_ms / 1000, 1), "finish": r.finish_reason or None, "cap": r.max_tokens})
            truncated = r.truncated
            raw = _parse(r.text) if not truncated else None
            if raw is None and truncated:
                raw = salvage_json(r.text)               # оборванный ответ не выбрасывается: берём то, что успело закончиться
                if raw is not None and rows_of(raw) == 0:
                    raw = None
                if raw is not None:
                    cx.stats["salvaged"] += 1
                    partial = True
        except LlmError as exc:
            if exc.code in ("not_configured", "unavailable", "unauthorized", "forbidden"):
                raise
            cx.stats["call_errors"] = cx.stats.get("call_errors", 0) + 1
            cx.stats.setdefault("error_codes", {})[exc.code] = cx.stats.setdefault("error_codes", {}).get(exc.code, 0) + 1
            log.warning("Извлечение: ошибка вызова модели", extra={"code": exc.code})
        if raw is not None:
            cx.cache.put(key, raw, partial)
        elif truncated:
            cx.stats["length_retries"] += 1
            cx.stats["truncated_lost"] = cx.stats.get("truncated_lost", 0) + 1
        else:
            cx.stats["json_retries"] += 1
    if raw is None:
        # не разобралось по формату (или потерян ответ): один повтор по половинам; тот же текст целиком второй раз не отправляем
        if depth < 1 and len(frag) >= MIN_SPLIT_LINES and (cx.budget is None or cx.budget.remaining() > TAIL_RESERVE_S + 60):
            cx.stats["split_runs"] = cx.stats.get("split_runs", 0) + 1
            mid = len(frag) // 2
            return (await _extract_chunk(cx, frag[:mid], frag_no, f"{label}а", depth + 1)
                    + await _extract_chunk(cx, frag[mid:], frag_no, f"{label}б", depth + 1))
        cx.res.failed += 1
        cx.res.truncated = cx.res.truncated or bool(cx.stats.get("truncated_lost"))
        cx.uncovered.append({"from": frag[0].ts, "to": frag[-1].ts, "lines": len(frag), "why": "модель не вернула разбираемый ответ"})
        return []
    cx.res.ok_fragments += 1
    out: list[Item] = []
    for k in TASKS:
        out += verify_pass(k, raw, frag, frag_no, cx.people, cx.stats, cx.base_date)
    # где остановился ответ: оборван по длине (partial) и/или заполнен до предела схемы; разобрано уверенно только то, что до этого времени
    until_cut = covered_until(raw) if partial else None
    until_full = full_lists_until(raw, lim)
    if until_full is not None:
        cx.stats["lists_full"] = cx.stats.get("lists_full", 0) + 1
    marks = [u for u in (until_cut, until_full) if u is not None]
    if not marks:
        cx.covered.update(x.idx for x in frag)
        return out
    until = min(marks)
    done = [x for x in frag if x.sec <= until]
    tail = [x for x in frag if x.sec > until]
    cx.covered.update(x.idx for x in done)
    tail_chars = sum(len(x.render()) + 1 for x in tail)
    if MIN_TAIL_LINES <= len(tail) < len(frag) and tail_chars >= 0.08 * chars and depth < MAX_SPLIT_DEPTH and (cx.budget is None or cx.budget.remaining() > TAIL_RESERVE_S + 60):
        cx.stats["tail_runs"] = cx.stats.get("tail_runs", 0) + 1
        if until_cut is not None:
            cx.stats["length_retries"] += 1
        return out + await _extract_chunk(cx, tail, frag_no, f"{label}в", depth + 1)
    if tail:
        if len(tail) >= MIN_TAIL_LINES and (until_cut is not None):
            cx.res.truncated = True                       # здесь содержание действительно потеряно: об этом скажет предупреждение документа
            cx.uncovered.append({"from": tail[0].ts, "to": tail[-1].ts, "lines": len(tail), "why": "ответ модели оборван, времени на повтор не осталось"})
        else:
            cx.covered.update(x.idx for x in tail)       # короткий хвост после заполненного списка: не стоит отдельного обращения
    return out


@dataclass
class Extraction:
    header: list[str]
    lines: list[Line]
    people: list[Person]
    items: list[Item]
    res: Result
    stats: dict
    frags: int
    uncovered: list
    covered_lines: int
    skipped: int = 0


def new_stats() -> dict:
    return {"bad_ts": 0, "empty": 0, "fabricated": 0, "reclassified": 0, "assignee_unverified": 0, "assignee_unknown": 0, "due_dropped": 0, "timewords_removed": 0,
            "length_retries": 0, "json_retries": 0, "questions_unsupported": 0, "duplicate_assignee": 0, "assignee_from_text": 0, "salvaged": 0, "tail_runs": 0, "split_runs": 0,
            "lists_full": 0, "call_errors": 0, "truncated_lost": 0, "summary_trimmed": 0}


async def extract(llm: LlmClient, *, header: list[str], lines: list[Line], limit: int, model: str, cache: FactsCache | None = None, budget: Budget | None = None,
                  local: bool = True, cap: int | None = None) -> Extraction:
    """Общий этап: реплики → проверенные пункты (темы, решения, поручения, вопросы). Используется протоколом, резюме и картой разговора."""
    from .extraction import chunk_lines  # noqa: PLC0415

    res = Result()
    people = people_of(participants_of(header, lines))
    ids = [p.pid for p in people]
    frags = chunk_lines(lines, limit)
    res.parts = len(frags)
    stats = new_stats()
    cx = _Ctx(llm, system_for(people), people, ids, res, stats, meeting_date(header), cache or FactsCache(), model, cap or (LOCAL_OUT_CAP if local else 4000), budget, local,
              total_lines=len(lines))
    items: list[Item] = []
    started = budget.elapsed() if budget else 0.0
    if budget is not None:
        budget.progress.update(stage="analysis", done=0, total=len(frags))
    skipped = 0
    for i, frag in enumerate(frags, 1):
        done = i - 1
        if budget is not None:
            budget.progress["done"] = done
            avg = (budget.elapsed() - started) / done if done else 30.0
            if budget.remaining() < TAIL_RESERVE_S + max(avg, 20.0):
                skipped = len(frags) - done
                cx.uncovered.append({"from": frag[0].ts, "to": frags[-1][-1].ts, "lines": sum(len(f) for f in frags[done:]), "why": "не хватило времени задания"})
                break
        items += await _extract_chunk(cx, frag, i, f"{i} из {len(frags)}")
        if i == 1 and res.ok_fragments == 0:
            break                              # первый же фрагмент не разобрался даже после повтора — модель или сервер не держат формат; остальные не мучаем
    res.skipped = skipped
    res.facts = cx.cache.export(model) or {}
    return Extraction(header, lines, people, items, res, stats, len(frags), cx.uncovered, len(cx.covered), skipped)


def diagnostics(ext: Extraction, cache: FactsCache, *, llm: LlmClient | None = None) -> dict:
    """Сводка для администратора: что делала модель и сколько это стоило. Пользователю в документе не показывается."""
    res, st = ext.res, ext.stats
    total = max(1, len(ext.lines))
    d = {"calls": res.calls, "extract_calls": sum(1 for c in res.calls_log if c.get("stage") == "extract"), "chunks": ext.frags, "ok_chunks": res.ok_fragments, "failed_chunks": res.failed, "skipped_chunks": ext.skipped,
         "retries": {"length": st["length_retries"], "format": st["json_retries"], "salvaged": st["salvaged"], "tail": st.get("tail_runs", 0), "split": st.get("split_runs", 0),
                     "call_errors": st.get("call_errors", 0)},
         "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens, "cache_hits": cache.hits, "lines": len(ext.lines), "lines_covered": ext.covered_lines,
         "coverage_pct": round(100 * ext.covered_lines / total, 1), "uncovered": ext.uncovered[:20], "tokens_per_s": round(res.tokens_per_s, 1) if res.tokens_per_s else None,
         "rejected": {k: v for k, v in st.items() if k in ("bad_ts", "empty", "fabricated", "questions_unsupported", "due_dropped", "assignee_unverified", "assignee_unknown", "summary_trimmed") and v},
         "calls_log": res.calls_log}
    if llm is not None:
        d["llm_s"] = round(llm.stats["ms"] / 1000, 1)
        d["finish"] = dict(llm.stats["finish"])
    return d


def coverage_warnings(ext: Extraction) -> list[str]:
    out: list[str] = []
    if ext.res.failed or ext.uncovered:
        n = sum(u["lines"] for u in ext.uncovered)
        pct = round(100 * n / max(1, len(ext.lines)))
        spans = "; ".join(f"{u['from']}–{u['to']}" for u in ext.uncovered[:4])
        out.append(f"Не удалось обработать около {pct}% стенограммы ({spans}{'…' if len(ext.uncovered) > 4 else ''}) — в документе могут быть пропуски. "
                   "Сформируйте документ ещё раз или выберите более сильную (внешнюю) модель.")
    return out


# --------------------------------------------------------------------------------------------- конвейер документа
async def run_pipeline(llm: LlmClient, *, kind: str, instruction: str, text: str, limit: int, budget: Budget | None, attendees: dict | None, local: bool,
                       cache: FactsCache | None = None, model: str = "", cap: int | None = None) -> Result:
    """Протокол или резюме: общий этап извлечения + сборка документа кодом + короткий итоговый абзац (если хватает времени)."""
    header, lines = parse_transcript(text)
    if not lines:
        return Result()
    cache = cache or FactsCache()
    ext = await extract(llm, header=header, lines=lines, limit=limit, model=model, cache=cache, budget=budget, local=local, cap=cap)
    res = ext.res
    if ext.skipped:
        progress = {"done": ext.frags - ext.skipped, "total": ext.frags}
        if kind == "protocol" or progress["done"] == 0:
            raise JobTimeout(f"За отведённое время обработано {progress['done']} из {progress['total']} фрагментов стенограммы: полный протокол не собран.", progress)
    if res.ok_fragments == 0:
        res.diag = diagnostics(ext, cache, llm=llm)
        return res
    if budget is not None:
        budget.progress.update(stage="assemble", done=ext.frags - ext.skipped)
    t0 = time.monotonic()
    out = await _assemble(llm, res, kind=kind, instruction=instruction, header=header, people=ext.people, items=ext.items, stats=ext.stats, n_frags=ext.frags,
                          attendees=attendees, budget=budget, local=local)
    out.warnings = [w for w in out.warnings if not w.startswith("Не удалось выполнить ")] + coverage_warnings(ext)
    out.structured["coverage_lines"] = {"lines": len(lines), "covered": ext.covered_lines, "uncovered": ext.uncovered}
    if ext.uncovered:
        out.structured["complete"] = False
    out.diag = diagnostics(ext, cache, llm=llm)
    out.diag["assemble_s"] = round(time.monotonic() - t0, 1)
    if ext.skipped and out.structured is not None:
        pct = round(100 * (ext.frags - ext.skipped) / ext.frags)
        out.structured["complete"] = False
        out.structured["coverage"] = {"done": ext.frags - ext.skipped, "total": ext.frags, "percent": pct}
        out.warnings.insert(0, f"Резюме неполное: за отведённое время обработано {ext.frags - ext.skipped} из {ext.frags} фрагментов стенограммы (около {pct}%). "
                               "Сформируйте его ещё раз или выберите внешнюю модель.")
    return out
