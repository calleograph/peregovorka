"""Локальная модель: один проход на фрагмент, оборванный ответ спасается, время всего задания ограничено (цель и жёсткий предел), отмена пользователем,
закрытие осиротевших заданий, пользовательские лимиты и инструкции к локальной модели не применяются."""
from __future__ import annotations

import asyncio
import re
import time
import uuid

import httpx
import pytest

from app.models import Protocol
from app.services import extraction as ex
from app.services import protocols as protocols_mod
from app.services.job_budget import Budget, JobTimeout, pass_cap
from app.services.protocols import LOCAL_BUILTIN_INSTRUCTION

from .conftest import login, put_settings
from .test_access_and_protocols import end_by_alice, meeting_with_two
from .test_admin_features import _drain
from .test_extraction import TEXT, llm_for, reply
from .test_local_llm import Runtime, app_with, model_env


# ------------------------------------------------------------------------------------------------ спасение оборванного JSON
def test_salvage_keeps_what_was_finished_before_the_cut():
    cut = ('{"topics":[{"title":"Перенос релиза","summary":"s","ts":"10:00:05"}],"items":[{"status":"decision","text":"Принято первое","ts":"10:00:20"},'
           '{"status":"decision","text":"Принято втор')
    d = ex.salvage_json(cut)
    assert d["topics"][0]["title"] == "Перенос релиза" and d["items"] == [{"status": "decision", "text": "Принято первое", "ts": "10:00:20"}]
    assert ex.salvage_json('{"topics":[{"title":"Об') is None, "ничего не закончилось — спасать нечего"
    assert ex.salvage_json("текст без json") is None and ex.salvage_json("[1,2") is None
    tricky = '{"topics":[{"title":"скобки } и ] внутри \\" строки","ts":"10:00:05"},{"title":"обор'
    assert ex.salvage_json(tricky) == {"topics": [{"title": 'скобки } и ] внутри " строки', "ts": "10:00:05"}]}, "скобки и кавычки внутри строк не ломают разбор"


def test_pass_cap_follows_speed_remaining_time_and_number_of_passes():
    b = Budget(target_s=300, max_s=600)
    assert pass_cap(b, None, 5) == 450, "скорость ещё неизвестна — значение по умолчанию"
    assert pass_cap(b, 7.0, 1) == 600, "верхняя граница"
    assert 180 <= pass_cap(b, 7.0, 8) <= 190, "время делится между оставшимися проходами"
    assert pass_cap(b, 7.0, 500) == 120, "нижняя граница: ответ не бывает бесполезно коротким"
    assert pass_cap(Budget(target_s=0), 7.0, 3) == 120, "цель уже пропущена"


# ------------------------------------------------------------------------------------------------ конвейер
def combined(body, n):
    if "response_format" not in body:
        return reply("Релиз перенесли на вторник; исправление прав будет отдано в четверг.")      # итоговый абзац
    ts = re.search(r"\[(\d\d:\d\d:\d\d)\]", body["messages"][1]["content"]).group(1)
    return reply({"topics": [{"title": f"Тема {n}", "summary": "суть", "ts": ts}], "items": [{"status": "decision", "text": f"Принято решение номер {n}", "ts": ts}],
                  "tasks": [], "questions": []})


def run_local(llm, *, kind="protocol", limit=300, budget=None, text=TEXT):
    return asyncio.run(ex.local_pipeline(llm, kind=kind, instruction="x", text=text, limit=limit, budget=budget or Budget()))


def n_fragments(limit=300):
    _h, lines = ex.parse_transcript(TEXT)
    return len(ex.chunk_lines(lines, limit))


def test_one_call_per_fragment_instead_of_four_and_a_short_summary():
    n = n_fragments()
    assert n >= 2
    llm, seen = llm_for(combined)
    res = run_local(llm)
    assert res.calls == n + 1 and len(seen) == n + 1, "по одному обращению на фрагмент + итоговый абзац (раньше — четыре на фрагмент)"
    passes = seen[:n]
    assert all(set(b["response_format"]["json_schema"]["schema"]["properties"]) == {"topics", "items", "tasks", "questions"} and b["max_tokens"] <= 600 for b in passes)
    assert "response_format" not in seen[-1] and seen[-1]["max_tokens"] <= 220
    assert res.ok_fragments == n and not res.failed and res.structured["stats"]["fragments"] == n


def test_a_cut_off_answer_is_salvaged_instead_of_repeated():
    def cut(body, n):
        if "response_format" not in body:
            return reply("Итог по проверенным пунктам встречи записан.")
        ts = re.search(r"\[(\d\d:\d\d:\d\d)\]", body["messages"][1]["content"]).group(1)
        return reply('{"topics":[{"title":"Перенос релиза","summary":"суть","ts":"' + ts + '"}],"items":[{"status":"decision","text":"Принято реше', finish="length")

    llm, seen = llm_for(cut)
    res = run_local(llm, limit=100000, kind="summary")
    assert res.ok_fragments == 1 and res.failed == 0 and res.truncated is True
    assert res.structured["stats"]["salvaged"] == 1 and res.structured["stats"]["length_retries"] == 0
    assert len([b for b in seen if "response_format" in b]) == 1, "оборванный, но разбираемый ответ не повторяется"


def test_unusable_answers_are_retried_at_most_once_per_fragment():
    llm, seen = llm_for(lambda body, n: reply("{", finish="length"))
    res = run_local(llm, limit=100000)
    assert len(seen) == 3 and res.ok_fragments == 0 and res.failed == 2, "целый фрагмент + две половины; дальше деления нет (раньше до семи обращений)"


class StepBudget(Budget):
    """Время «кончается» после первого фрагмента."""

    def __init__(self):
        super().__init__()
        self.n = 0

    def remaining(self):
        self.n += 1
        return 1000.0 if self.n <= 1 else 5.0


def test_not_enough_time_protocol_is_a_timeout_summary_is_marked_incomplete():
    llm, _ = llm_for(combined)
    with pytest.raises(JobTimeout) as e:
        run_local(llm, kind="protocol", budget=StepBudget())
    assert e.value.progress["done"] == 1 and e.value.progress["total"] == n_fragments() and "полный протокол не собран" in str(e.value)
    llm, _ = llm_for(combined)
    res = run_local(llm, kind="summary", budget=StepBudget())
    assert res.structured["complete"] is False and res.structured["coverage"]["done"] == 1 and res.structured["coverage"]["total"] == n_fragments()
    assert res.warnings[0].startswith("Резюме неполное") and "из " in res.warnings[0]
    llm, seen = llm_for(combined)
    with pytest.raises(JobTimeout):                     # времени нет совсем: ни одного обращения к модели
        run_local(llm, kind="summary", budget=Budget(max_s=1.0))
    assert seen == []


def test_summary_paragraph_is_built_by_code_when_the_target_is_already_missed():
    llm, seen = llm_for(combined)
    res = run_local(llm, kind="summary", limit=100000, budget=Budget(target_s=1.0, max_s=600.0))
    assert len(seen) == 1 and "response_format" in seen[0], "до цели меньше минуты: итоговый абзац собирает код, лишнего обращения к модели нет"
    assert any("без дополнительного обращения к модели" in w for w in res.warnings) and res.text.strip()


# ------------------------------------------------------------------------------------------------ задание целиком: время, отмена, осиротевшие
class Hung(Runtime):
    """Подставной llama-server, который не отвечает: запрос висит, пока его не отменят (так видно, что соединение действительно закрыто)."""

    def __init__(self):
        super().__init__()
        self.entered, self.cancelled = 0, 0

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        self.entered += 1
        try:
            await asyncio.sleep(120)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return httpx.Response(500)


def wait_for(client, mid, pid, states, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        got = client.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        if got["status"] in states:
            return got
        time.sleep(0.2)
    raise AssertionError(f"состояние {states} не наступило: {got['status']}")


def start_summary(c, mid):
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})
    assert r.status_code == 202, r.text
    return r.json()["protocol_id"]


class Optimistic(Budget):
    """Таймер задания — 2 с, но конвейер считает, что времени достаточно: запрос уходит и «зависает», пока его не отменит предел."""

    def remaining(self):
        return 1000.0


def test_no_time_for_even_one_pass_stops_the_job_before_any_model_call(tmp_path, directory, monkeypatch):
    rt = Hung()
    monkeypatch.setattr(protocols_mod, "Budget", lambda: Budget(max_s=2.0, target_s=1.0))
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = start_summary(c, mid)
        got = wait_for(c, mid, pid, {"timeout"}, timeout=20)
        _drain(c)
        assert got["status"] == "timeout" and rt.entered == 0 and got["progress"] == {"done": 0, "total": 1} and "обработано 0 из 1" in got["error"]


def test_the_whole_job_is_stopped_at_the_limit_and_the_model_request_is_cancelled(tmp_path, directory, monkeypatch):
    rt = Hung()
    monkeypatch.setattr(protocols_mod, "Budget", lambda: Optimistic(max_s=2.0, target_s=1.0))
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = start_summary(c, mid)
        running = wait_for(c, mid, pid, {"running", "timeout"})
        assert running["status"] == "running" and running["run_started_at"], "до остановки задание «в работе», время начала известно"
        got = wait_for(c, mid, pid, {"timeout"}, timeout=20)
        _drain(c)
        assert got["status"] == "timeout" and got["error"].startswith("Превышено время выполнения") and "2 с" in got["error"]
        assert rt.entered == 1 and rt.cancelled == 1, "запрос к модели отменён (соединение закрыто), новых обращений нет"
        assert c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()["generation"]["error_code"] == "timeout"
        again = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "x"})      # повторный запуск доступен
        assert again.status_code == 202


def test_user_can_cancel_and_resources_are_released(tmp_path, directory):
    rt = Hung()
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local")
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        login(c, "alice")
        pid = start_summary(c, mid)
        wait_for(c, mid, pid, {"running"})
        assert c.patch(f"/api/v1/meetings/{mid}/protocols/{pid}", json={"content": "x"}).status_code == 409, "пока формируется, править нельзя"
        r = c.post(f"/api/v1/meetings/{mid}/protocols/{pid}/cancel")
        assert r.status_code == 202 and r.json()["status"] == "cancelling"
        got = wait_for(c, mid, pid, {"cancelled"}, timeout=20)
        _drain(c)
        assert got["error"] == "Формирование отменено." and rt.cancelled == 1
        assert c.post(f"/api/v1/meetings/{mid}/protocols/{pid}/cancel").status_code == 409, "завершённое не отменяется"
        login(c, "bob")
        assert c.post(f"/api/v1/meetings/{mid}/protocols/{pid}/cancel").status_code in (403, 404)


def test_orphaned_jobs_are_closed_after_a_restart_without_touching_materials(tmp_path, directory):
    with app_with(tmp_path, directory, Runtime(), **model_env(tmp_path)) as c:
        _room, mid = meeting_with_two(c)
        end_by_alice(c, mid)
        ids = {}

        async def seed():
            async with c.app_obj.state.session_maker() as db:
                for kind, status in (("summary", "pending"), ("protocol", "running"), ("protocol", "ready"), ("transcript", "pending")):
                    rec = Protocol(meeting_id=uuid.UUID(mid), kind=kind, status=status, created_by="t", content="готовый текст" if status == "ready" else None)
                    db.add(rec)
                    await db.flush()
                    ids[(kind, status)] = rec.id
                await db.commit()

        c.portal.call(seed)
        assert c.portal.call(c.app_obj.state.protocols.recover_orphans) == 2

        async def read():
            async with c.app_obj.state.session_maker() as db:
                return {k: await db.get(Protocol, v) for k, v in ids.items()}

        rows = c.portal.call(read)
        for key in (("summary", "pending"), ("protocol", "running")):
            assert rows[key].status == "failed" and rows[key].meta["error_code"] == "interrupted" and "перезапуском" in rows[key].error
        assert rows[("protocol", "ready")].status == "ready" and rows[("protocol", "ready")].content == "готовый текст"
        assert rows[("transcript", "pending")].status == "pending", "выгрузка стенограммы — другая задача, её не трогаем"
        assert c.portal.call(c.app_obj.state.protocols.recover_orphans) == 0


# ------------------------------------------------------------------------------------------------ лимиты и инструкции
def test_local_model_ignores_user_limits_and_instructions_but_external_keeps_them(tmp_path, directory):
    rt = Runtime()
    with app_with(tmp_path, directory, rt, **model_env(tmp_path)) as c:
        put_settings(c, "llm", provider="local", max_tokens_protocol=64, max_tokens_summary=64, limit_protocol=64, limit_summary=64, context_window=2048)
        _room, mid = meeting_with_two(c, protocol_instructions="КОМНАТНАЯ ИНСТРУКЦИЯ")
        end_by_alice(c, mid)
        login(c, "alice")
        d = c.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "summary"}).json()
        assert d["builtin"] is True and d["instruction"] == LOCAL_BUILTIN_INSTRUCTION and "КОМНАТНАЯ" not in d["instruction"]
        assert d["plan"]["builtin_instruction"] is True and d["plan"]["max_output_tokens"] is None and d["plan"]["job_limit_s"] == 600
        pid = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "summary", "instruction": "МОЯ ИНСТРУКЦИЯ: только цифры"}).json()["protocol_id"]
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()
        assert got["status"] == "ready" and got["instruction"] == LOCAL_BUILTIN_INSTRUCTION
        sent = "\n".join(m["content"] for b in rt.seen for m in b["messages"])
        assert "МОЯ ИНСТРУКЦИЯ" not in sent and "КОМНАТНАЯ ИНСТРУКЦИЯ" not in sent
        assert min(b["max_tokens"] for b in rt.seen) > 64, "пользовательские лимиты длины ответа к локальной модели не применяются"
