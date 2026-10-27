"""Сравнение конвейеров протокола и карты разговора на настоящей локальной модели (llama.cpp + Qwen3 1.7B) и синтетических стенограммах с известными фактами.

Запуск (из корня репозитория; модель — см. tests/perf/README или `bench_llm_up.sh`):
    ~/pg-work/venv/bin/python tests/perf/llm_pipeline_bench.py --impl baseline --sizes short,medium --what map,protocol --out out.json

Что считается (по каждому запуску): обращений к модели, повторов и их причин, оборванных по длине ответов, неразобранных фрагментов, токены (вход/выход), время модели и общее,
а также ПОЛНОТА и ДОСТОВЕРНОСТЬ по заложенным в стенограмму фактам: найдено решений / поручений (с верным ответственным и сроком) / открытых вопросов, покрыто тем,
«отклонённое предложение стало решением», пункты, не похожие ни на один заложенный факт.
Не заменяет оценку на живых встречах: корпус синтетический, модель одна, процессор общий с другими задачами машины.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# PG_BACKEND — каталог backend, из которого берётся код конвейеров (для сравнения «до»: рабочая копия прежнего коммита, `git worktree add <dir> <commit>`)
sys.path.insert(0, os.environ.get("PG_BACKEND") or os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meeting_corpus as mc  # noqa: E402
from app.integrations.llm import LlmClient, LlmResult  # noqa: E402
from app.services.settings import LlmSettings  # noqa: E402
from app.services import conv_map, extraction as ex  # noqa: E402
from app.services.job_budget import Budget  # noqa: E402

MODEL_ALIAS = "qwen3-1.7b-q4_k_m"


class Recorder(LlmClient):
    """Обычный клиент модели + журнал каждого обращения: когда, сколько, чем кончилось."""

    def __init__(self, cfg, **kw):
        super().__init__(cfg, **kw)
        self.calls: list[dict] = []
        self.t0 = time.monotonic()

    async def complete(self, system, user, **kw) -> LlmResult:
        began = time.monotonic()
        rec = {"at": round(began - self.t0, 1), "in_chars": len(system) + len(user), "max_tokens": kw.get("max_tokens"), "json": kw.get("json_schema") is not None,
               "stage": "summary" if system.startswith(ex.SUMMARY_SYSTEM[:40]) else "extract"}
        try:
            r = await super().complete(system, user, **kw)
        except Exception as exc:  # noqa: BLE001
            rec.update(error=getattr(exc, "code", type(exc).__name__), s=round(time.monotonic() - began, 1))
            self.calls.append(rec)
            raise
        rec.update(s=round(time.monotonic() - began, 1), prompt_tokens=r.prompt_tokens, completion_tokens=r.completion_tokens, finish=r.finish_reason, truncated=r.truncated)
        self.calls.append(rec)
        return r


def make_client(base_url: str, purpose: str) -> Recorder:
    cfg = LlmSettings(enabled=True, provider="local", type="openai_compatible", base_url=base_url.rstrip("/") + "/v1", model=MODEL_ALIAS, api_key="", max_tokens=1500,
                      max_tokens_summary=1500, max_tokens_protocol=1500, max_tokens_map=1500, context_window=8192, temperature=0.0, send_temperature=True, supports_system=True,
                      supports_json=True, timeout=1800, use_corporate_ca=False, allow_http=True)
    return Recorder(cfg, local=True, purpose=purpose)


# ------------------------------------------------------------------------------------------------ оценка по заложенным фактам
def stems_of(s: str) -> set[str]:
    return {ex.norm(w)[:5] for w in __import__("re").findall(r"[А-Яа-яЁёA-Za-z]{3,}", s)}


def hit(text: str, key: str, need: float = 0.5) -> bool:
    ks = [k for k in key.split() if k]
    if not ks:
        return False
    words = stems_of(text)
    got = sum(1 for k in ks if any(w.startswith(k[:5]) or k[:5].startswith(w[:4]) and len(w) >= 4 and w[:4] == k[:4] for w in words))
    return got / len(ks) >= need


def score_protocol(corpus: mc.Corpus, st: dict) -> dict:
    decs = [d["text"] for d in st.get("decisions") or []]
    tasks = st.get("tasks") or []
    qs = [q["text"] for q in st.get("open_questions") or []]
    props = [p["text"] for p in st.get("proposals") or []]
    topics = [(t["text"] + " " + (t.get("detail") or "")) for t in st.get("topics") or []]
    pl_d = [(t.key, k) for t in corpus.topics for k in t.decisions]
    pl_t = [(t.key, w) for t in corpus.topics for w in t.tasks]
    pl_q = [(t.key, k) for t in corpus.topics for k in t.questions]
    pl_r = [(t.key, k) for t in corpus.topics for k in t.rejected]
    d_found = [k for _t, k in pl_d if any(hit(x, k, 0.45) for x in decs)]
    t_found, t_owner_ok, t_due_ok = 0, 0, 0
    for _t, (who, key, due) in pl_t:
        cand = [x for x in tasks if hit(x["text"], key, 0.45)]
        if cand:
            t_found += 1
            t_owner_ok += any(x.get("assignee") == mc.PEOPLE[who] for x in cand)
            t_due_ok += any(x.get("deadline_phrase") and due[:4] in (x.get("deadline_phrase") or "").lower() or x.get("deadline") for x in cand)
    q_found = [k for _t, k in pl_q if any(hit(x, k, 0.45) for x in qs)]
    rej_as_decision = [k for _t, k in pl_r if any(hit(x, k, 0.6) for x in decs)]
    known = [k for _t, k in pl_d] + [w[1] for _t, w in pl_t] + [k for _t, k in pl_q] + [k for _t, k in pl_r]
    extra_decisions = [x for x in decs if not any(hit(x, k, 0.4) for k in known)]
    extra_tasks = [x["text"] for x in tasks if not any(hit(x["text"], k, 0.4) for k in known)]
    topic_found = [t.key for t in corpus.topics if any(hit(x, t.words, 0.25) for x in topics)]
    return {"decisions": f"{len(d_found)}/{len(pl_d)}", "tasks": f"{t_found}/{len(pl_t)}", "tasks_owner_ok": f"{t_owner_ok}/{len(pl_t)}", "tasks_deadline_ok": f"{t_due_ok}/{len(pl_t)}",
            "questions": f"{len(q_found)}/{len(pl_q)}", "topics": f"{len(topic_found)}/{len(corpus.topics)}", "rejected_became_decision": rej_as_decision,
            "extra_decisions": extra_decisions, "extra_tasks": extra_tasks, "n_decisions": len(decs), "n_tasks": len(tasks), "n_questions": len(qs), "n_proposals": len(props),
            "n_topics_items": len(topics),
            "recall": round((len(d_found) + t_found + len(q_found)) / max(1, len(pl_d) + len(pl_t) + len(pl_q)), 3)}


def score_map(corpus: mc.Corpus, data: dict) -> dict:
    """Покрытие шкалы: доля времени каждой заложенной темы, попавшая в отрезки карты, чьё название/резюме похоже на тему."""
    topics = data.get("topics") or []
    out_cov, found = {}, []
    for t in corpus.topics:
        a, b = corpus.topic_span[t.key]
        own = [r["sec"] for r in corpus.rows if r["topic"] == t.key]
        span = max(1, b - a)
        covered = 0
        for mt in topics:
            if not hit(mt["title"] + " " + (mt.get("summary") or ""), t.words, 0.25):
                continue
            for s in mt["segments"]:
                covered += max(0, min(s["end_s"], b) - max(s["start_s"], a))
        out_cov[t.key] = round(min(1.0, covered / span), 2)
        if covered / span >= 0.4:
            found.append(t.key)
    spurious = [mt["title"] for mt in topics if not any(hit(mt["title"] + " " + (mt.get("summary") or ""), t.words, 0.25) for t in corpus.topics)]
    return {"topics_found": f"{len(found)}/{len(corpus.topics)}", "coverage": out_cov, "mean_coverage": round(sum(out_cov.values()) / max(1, len(out_cov)), 2),
            "n_topics": len(topics), "spurious": spurious, "uncovered_s": data.get("uncovered_s"), "decisions": sum(len(t["decisions"]) for t in topics) + len((data.get("unassigned") or {}).get("decisions", [])),
            "tasks": sum(len(t["tasks"]) for t in topics) + len((data.get("unassigned") or {}).get("tasks", []))}


# ------------------------------------------------------------------------------------------------ запуск реализаций
@dataclass
class FakeRoom:
    name: str = "Совещание ИТ-отдела"


@dataclass
class FakeMeeting:
    started_at: datetime
    ended_at: datetime
    room: FakeRoom


def totals(llm: Recorder, wall: float) -> dict:
    ok = [c for c in llm.calls if "error" not in c]
    return {"calls": len(llm.calls), "errors": sum(1 for c in llm.calls if "error" in c), "truncated": sum(1 for c in ok if c.get("truncated")),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in ok), "completion_tokens": sum(c.get("completion_tokens") or 0 for c in ok),
            "llm_s": round(sum(c["s"] for c in llm.calls), 1), "wall_s": round(wall, 1), "finish": {k: sum(1 for c in ok if c.get("finish") == k) for k in {c.get("finish") for c in ok}},
            "stage_calls": {s: sum(1 for c in llm.calls if c["stage"] == s) for s in ("extract", "summary")}}


async def baseline_map(base_url: str, corpus: mc.Corpus) -> dict:
    llm = make_client(base_url, "map")
    lines = []
    for r in corpus.rows:
        nxt = r["sec"] + max(4, len(r["text"]) // 14)
        lines.append(conv_map.MapLine(ex.Line(len(lines), conv_map.clock(r["sec"]), r["sec"], r["speaker"], r["text"]), nxt, len(lines) + 1, r["speaker"]))
    t0 = time.monotonic()
    err = None
    data, run = {}, None
    try:
        groups, run = await conv_map.analyse(llm, lines)
        meeting = FakeMeeting(corpus.started, corpus.started + timedelta(seconds=corpus.duration_s), FakeRoom())
        data = conv_map.build_map(meeting, "UTC", lines, groups, None, 0)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0
    out = {"totals": totals(llm, wall), "error": err, "calls": llm.calls}
    if run is not None:
        out["pipeline"] = {"chunks": run.chunks, "ok_chunks": run.ok_chunks, "failed_chunks": run.failed_chunks, "json_retries": run.json_retries, "length_retries": run.length_retries,
                           "rejected": run.rejected_reasons, "warnings": run.warnings}
        out["quality"] = score_map(corpus, data)
    return out


async def baseline_protocol(base_url: str, corpus: mc.Corpus) -> dict:
    llm = make_client(base_url, "protocol")
    t0 = time.monotonic()
    budget = Budget(target_s=300, max_s=3600)
    err, res = None, None
    try:
        res = await ex.local_pipeline(llm, kind="protocol", instruction="x", text=corpus.text, limit=6000, budget=budget)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    wall = time.monotonic() - t0
    out = {"totals": totals(llm, wall), "error": err, "calls": llm.calls, "over_limit_600s": wall > 600}
    if res is not None:
        st = res.structured or {}
        s = st.get("stats") or {}
        out["pipeline"] = {"parts": res.parts, "ok_fragments": res.ok_fragments, "failed": res.failed, "skipped": res.skipped, "truncated": res.truncated, "stats": s, "warnings": res.warnings}
        out["quality"] = score_protocol(corpus, st)
    return out


IMPLS = {"baseline": {"map": baseline_map, "protocol": baseline_protocol}}


def load_new() -> None:
    try:
        import bench_new  # noqa: PLC0415

        IMPLS["new"] = bench_new.IMPLS
    except ImportError:
        pass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:18080")
    ap.add_argument("--impl", default="baseline")
    ap.add_argument("--sizes", default="short,medium")
    ap.add_argument("--what", default="map,protocol")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    load_new()
    result: dict = {"impl": a.impl, "at": datetime.now(timezone.utc).isoformat(), "runs": {}}
    for size in a.sizes.split(","):
        corpus = mc.build(size)
        for what in a.what.split(","):
            print(f"== {a.impl} / {size} ({corpus.chars} знаков, {corpus.duration_s // 60} мин) / {what}", flush=True)
            r = await IMPLS[a.impl][what](a.base_url, corpus)
            t = r["totals"]
            print(f"   обращений {t['calls']} (ошибок {t['errors']}, оборвано {t['truncated']}), токены {t['prompt_tokens']}+{t['completion_tokens']}, "
                  f"время {t['wall_s']} с; {r.get('error') or ''}", flush=True)
            if "quality" in r:
                print("   качество:", json.dumps({k: v for k, v in r["quality"].items() if k not in ("extra_decisions", "extra_tasks", "spurious", "coverage")}, ensure_ascii=False), flush=True)
            result["runs"][f"{size}/{what}"] = r
            if a.out:
                with open(a.out, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
