"""Память комнаты: связывание тем разных встреч, состояние тем и наблюдения по событиям, ручные правки (не переписываются повторным анализом), права
(история только по доступным встречам), срок хранения. Смысловая часть — на синтетической истории tests/memory_corpus.py с эталонной разметкой."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.models import ConversationMap, Meeting, MeetingGrant, RoomThreadEvent, TranscriptSegment, User
from app.services import room_memory as rm

from . import memory_corpus as mc
from .conftest import login, make_room, make_settings, put_settings, running_app

API = "/api/v1/rooms"
TITLE_GOLD = {t["title"]: t["gold"] for m in mc.MEETINGS for t in m}


def gold_llm(seen: list):
    """Подставная модель для связывания: отвечает по эталону (проверяет обвязку: кандидаты, разбор, проверку идентификаторов), а не качество модели."""
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        user = body["messages"][-1]["content"]
        seen.append(user)
        threads = dict(re.findall(r"^(T\d+) \| ([^|]+?) \|", user, re.M))
        links = []
        for n, title, cands in re.findall(r"^(N\d+) \| ([^|]+?) \|.*кандидаты: (.*)$", user, re.M):
            g = TITLE_GOLD.get(title.strip())
            pick = next((c.strip() for c in cands.split(",") if c.strip() in threads and TITLE_GOLD.get(threads[c.strip()].strip()) == g), "new")
            links.append({"topic": n, "thread": pick})
        links.append({"topic": "N1", "thread": "T999"}) if links else None            # чужой идентификатор должен быть отвергнут
        content = json.dumps({"links": links}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
    return h


def seed(c, room_id: str, *, upto: int | None = None) -> list[str]:
    """Встречи корпуса в базе: встреча, реплики (по одной на тему) и готовая карта. Возвращает id встреч по порядку."""
    sm = c.app_obj.state.memory.ps._sm
    rid = uuid.UUID(room_id)

    async def go():
        ids = []
        async with sm() as db:
            for i in range(mc.NO_TRANSCRIPT + 1):
                st = mc.meeting_start(i)
                m = Meeting(room_id=rid, livekit_room=f"m-{uuid.uuid4().hex}", started_at=st, ended_at=st + timedelta(minutes=30), finalized_at=st + timedelta(minutes=31))
                db.add(m)
                await db.flush()
                ids.append(str(m.id))
                topics = mc.MEETINGS[i] if i < len(mc.MEETINGS) else ([{"title": "Разное", "summary": "Короткое обсуждение", "segments": [{"start_s": 10, "end_s": 300}]}]
                                                                     if i == mc.NO_MAP else [])
                for t in topics:
                    s0 = t["segments"][0]["start_s"]
                    db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=rid, participant_identity="u", started_at=st + timedelta(seconds=s0),
                                             ended_at=st + timedelta(seconds=s0 + 30), text=t["summary"]))
                if i < len(mc.MEETINGS) and (upto is None or i < upto):
                    data = {"version": 3, "meeting": {"title": "Планёрка", "started_at": st.isoformat(), "duration_s": 1800}, "categories": [],
                            "topics": [{k: v for k, v in t.items() if k != "gold"} | {"ai_title": t["title"], "ai_category": t["category"]} for t in topics],
                            "unassigned": {}, "items_ready": True}
                    db.add(ConversationMap(meeting_id=m.id, status="ready", data=data, meta={}))
            await db.commit()
        return ids
    return c.portal.call(go)


def assignments(c, room_id: str, ids: list[str]) -> dict[tuple[int, str], str]:
    async def go():
        async with c.app_obj.state.memory.ps._sm() as db:
            rows = (await db.execute(select(RoomThreadEvent.meeting_id, RoomThreadEvent.topic_id, RoomThreadEvent.thread_id).where(RoomThreadEvent.kind == "mention"))).all()
        return {(ids.index(str(m)), t): str(th) for m, t, th in rows}
    return c.portal.call(go)


def process(c, room_id: str):
    return c.portal.call(c.app_obj.state.memory.process_room, uuid.UUID(room_id))


# ------------------------------------------------------------------------------------------------ чистые правила
def test_candidates_and_strict_code_rule_do_not_merge_similar_but_different_topics():
    vpn_vendor = {"title": "Доступ подрядчиков по VPN", "summary": "Подрядчикам нужен доступ к стенду через VPN."}
    vpn_staff = {"id": "x", "title": "VPN для удалённых сотрудников", "summary": "Сотрудникам нужен VPN."}
    assert rm.score(vpn_vendor, vpn_staff) > 0, "похожие — кандидаты для модели"
    assert rm.code_link(vpn_vendor, [vpn_staff]) is None, "без модели похожие слова не объединяют разные вопросы"
    same = {"id": "y", "title": "Найм инженера поддержки", "summary": ""}
    assert rm.code_link({"title": "Найм инженера поддержки", "summary": ""}, [same]) == "y"
    out = rm.parse_links('{"links":[{"topic":"N1","thread":"T2"},{"topic":"N2","thread":"T9"}]}',
                         {"N1": {"T2"}, "N2": {"T1"}, "N3": set()})
    assert out == {"N1": "T2"}, "идентификатор вне кандидатов отвергается; без внятного ответа решает правило кода"
    assert rm.parse_links("Обсудили сертификаты.", {"N1": {"T1"}}) == {}


def _ev(i, kind, days, meeting, text="x", **kw):
    return rm.Ev(id=f"e{i}", thread="t", meeting=meeting, kind=kind, text=text, at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=days), **kw)


def test_state_and_observations_are_explained_by_events_and_development_is_not_called_repetition():
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rep = rm.ThreadView("t", "Найм", [_ev(1, "mention", 0, "m1"), _ev(2, "mention", 7, "m2"), _ev(3, "mention", 14, "m3")])
    dev = rm.ThreadView("t", "Отчёты", [_ev(1, "mention", 0, "m1"), _ev(2, "mention", 7, "m2"), _ev(3, "proposal", 7, "m2", "по почте"),
                                        _ev(4, "mention", 14, "m3"), _ev(5, "question", 14, "m3", "кому можно?")])
    kinds = lambda t: {o["kind"] for o in rm.observations([t], now)}     # noqa: E731
    assert "repeat" in kinds(rep) and "developing" not in kinds(rep)
    assert "developing" in kinds(dev) and "repeat" not in kinds(dev), "новые предложения и вопросы — развитие, а не повторение"
    assert rm.thread_state(rep, now) == "dormant"
    reopened = rm.ThreadView("t", "Почта", [_ev(1, "mention", 0, "m1"), _ev(2, "decision", 0, "m1", "переходим"), _ev(3, "mention", 20, "m2")])
    assert rm.thread_state(reopened, now) == "reopened" and "reopened" in kinds(reopened)
    task = rm.ThreadView("t", "Бюджет", [_ev(1, "mention", 0, "m1"), _ev(2, "decision", 0, "m1"), _ev(3, "task", 0, "m1", "заявка")])
    obs = {o["kind"]: o for o in rm.observations([task], now)}
    assert "unconfirmed" in obs and "не значит, что поручение не выполнено" in obs["unconfirmed"]["why"], "нет сведений ≠ не выполнено"
    assert "task_owner" in obs and obs["task_owner"]["evidence"] == ["e3"]
    closed = rm.ThreadView("t", "Найм", rep.events, user_state="closed")
    assert rm.observations([closed], now) == [] and rm.thread_state(closed, now) == "closed", "закрытая человеком тема не тревожит"


# ------------------------------------------------------------------------------------------------ история комнаты целиком
def test_room_history_links_topics_across_meetings_and_reports_quality_against_gold(tmp_path, directory, capsys):
    seen: list = []
    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(gold_llm(seen))}) as c:
        room = make_room(c)
        ids = seed(c, room["id"])
        # без модели — строгое правило кода
        process(c, room["id"])
        code = mc.gold_pairs(assignments(c, room["id"], ids))
        assert code["false_merges"] == 0, code
        # с моделью (подставной, по эталону): пересчёт с нуля
        put_settings(c, "llm", enabled=True, provider="external", type="openai_compatible", base_url="http://llm.test/v1", model="m", allow_http=True)

        async def reset():
            async with c.app_obj.state.memory.ps._sm() as db:
                from app.models import RoomMemory, RoomThread
                await db.execute(RoomThreadEvent.__table__.delete())
                await db.execute(RoomThread.__table__.delete())
                await db.execute(RoomMemory.__table__.delete())
                await db.commit()
        c.portal.call(reset)
        process(c, room["id"])
        llm = mc.gold_pairs(assignments(c, room["id"], ids))
        print(f"\nсвязывание: без модели {code}; с моделью-эталоном {llm}")
        assert llm == {"precision": 1.0, "recall": 1.0, "false_merges": 0, "missed_links": 0}, llm
        assert seen and all("Предлагаю автоматизировать" not in u and "Белова" not in u for u in seen), "в модель — названия и суть тем из карты, без цитат и авторов пунктов"
        login(c, "root")
        st = c.get(f"{API}/{room['id']}/memory").json()
        titles = {t["title"]: t for t in st["threads"]}
        rep = titles["Автоматизация подготовки отчётов"]
        assert len(rep["meetings"]) == 6 and rep["counts"]["decision"] == 2 and rep["author"] == "Анна Белова", rep
        kinds = {(o["kind"], o["title"].split("»")[0].strip("«")) for o in st["observations"]}
        assert ("changed", "Автоматизация подготовки отчётов") in kinds, "два решения в разные даты"
        assert ("reopened", "Обновление почтового сервера") in kinds
        assert ("repeat", "Найм инженера поддержки") in kinds
        assert any(k == "unconfirmed" for k, _ in kinds) and any(k == "lost" for k, _ in kinds) and any(k == "progress" for k, _ in kinds)
        assert titles["Доступ подрядчиков по VPN"]["id"] != titles["VPN для удалённых сотрудников"]["id"]
        cov = st["coverage"]
        assert cov["meetings"] == 11 and cov["with_transcript"] == 10 and cov["with_map"] == 9 and cov["without_map"] == 1, cov


def test_manual_corrections_survive_reanalysis_and_dismissed_advice_does_not_return(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory) as c:
        room = make_room(c)
        ids = seed(c, room["id"])
        process(c, room["id"])
        login(c, "root")
        st = c.get(f"{API}/{room['id']}/memory").json()
        by = {t["title"]: t for t in st["threads"]}
        vendor, staff = by["Доступ подрядчиков по VPN"], by["VPN для удалённых сотрудников"]
        # объединить (ошибочно) и разделить обратно; затем переименовать и закрыть
        assert c.post(f"{API}/{room['id']}/memory/threads/{staff['id']}/merge", json={"into": vendor["id"]}).status_code == 200
        process(c, room["id"])
        st = c.get(f"{API}/{room['id']}/memory").json()
        merged = next(t for t in st["threads"] if t["id"] == vendor["id"])
        assert len(merged["meetings"]) == 2, "объединение закреплено и не отменено повторным анализом"
        r = c.post(f"{API}/{room['id']}/memory/threads/{vendor['id']}/split", json={"meeting_id": ids[6], "topic_id": "t_v2"})
        assert r.status_code == 200
        new_id = r.json()["thread"]
        assert c.patch(f"{API}/{room['id']}/memory/threads/{new_id}", json={"title": "VPN сотрудников", "state": "closed", "author": "Олег Ким"}).status_code == 200
        # переанализ всей комнаты (карты «изменились»)
        async def touch():
            async with c.app_obj.state.memory.ps._sm() as db:
                from app.models import RoomMemory
                mem = await db.get(RoomMemory, uuid.UUID(room["id"]))
                mem.processed = {}
                await db.commit()
        c.portal.call(touch)
        process(c, room["id"])
        st = c.get(f"{API}/{room['id']}/memory").json()
        t = next(t for t in st["threads"] if t["id"] == new_id)
        assert t["title"] == "VPN сотрудников" and t["state"] == "closed" and t["author"] == "Олег Ким" and t["author_is_user"]
        assert next(x for x in st["threads"] if x["id"] == vendor["id"])["meetings"] == [ids[3]]
        # отзыв на наблюдение
        o = st["observations"][0]
        assert c.post(f"{API}/{room['id']}/memory/observations/feedback", json={"key": o["key"], "status": "dismissed"}).status_code == 200
        st2 = c.get(f"{API}/{room['id']}/memory").json()
        assert o["key"] not in {x["key"] for x in st2["observations"]} and st2["dismissed"] == st["dismissed"] + 1
        acts = {a["action"] for a in c.get("/api/v1/admin/audit").json()}
        assert {"memory.merge", "memory.split", "memory.edit_thread"} <= acts


def test_history_is_limited_to_meetings_the_user_may_see_and_only_leaders_edit(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory) as c:
        room = make_room(c)
        ids = seed(c, room["id"])
        process(c, room["id"])
        login(c, "alice")
        login(c, "bob")
        assert c.get(f"{API}/{room['id']}/memory").status_code == 403, "нет доступа ни к одной встрече — нет и истории"

        async def grant():
            async with c.app_obj.state.memory.ps._sm() as db:
                uid = (await db.execute(select(User.id).where(User.sam_account_name == "bob"))).scalar_one()
                db.add(MeetingGrant(meeting_id=uuid.UUID(ids[3]), user_id=uid, granted_by="test"))
                await db.commit()
        c.portal.call(grant)
        st = c.get(f"{API}/{room['id']}/memory").json()
        assert {e["meeting"] for e in st["events"]} == {ids[3]}, "события только доступной встречи"
        assert {t["title"] for t in st["threads"]} == {"Бюджет на следующий квартал", "Доступ подрядчиков по VPN"}
        assert st["coverage"]["partial_view"] and not st["can_manage"]
        r = c.post(f"{API}/{room['id']}/memory/ask", json={"question": "Что решили по почтовому серверу?"})
        assert r.status_code == 200 and not r.json()["events"], "вопрос не находит событий недоступных встреч"
        tid = st["threads"][0]["id"]
        assert c.patch(f"{API}/{room['id']}/memory/threads/{tid}", json={"title": "x"}).status_code == 403
        assert c.post(f"{API}/{room['id']}/memory/settings", json={"mode": "on"}).status_code == 403


def test_ask_answers_only_from_selected_events_and_falls_back_to_search_without_model(tmp_path, directory):
    asked: list = []

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        asked.append(body["messages"][-1]["content"])
        if "response_format" in body:                                   # связывание тем при обработке
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"links":[]}'}, "finish_reason": "stop"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Решили перевести сервер на новую версию [E1]."}, "finish_reason": "stop"}]})

    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(h)}) as c:
        room = make_room(c)
        seed(c, room["id"])
        process(c, room["id"])
        login(c, "root")
        r = c.post(f"{API}/{room['id']}/memory/ask", json={"question": "Что решили по почтовому серверу?"}).json()
        assert r["basis"] == "search" and r["events"] and r["answer"] is None, "без модели — найденные события"
        put_settings(c, "llm", enabled=True, provider="external", type="openai_compatible", base_url="http://llm.test/v1", model="m", allow_http=True)
        r = c.post(f"{API}/{room['id']}/memory/ask", json={"question": "Что решили по почтовому серверу?"}).json()
        assert r["basis"] == "ai" and "[E1]" in r["answer"] and any("почтов" in e["text"].lower() for e in r["events"])
        q = asked[-1]
        assert "ВОПРОС" in q and len(q.splitlines()) <= 45 and "Найм" not in q, "в модель — только отобранные события, не вся история"


def test_retention_drops_events_or_keeps_them_without_quotes(tmp_path, directory):
    from app.services.room_memory import forget_meetings

    with running_app(make_settings(tmp_path), directory) as c:
        room = make_room(c)
        ids = seed(c, room["id"])
        process(c, room["id"])

        async def purge(keep: bool):
            async with c.app_obj.state.memory.ps._sm() as db:
                n = await forget_meetings(db, [uuid.UUID(ids[0])], {uuid.UUID(room["id"])} if keep else set())
                await db.commit()
                rows = (await db.execute(select(RoomThreadEvent).where(RoomThreadEvent.meeting_id == uuid.UUID(ids[0])))).scalars().all()
                return n, [(e.quote, e.source_gone) for e in rows]
        n, rows = c.portal.call(purge, True)
        assert n == 0 and rows and all(q is None and gone for q, gone in rows), "история остаётся, цитат нет, первоисточник помечен удалённым"
        n, rows = c.portal.call(purge, False)
        assert n > 0 and rows == []
