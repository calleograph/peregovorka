"""Реализации «после» для llm_pipeline_bench.py: общий этап извлечения (fact_engine) — карта и протокол на одном и том же движке.
Режим `both`: сначала протокол, затем карта по той же встрече с общим кэшем ответов (так бывает в жизни: протокол сформирован, карту строят после), и наоборот."""
from __future__ import annotations

import time
from datetime import timedelta

import meeting_corpus as mc
from llm_pipeline_bench import FakeMeeting, FakeRoom, make_client, score_map, score_protocol, totals
from app.services import conv_map, extraction as ex
from app.services.fact_engine import FactsCache
from app.services.job_budget import Budget
from app.services import fact_engine as fe

MODEL = "qwen3-1.7b-q4_k_m"


def _lines(corpus: mc.Corpus):
    header, plain = ex.parse_transcript(corpus.text)
    out = []
    for i, ln in enumerate(plain):
        r = corpus.rows[i]
        nxt = r["sec"] + max(4, len(r["text"]) // 14)
        out.append(conv_map.MapLine(ln, r["sec"], nxt, i + 1, r["speaker"]))
    return header, out


async def new_map(base_url: str, corpus: mc.Corpus, cache: FactsCache | None = None) -> dict:
    llm = make_client(base_url, "map")
    header, lines = _lines(corpus)
    t0 = time.monotonic()
    err, data, run, ext = None, {}, None, None
    cache = cache or FactsCache()
    try:
        groups, run, ext, structured = await conv_map.analyse(llm, lines, header=header, clock_offset=0, model=MODEL, cache=cache, limit=6000, duration_s=corpus.duration_s)
        meeting = FakeMeeting(corpus.started, corpus.started + timedelta(seconds=corpus.duration_s), FakeRoom())
        # у синтетической встречи часы на стене = секунды от начала + 10:00:00; смещение задаём как у реального сервиса
        data = conv_map.build_map(meeting, "UTC", lines, groups, structured, corpus.started.hour * 3600, conv_map.unprocessed_ranges(ext, lines))
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0
    out = {"totals": totals(llm, wall), "error": err, "calls": llm.calls, "cache_hits": cache.hits}
    if ext is not None:
        d = fe.diagnostics(ext, cache, llm=llm)
        out["pipeline"] = {"chunks": ext.frags, "ok_chunks": ext.res.ok_fragments, "failed_chunks": ext.res.failed, "retries": d["retries"], "coverage_pct": d["coverage_pct"],
                           "rejected": run.rejected_reasons, "warnings": run.warnings, "stats": {k: v for k, v in ext.stats.items() if v}}
        out["quality"] = score_map(corpus, data)
        # решения/поручения карты оцениваем тем же способом, что и протокола: по проверенным пунктам общего этапа
        out["quality_items"] = score_protocol(corpus, structured)
    out["_cache"] = cache
    return out


async def new_protocol(base_url: str, corpus: mc.Corpus, cache: FactsCache | None = None) -> dict:
    llm = make_client(base_url, "protocol")
    t0 = time.monotonic()
    budget = Budget(target_s=300, max_s=3600)
    err, res = None, None
    cache = cache or FactsCache()
    try:
        res = await ex.local_pipeline(llm, kind="protocol", instruction="x", text=corpus.text, limit=6000, budget=budget, cache=cache, model=MODEL)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0
    out = {"totals": totals(llm, wall), "error": err, "calls": llm.calls, "over_limit_600s": wall > 600, "cache_hits": cache.hits}
    if res is not None:
        st = res.structured or {}
        out["pipeline"] = {"parts": res.parts, "ok_fragments": res.ok_fragments, "failed": res.failed, "skipped": res.skipped, "truncated": res.truncated, "stats": st.get("stats"),
                           "warnings": res.warnings, "diag": {k: v for k, v in res.diag.items() if k != "calls_log"}}
        out["quality"] = score_protocol(corpus, st)
    out["_cache"] = cache
    return out


async def both(base_url: str, corpus: mc.Corpus) -> dict:
    """Протокол, затем карта по той же встрече: карта берёт готовые ответы модели (кэш) и не обращается к ней там, где запрос совпал."""
    p = await new_protocol(base_url, corpus)
    cache = FactsCache(dict(p["_cache"].used))
    m = await new_map(base_url, corpus, cache)
    m.pop("_cache", None)
    p.pop("_cache", None)
    return {"protocol": p, "map_after_protocol": m, "totals": {k: p["totals"][k] + m["totals"][k] for k in ("calls", "prompt_tokens", "completion_tokens", "llm_s", "wall_s")}}


async def _strip(coro):
    r = await coro
    r.pop("_cache", None)
    return r


IMPLS = {"map": lambda u, c: _strip(new_map(u, c)), "protocol": lambda u, c: _strip(new_protocol(u, c)), "both": both}
