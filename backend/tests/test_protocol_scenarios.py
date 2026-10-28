"""Сценарии формирования протокола на уже завершённых встречах: сегодня, неделю и месяц назад; после ошибки; другой моделью; по другому шаблону; после обновления;
число обращений к модели; HTML/Word/PDF без обращений к модели; прежний готовый протокол не пропадает, пока нового нет."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.models import Meeting, Protocol, TranscriptSegment
from app.services.settings import utcnow

from .conftest import login, make_settings, put_settings, running_app
from .test_access_and_protocols import end_by_alice, meeting_with_two
from .test_admin_features import _drain
from .test_llm_admin_api import add_profile

DOC = "# Протокол встречи\n\n## Решения\n\n* Утвердить бюджет\n\n## Поручения\n\n| Ответственный | Поручение | Срок |\n| --- | --- | --- |\n| Алиса | Подготовить смету | 01.11 |\n"


class Provider:
    """Подставной провайдер: считает обращения, помнит последний запрос, ведёт себя по сценарию."""

    def __init__(self):
        self.requests: list[dict] = []
        self.plan: list[httpx.Response | None] = []     # None — ответ по умолчанию

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(req.content))
        nxt = self.plan.pop(0) if self.plan else None
        return nxt or httpx.Response(200, json={"choices": [{"message": {"content": DOC}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 300, "completion_tokens": 120}})


@pytest.fixture
def env(tmp_path, directory):
    prov = Provider()
    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(prov)}) as c:
        put_settings(c, "llm", enabled=True, type="openai", model="m", api_key="k")
        c.prov = prov
        yield c


def finished(c, **room_over):
    room, mid = meeting_with_two(c, **room_over)
    end_by_alice(c, mid)
    login(c, "alice")
    return room, mid


def make(c, mid, kind="protocol", **body):
    r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": kind, "instruction": "Коротко", **body})
    assert r.status_code == 202, r.text
    pid = r.json()["protocol_id"]
    _drain(c)
    return c.get(f"/api/v1/meetings/{mid}/protocols/{pid}").json()


def age(c, mid, days):
    """Встреча «состоялась» days дней назад: сдвигаем её и реплики; комнаты LiveKit и сеанса конференции давно нет."""
    async def go():
        async with c.app_obj.state.session_maker() as db:
            m = await db.get(Meeting, uuid.UUID(mid))
            d = timedelta(days=days)
            await db.execute(update(Meeting).where(Meeting.id == m.id).values(started_at=m.started_at - d, ended_at=m.ended_at - d))
            for s in (await db.execute(select(TranscriptSegment).where(TranscriptSegment.meeting_id == m.id))).scalars():
                s.started_at, s.ended_at = s.started_at - d, s.ended_at - d
            await db.commit()
    c.portal.call(go)


@pytest.mark.parametrize("days", [0, 8, 45])
def test_protocol_is_made_from_the_saved_transcript_today_last_week_and_last_month(env, days):
    _room, mid = finished(env)
    if days:
        age(env, mid, days)
    n = len(env.prov.requests)
    p = make(env, mid)
    assert p["status"] == "ready" and "Утвердить бюджет" in p["content"], p.get("error")
    assert len(env.prov.requests) - n == 1, "один логический запрос на весь протокол"
    assert "Принято решение по бюджету" in env.prov.requests[-1]["messages"][-1]["content"], "в модель ушла сохранённая стенограмма"
    g = p["generation"]
    assert g["llm_calls"] == 1 and g["model"] == "m" and p["timing"]["total_s"] is not None


def test_failed_attempt_stays_in_the_list_and_a_new_attempt_works_without_the_old_room(env):
    _room, mid = finished(env)
    env.prov.plan = [httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"completion_tokens": 0}})] * 2
    bad = make(env, mid)
    assert bad["status"] == "failed" and "пустой ответ" in bad["error"] and len(env.prov.requests) == 2, "пустой ответ: один повтор и понятная причина"
    good = make(env, mid)
    assert good["status"] == "ready"
    rows = env.get(f"/api/v1/meetings/{mid}/protocols").json()
    assert [r["status"] for r in rows] == ["ready", "failed"], "неудачная попытка не мешает и не стирается"


def test_regeneration_never_destroys_a_ready_protocol_even_when_the_new_attempt_fails(env, monkeypatch):
    import app.services.protocols as pr
    monkeypatch.setattr(pr, "RETRY_PAUSE_S", 0)
    _room, mid = finished(env)
    first = make(env, mid)
    env.prov.plan = [httpx.Response(500, text="boom")] * 2            # сбой и единственный повтор
    second = make(env, mid)
    assert second["status"] == "failed"
    rows = {r["id"]: r for r in env.get(f"/api/v1/meetings/{mid}/protocols").json()}
    assert rows[first["id"]]["status"] == "ready"
    again = env.get(f"/api/v1/meetings/{mid}/protocols/{first['id']}").json()
    assert again["status"] == "ready" and again["content"] == first["content"]


def test_other_model_for_one_run_and_the_template_reach_the_provider(env):
    room, mid = finished(env)
    a = add_profile(env, "Подключение A", "model-a")
    login(env, "root")
    p = make(env, mid, instruction="ШАБЛОН: только решения и сроки", llm_once=f"profile:{a['id']}")
    assert p["status"] == "ready" and p["generation"]["model"] == "model-a" and p["generation"]["llm_once"]
    last = env.prov.requests[-1]
    assert last["model"] == "model-a" and "ШАБЛОН: только решения и сроки" in last["messages"][0]["content"], "пользовательский шаблон — в запросе"
    q = make(env, mid, instruction="ДРУГОЙ ШАБЛОН")
    assert q["generation"]["model"] == "m" and "ДРУГОЙ ШАБЛОН" in env.prov.requests[-1]["messages"][0]["content"]


def test_protocol_left_running_by_an_update_is_closed_and_can_be_regenerated(env):
    _room, mid = finished(env)

    async def orphan():
        async with env.app_obj.state.session_maker() as db:
            db.add(Protocol(meeting_id=uuid.UUID(mid), kind="protocol", status="running", created_by="test", instruction="x"))
            await db.commit()
    env.portal.call(orphan)
    assert env.portal.call(env.app_obj.state.protocols.recover_orphans) >= 1
    rows = env.get(f"/api/v1/meetings/{mid}/protocols").json()
    assert rows[0]["status"] == "failed" and "перезапуск" in (rows[0]["error"] or "").lower() or rows[0]["status"] == "failed"
    assert make(env, mid)["status"] == "ready"


def test_html_word_pdf_and_markdown_are_built_without_new_model_calls(env):
    _room, mid = finished(env)
    p = make(env, mid)
    n = len(env.prov.requests)
    for fmt, marker in (("html", b"<"), ("md", b"#"), ("docx", b"PK"), ("pdf", b"%PDF")):
        r = env.get(f"/api/v1/meetings/{mid}/protocols/{p['id']}/export", params={"format": fmt})
        assert r.status_code == 200 and marker in r.content[:400], fmt
    html = env.get(f"/api/v1/meetings/{mid}/protocols/{p['id']}/export", params={"format": "html"}).text
    assert "Утвердить бюджет" in html and "<table" in html and "Подготовить смету" in html
    assert len(env.prov.requests) == n, "смена формата модель не вызывает"


def test_long_transcript_uses_per_fragment_notes_and_a_failed_fragment_is_repeated_alone(env):
    _room, mid = finished(env)
    put_settings(env, "protocol", max_input_chars=2000)
    async def many():
        async with env.app_obj.state.session_maker() as db:
            seg = (await db.execute(select(TranscriptSegment).where(TranscriptSegment.meeting_id == uuid.UUID(mid)))).scalars().first()
            for i in range(60):
                db.add(TranscriptSegment(meeting_id=seg.meeting_id, room_id=seg.room_id, user_id=seg.user_id, participant_identity=seg.participant_identity, segment_uid=uuid.uuid4(),
                                         started_at=seg.started_at + timedelta(seconds=10 * (i + 1)), ended_at=seg.started_at + timedelta(seconds=10 * (i + 1) + 5),
                                         text=f"Реплика номер {i}: обсуждаем пункт повестки и сроки поставки оборудования", language="ru", model={}))
            await db.commit()
    env.portal.call(many)
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "* решение"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 5}})
    broken = httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"completion_tokens": 0}})
    env.prov.plan = [ok, broken, ok]                              # вторая часть сначала пустая — повторяется только она
    n = len(env.prov.requests)
    p = make(env, mid)
    assert p["status"] == "ready", p.get("error")
    sent = [r["messages"][-1]["content"][:30] for r in env.prov.requests[n:]]
    parts = {s for s in sent if s.startswith("Фрагмент")}
    assert len(sent) == p["generation"]["llm_calls"], sent          # «запросов к модели» в диагностике — реальное число, с повтором пустого фрагмента
    assert sum(1 for s in sent if s.startswith("Фрагмент 2 ")) == 2 and sum(1 for s in sent if s.startswith("Фрагмент 1 ")) == 1, "повторён только неудавшийся фрагмент"
    assert sent[-1].startswith("Заметки по фрагментам") and parts


def test_transient_provider_failure_repeats_only_that_request_once(env, monkeypatch):
    import app.services.protocols as pr

    monkeypatch.setattr(pr, "RETRY_PAUSE_S", 0)
    _room, mid = finished(env)
    env.prov.plan = [httpx.Response(503, text="overloaded")]
    n = len(env.prov.requests)
    p = make(env, mid)
    assert p["status"] == "ready" and len(env.prov.requests) - n == 2, "один сбой 503 — один повтор, затем готовый документ"
    env.prov.plan = [httpx.Response(401, text="bad key")]
    n = len(env.prov.requests)
    bad = make(env, mid)
    assert bad["status"] == "failed" and len(env.prov.requests) - n == 1, "отказ по ключу не повторяется"


def test_meeting_that_fits_the_known_context_window_goes_in_one_request(env):
    _room, mid = finished(env)
    put_settings(env, "protocol", max_input_chars=2000)
    put_settings(env, "llm", context_window=128000)

    async def many():
        async with env.app_obj.state.session_maker() as db:
            seg = (await db.execute(select(TranscriptSegment).where(TranscriptSegment.meeting_id == uuid.UUID(mid)))).scalars().first()
            for i in range(80):
                db.add(TranscriptSegment(meeting_id=seg.meeting_id, room_id=seg.room_id, user_id=seg.user_id, participant_identity=seg.participant_identity, segment_uid=uuid.uuid4(),
                                         started_at=seg.started_at + timedelta(seconds=10 * (i + 1)), ended_at=seg.started_at + timedelta(seconds=10 * (i + 1) + 5),
                                         text=f"Реплика номер {i}: обсуждаем пункт повестки и сроки поставки оборудования", language="ru", model={}))
            await db.commit()
    env.portal.call(many)
    n = len(env.prov.requests)
    p = make(env, mid)
    assert p["status"] == "ready" and len(env.prov.requests) - n == 1 and p["generation"]["llm_calls"] == 1
    assert "Реплика номер 79" in env.prov.requests[-1]["messages"][-1]["content"], "вся стенограмма в одном запросе"


def test_job_time_limit_is_a_setting_and_defaults_to_ten_minutes(env):
    _room, mid = finished(env)
    d = env.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol"}).json()
    assert d["plan"]["job_limit_s"] == 600
    put_settings(env, "protocol", job_limit_s=1800)
    login(env, "alice")
    assert env.get(f"/api/v1/meetings/{mid}/protocols/default-instruction", params={"kind": "protocol"}).json()["plan"]["job_limit_s"] == 1800
    assert make(env, mid)["status"] == "ready"
