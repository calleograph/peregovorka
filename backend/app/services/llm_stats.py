"""Статистика генерации документов по моделям, прогноз времени и подпись «как создан документ».

Данные берутся из метаданных уже созданных документов (`protocols.meta`): ничего отдельного не копится. Чем больше документов создано
конкретной моделью, тем точнее прогноз; без истории прогноз — широкий диапазон по грубой оценке (ложной точности нет).
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ConversationMap, Protocol

# Грубые оценки секунд на 1 000 знаков стенограммы, пока нет истории. Локальная модель — по замерам на 4 потоках CPU (HISTORY.md), внешняя — по
# сетевым вызовам; диапазон в прогнозе намеренно широкий.
PRIOR_LOCAL = {"protocol": 22.0, "summary": 24.0}
PRIOR_EXTERNAL = {"protocol": 3.0, "summary": 2.0}
PRIOR_EXTERNAL_BASE_S = 8.0
MIN_HISTORY = 3


def plural_min(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "минута"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "минуты"
    return "минут"


def format_range(low_s: float, high_s: float) -> str:
    """«примерно 3–7 минут» / «меньше минуты» / «более 3 часов»: округление до минут, без ложной точности."""
    if high_s < 60:
        return "меньше минуты"
    lo = max(1, int(low_s // 60))
    hi = max(lo + 1, int(math.ceil(high_s / 60)))
    if lo >= 180:
        return "более 3 часов"
    if hi >= 120:
        h_lo, h_hi = round(lo / 60, 1), round(hi / 60, 1)
        return f"примерно {h_lo:g}–{h_hi:g} ч"
    return f"примерно {lo}–{hi} {plural_min(hi)}"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    if s < 60:
        return f"{s} с"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} мин {s} с" if s else f"{m} мин"
    h, m = divmod(m, 60)
    return f"{h} ч {m} мин"


def group_key(meta: dict) -> tuple[str, bool, str]:
    """Что считается «одной моделью»: идентификатор модели, локальная ли, профиль API (у внешних — один и тот же model может быть у разных шлюзов)."""
    local = bool(meta.get("llm_local"))
    return (str(meta.get("model") or "?"), local, "" if local else str(meta.get("llm_profile") or ""))


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


async def recent_runs(db: AsyncSession, limit: int = 1500) -> list[dict]:
    """Последние документы с метаданными генерации (самые новые первыми)."""
    rows = (await db.execute(select(Protocol.kind, Protocol.status, Protocol.meta, Protocol.created_at)
                             .where(Protocol.kind.in_(("protocol", "summary")), Protocol.meta.is_not(None))
                             .order_by(Protocol.created_at.desc()).limit(limit))).all()
    out = []
    for kind, status, meta, created in rows:
        if isinstance(meta, dict) and meta.get("model"):
            out.append({"kind": kind, "status": status, "meta": meta, "at": created})
    maps = (await db.execute(select(ConversationMap.status, ConversationMap.meta, ConversationMap.updated_at).where(ConversationMap.meta.is_not(None))
                             .order_by(ConversationMap.updated_at.desc()).limit(limit))).all()
    for status, meta, at in maps:                          # карты разговоров — отдельная задача «map» в той же таблице показателей
        if isinstance(meta, dict) and meta.get("model") and status in ("ready", "failed"):
            out.append({"kind": "map", "status": status, "meta": meta, "at": at})
    return out


def forecast(runs: list[dict], *, kind: str, model: str, local: bool, profile: str, chars: int, limit: int) -> dict:
    """Диапазон времени работы модели: по истории этой же модели и задачи (медиана и разброс на 1 000 знаков), а без неё — по грубой оценке."""
    kchars = max(chars, 1) / 1000
    chunks = max(1, math.ceil(chars / max(limit, 1)))
    key = (model, local, "" if local else profile)
    samples = []
    for r in runs:
        m = r["meta"]
        if r["kind"] != kind or r["status"] != "ready" or group_key(m) != key:
            continue
        llm_s, inp = _num(m.get("llm_s")), _num(m.get("input_chars"))
        if llm_s and inp and inp >= 500 and not m.get("failed"):
            samples.append(llm_s / (inp / 1000))
    if len(samples) >= MIN_HISTORY:
        samples.sort()
        n = len(samples)
        q1, q3 = samples[n // 4], samples[(3 * n) // 4 if (3 * n) // 4 < n else n - 1]
        med = statistics.median(samples)
        low, high = min(q1, med) * kchars * 0.85, max(q3, med) * kchars * 1.25
        basis, count = "history", n
    else:
        per = (PRIOR_LOCAL if local else PRIOR_EXTERNAL).get(kind, 20.0)
        base = 0.0 if local else PRIOR_EXTERNAL_BASE_S
        low, high = base + per * kchars * 0.5, base + per * kchars * 2.0
        basis, count = "estimate", len(samples)
    if not local:
        high = max(high, low * 1.5)
    return {"low_s": int(low), "high_s": int(high), "text": format_range(low, high), "basis": basis, "samples": count, "chunks": chunks,
            "note": ("по %d предыдущим документам этой модели" % count) if basis == "history"
            else "грубая оценка: по этой модели ещё нет истории, прогноз уточнится после нескольких документов"}


ARCHIVED_MODELS = ("qwen3-0.6b",)          # снятые с вооружения модели: статистика остаётся, но помечается и по умолчанию скрывается


def is_archived(model_id: str) -> bool:
    return str(model_id or "").lower().startswith(ARCHIVED_MODELS)


def model_stats(runs: list[dict]) -> list[dict]:
    """Таблица по моделям и задачам: сколько документов, ошибок, оборванных; среднее и медианное время; средний вход и выход; токены в секунду."""
    groups: dict[tuple, dict] = {}
    for r in runs:
        m = r["meta"]
        key = (*group_key(m), r["kind"])
        g = groups.setdefault(key, {"model": key[0], "local": key[1], "profile": str(m.get("llm_profile") or ""), "title": str(m.get("model_title") or m.get("llm_profile") or key[0]),
                                    "kind": key[3], "ok": 0, "failed": 0, "truncated": 0, "length_hits": 0, "retries": 0, "_t": [], "_in": [], "_out": [], "_tok": 0, "_tok_s": 0.0,
                                    "last": None})
        if r["status"] == "failed" or m.get("failed"):
            g["failed"] += 1
        else:
            g["ok"] += 1
            llm_s = _num(m.get("llm_s"))
            if llm_s is not None:
                g["_t"].append(llm_s)
                ct = _num(m.get("completion_tokens"))
                if ct and llm_s > 0:
                    g["_tok"] += ct
                    g["_tok_s"] += llm_s
            for src, dst in (("input_chars", "_in"), ("output_chars", "_out")):
                v = _num(m.get(src))
                if v is not None:
                    g[dst].append(v)
        if m.get("truncated"):
            g["truncated"] += 1
        g["length_hits"] += int(_num(m.get("length_hits")) or 0)
        g["retries"] += int(_num(m.get("retries")) or 0)
        if g["last"] is None or (r["at"] and r["at"] > g["last"]):
            g["last"] = r["at"]
    out = []
    for g in groups.values():
        t, i, o = g.pop("_t"), g.pop("_in"), g.pop("_out")
        tok, tok_s = g.pop("_tok"), g.pop("_tok_s")
        g.update(archived=is_archived(g["model"]), documents=g["ok"] + g["failed"], avg_s=round(sum(t) / len(t), 1) if t else None, median_s=round(statistics.median(t), 1) if t else None,
                 avg_input_chars=int(sum(i) / len(i)) if i else None, avg_output_chars=int(sum(o) / len(o)) if o else None,
                 tokens_per_s=round(tok / tok_s, 1) if tok_s else None, last=g["last"].isoformat() if isinstance(g["last"], datetime) else None)
        out.append(g)
    out.sort(key=lambda g: (-g["documents"], g["title"]))
    return out
