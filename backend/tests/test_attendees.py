"""«Присутствовали» в протоколе: таблицы ФИО / Должность / Подразделение (сотрудники) и Имя / Статус (гости), снимок данных на момент встречи (должность через год не
переписывает старый протокол), один источник для всех видов документа. Без сети."""
from __future__ import annotations

import httpx
from sqlalchemy import update

from app.models import User
from app.services import doc_render as dr
from app.services.export_docs import to_html

from .conftest import login, make_room, make_settings, put_settings, running_app
from .test_admin_features import _drain
from .test_transcripts import _feed_and_consume, _join, _segment

PEOPLE = {"people": [{"name": "Лопанский Станислав Евгеньевич", "title": "Ведущий специалист по информационной безопасности",
                      "department": "Служба системного администрирования и информационной безопасности", "email": "s@example.local", "phone": ""},
                     {"name": "Без Должности", "title": "", "department": "", "email": "", "phone": ""}],
          "guests": [{"name": "Пётр (гость)", "status": "Гость"}]}


def test_attendee_tables_have_the_requested_columns_and_do_not_invent_missing_values():
    md = "\n".join(dr.attendees_markdown(PEOPLE))
    assert "## Присутствовали" in md and "| ФИО | Должность | Подразделение |" in md and "| Имя | Статус |" in md
    assert "| Лопанский Станислав Евгеньевич | Ведущий специалист по информационной безопасности | Служба системного администрирования и информационной безопасности |" in md
    assert "| Без Должности | — | — |" in md and "| Пётр (гость) | Гость |" in md
    assert "s@example.local" not in md, "e-mail и телефон в официальный протокол не попадают"
    assert dr.attendees_markdown(None) == [] and dr.attendees_markdown({"people": [], "guests": []}) == []


def test_document_replaces_the_names_line_with_the_tables_and_free_text_gets_the_section_injected():
    doc = dr.build_document(["Переговорка: Тест", "Участвовали: А, Б"], [], attendees=PEOPLE)
    md = dr.document_to_markdown(doc)
    assert "**Участвовали:**" not in md and "## Присутствовали" in md
    assert "<table" in to_html(md) and "Ведущий специалист" in to_html(md)
    free = dr.inject_attendees("# Протокол\n\nТекст от модели\n", PEOPLE)
    assert free.index("## Присутствовали") < free.index("Текст от модели") and free.startswith("# Протокол")
    assert dr.inject_attendees(free, PEOPLE) == free, "второй раз раздел не добавляется"
    assert dr.inject_attendees("# П\n\n## Присутствовали\n\nсписок", PEOPLE).count("Присутствовали") == 1, "если модель написала раздел сама — не дублируется"


def md_llm(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "# Протокол\n\nОбсудили бюджет."}, "finish_reason": "stop"}]})


def test_protocol_uses_the_participants_snapshot_taken_at_the_meeting_not_todays_profile(tmp_path, directory):
    with running_app(make_settings(tmp_path), directory, transports={"llm": httpx.MockTransport(md_llm)}) as c:
        put_settings(c, "llm", enabled=True, provider="external", type="openai_compatible", base_url="http://llm.test/v1", model="m", allow_http=True)
        put_settings(c, "protocol", external_mode="free")
        room = make_room(c)
        a = _join(c, "alice", room["id"])
        _join(c, "bob", room["id"])
        mid = a["meeting_id"]

        async def set_profile(sam: str, title: str, dep: str):
            async with c.app_obj.state.session_maker() as db:
                await db.execute(update(User).where(User.sam_account_name == sam).values(title=title, department=dep))
                await db.commit()

        c.portal.call(lambda: set_profile("alice", "Системный администратор", "Отдел эксплуатации"))
        _feed_and_consume(c, [_segment(mid, a["identity"], "Обсудили бюджет", offset=5, dur=30)])
        login(c, "alice")
        assert c.post(f"/api/v1/meetings/{mid}/end").status_code == 204
        _drain(c)
        # через «год» человек стал директором, запись в каталоге изменилась
        c.portal.call(lambda: set_profile("alice", "Директор", "Правление"))
        r = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "x"})
        assert r.status_code == 202, r.text
        _drain(c)
        got = c.get(f"/api/v1/meetings/{mid}/protocols/{r.json()['protocol_id']}").json()["content"]
        assert "## Присутствовали" in got and "| Alice A | Системный администратор | Отдел эксплуатации |" in got, got
        assert "Директор" not in got and "Правление" not in got, "исторический протокол не переписывается новой должностью"
        # повторное формирование — те же данные
        r2 = c.post(f"/api/v1/meetings/{mid}/protocols", json={"kind": "protocol", "instruction": "y"})
        _drain(c)
        again = c.get(f"/api/v1/meetings/{mid}/protocols/{r2.json()['protocol_id']}").json()["content"]
        assert "Системный администратор" in again and "Директор" not in again
        # DOCX/HTML строятся из того же текста
        html = c.get(f"/api/v1/meetings/{mid}/protocols/{r2.json()['protocol_id']}/export", params={"format": "html"}).text
        assert "Системный администратор" in html and "<table" in html
