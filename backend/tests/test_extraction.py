"""Структурное извлечение для небольшой локальной модели: разбор стенограммы, id участников, защита от смешения похожих ФИО, проверка пунктов по тексту (срок, ответственный,
значения, статус), слияние кодом, повтор оборвавшегося прохода меньшими частями, детерминированный Markdown и сроки. Без сети: подставной сервер на httpx.MockTransport."""
from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx

from app.integrations.llm import LlmClient
from app.services import extraction as ex
from app.services.deadlines import is_deadline_phrase, meeting_date, resolve
from app.services.settings import LlmSettings

HEADER = ("Переговорка: Тест\nДата: 2026-10-12 (понедельник)\nНачало: 10:00 — окончание: 10:30\n"
          "Участвовали: Анна Крылова, Иван Петров, Иван Петренко, Алексей Мороз, Алексей Морозов, Игорь Соколов, Ирина Соколова\n")
LINES = [
    "[10:00:05] Анна Крылова: Релиз переносим на вторник, двадцатое октября. Иван Петров, отдаёшь исправление прав в четверг к обеду.",
    "[10:00:20] Иван Петров: Да, в четверг к обеду отдам.",
    "[10:01:00] Иван Петренко: Окно выкладки согласую сегодня. Адрес стенда 192.0.2.55, порт 8443.",
    "[10:02:00] Алексей Мороз: Миграцию сделаю в ночь с пятницы на субботу.",
    "[10:02:30] Игорь Соколов: Предлагаю открыть ssh наружу.",
    "[10:02:40] Ирина Соколова: Категорически против, только через VPN. Не берём.",
    "[10:03:00] Анна Крылова: Сколько хранить логи, девяносто или сто восемьдесят дней, пока не знаем, это открытый вопрос.",
    "[10:03:30] Алексей Морозов: Помогу с компонентом загрузки, срок не назову.",
]
TEXT = HEADER + "\n".join(LINES) + "\n"


def parsed():
    h, ls = ex.parse_transcript(TEXT)
    names = ex.participants_of(h, ls)
    return h, ls, names, ex.people_of(names)


def stats0():
    return {k: 0 for k in ("bad_ts", "empty", "fabricated", "reclassified", "assignee_unverified", "assignee_unknown", "due_dropped", "timewords_removed", "length_retries",
                           "json_retries", "questions_unsupported", "duplicate_assignee", "assignee_from_text")}


def verify(kind, raw):
    h, ls, _n, people = parsed()
    st = stats0()
    return ex.verify_pass(kind, raw, ls, 1, people, st, meeting_date(h)), st


def llm_for(handler):
    seen: list[dict] = []

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        return handler(body, len(seen))

    c = LlmSettings(enabled=True, provider="external", type="openai_compatible", base_url="http://llm.test/v1", model="m", max_tokens=1200, allow_http=True)
    return LlmClient(c, transport=httpx.MockTransport(h), local=True), seen


def reply(obj, finish="stop"):
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}})


def pass_of(body):
    """Какой проход запросили (по ключу схемы)."""
    return next(iter(body["response_format"]["json_schema"]["schema"]["properties"]))


def answer(body, topics=(), decisions=(), tasks=(), questions=()):
    return {"topics": {"topics": list(topics)}, "items": {"items": list(decisions)}, "tasks": {"tasks": list(tasks)}, "questions": {"questions": list(questions)}}[pass_of(body)]


# ------------------------------------------------------------------------------------------------ разбор, id, имена
def test_parse_participants_ids_and_chunks():
    h, ls, names, people = parsed()
    assert len(ls) == len(LINES) and ls[0].ts == "10:00:05" and ls[0].speaker == "Анна Крылова" and len(names) == 7
    assert [p.pid for p in people] == ["p01", "p02", "p03", "p04", "p05", "p06", "p07"]
    assert ex.participants_of(["Участвовали: Борис, Галина (гость)"], [])[1] == "Галина"
    chunks = ex.chunk_lines(ls, 300)
    assert len(chunks) > 2 and sum(len(c) for c in chunks) == len(ls)
    assert meeting_date(h) == date(2026, 10, 12)


def test_similar_names_are_never_confused_by_mentions_and_the_prompt_uses_ids():
    _h, _l, names, people = parsed()
    byid = {p.pid: p.full for p in people}
    pairs = {frozenset((byid[a], byid[b])) for a, b in ex.similar_pairs(people)}
    assert frozenset({"Иван Петров", "Иван Петренко"}) in pairs and frozenset({"Алексей Мороз", "Алексей Морозов"}) in pairs and frozenset({"Игорь Соколов", "Ирина Соколова"}) in pairs
    assert {"Алексей Мороз", "Алексей Морозов"} <= ex.mentions("Алексею Морозу передай, а Морозова попроси помочь", people)[0]
    assert ex.mentions("Морозу нужно время", people)[0] == {"Алексей Мороз"} and ex.mentions("Морозова не трогай", people)[0] == {"Алексей Морозов"}
    assert ex.mentions("Соколовой скажи", people)[0] == {"Ирина Соколова"}
    f3, amb3 = ex.mentions("Соколову скажи", people)
    assert f3 == set() and amb3 is True, "«Соколову» бывает и мужским, и женским падежом — не подтверждение ни для Игоря, ни для Ирины"
    f2, amb2 = ex.mentions("Иван, сделай это", people)
    assert f2 == set() and amb2 is True, "имя без фамилии у двух людей — двусмысленно"
    assert ex.mentions("Иван Петренко сделает", people)[0] == {"Иван Петренко"}
    system = ex.system_for(people)
    assert "p02 — Иван Петров" in system and "p03 — Иван Петренко" in system and "Иван Петров ≠ Иван Петренко" in system and "Алексей Мороз ≠ Алексей Морозов" in system
    sch = ex.schema_for("tasks", [p.pid for p in people])
    assert sch["properties"]["tasks"]["items"]["properties"]["assignee"]["enum"] == [f"p0{n}" for n in range(1, 8)] + ["none"], "модель называет id, а не имя"


# ------------------------------------------------------------------------------------------------ сроки
def test_deadline_phrases_are_recognized_and_resolved_by_code():
    base = date(2026, 10, 12)       # понедельник
    for phrase, ok, expect in [("в четверг к обеду", True, date(2026, 10, 15)), ("до 23 октября", True, date(2026, 10, 23)), ("до двадцать третьего октября", True, date(2026, 10, 23)),
                               ("завтра", True, date(2026, 10, 13)), ("сегодня", True, base), ("до пятнадцатого", True, date(2026, 10, 15)), ("к следующей встрече", True, None),
                               ("10:19", False, None), ("после миграции", False, None), ("с 7 по 20 июля", False, None), ("в субботу утром", True, date(2026, 10, 17))]:
        assert is_deadline_phrase(phrase) is ok, phrase
        assert resolve(phrase, base) == expect, phrase
    assert resolve("завтра", None) is None


def test_task_assignee_is_a_participant_id_confirmed_by_the_text_and_deadline_only_if_said():
    items, st = verify("tasks", {"tasks": [
        {"assignee": "p02", "task": "отдать исправление прав", "deadline": "в четверг к обеду", "ts": "10:00:20"},     # назван и говорит; срок есть → дата
        {"assignee": "p03", "task": "согласовать окно выкладки на 192.0.2.99", "deadline": "сегодня", "ts": "10:01:00"},   # говорит сам; IP выдуман → значение убрано
        {"assignee": "p05", "task": "сделать миграцию", "deadline": "", "ts": "10:02:00"},                                  # реплика Мороза, а не Морозова → не назначается
        {"assignee": "p05", "task": "помочь с загрузкой файлов", "deadline": "до пятницы", "ts": "10:03:30"},              # «пятница» звучит в другой реплике → срок убирается
        {"assignee": "p99", "task": "сделать что-нибудь важное", "deadline": "", "ts": "10:03:00"},                         # такого участника нет
        {"assignee": "none", "task": "провести ревью", "deadline": "10:19", "ts": "10:03:00"},                             # время окончания встречи — не срок
    ]})
    by = {i.text: i for i in items}
    a = by["отдать исправление прав"]
    assert a.assignee == "Иван Петров" and a.assignee_id == "p02" and a.due == "в четверг к обеду" and a.due_date == "2026-10-15" and "отдаёшь исправление прав" in a.sources[0]["quote"]
    b = next(i for i in items if i.text.startswith("согласовать окно"))
    assert "192.0.2.99" not in b.text and b.assignee == "Иван Петренко" and b.due == "сегодня" and b.due_date == "2026-10-12" and st["fabricated"] == 1
    c = by["сделать миграцию"]
    assert c.assignee == "" and c.assignee_guess == "Алексей Морозов", "Мороз ≠ Морозов: неподтверждённый ответственный не назначается, предположение модели помечается"
    e = by["помочь с загрузкой файлов"]
    assert e.due == "" and st["due_dropped"] >= 2
    assert by["сделать что-нибудь важное"].assignee == "" and by["сделать что-нибудь важное"].assignee_doubtful is True and st["assignee_unknown"] == 1
    assert by["провести ревью"].due == ""


def test_same_task_is_not_given_to_two_people_in_one_reply():
    items, st = verify("tasks", {"tasks": [
        {"assignee": "p02", "task": "подтвердить даты ремонта", "deadline": "", "ts": "10:00:20"},
        {"assignee": "p01", "task": "подтвердить даты ремонта", "deadline": "", "ts": "10:00:20"},
    ]})
    assert len(items) == 1 and st["duplicate_assignee"] == 1


# ------------------------------------------------------------------------------------------------ статус: решение / предложение / вопрос
def test_decision_proposal_and_open_question_are_not_mixed_up():
    items, st = verify("decisions", {"items": [
        {"status": "decision", "text": "Открыть ssh наружу", "ts": "10:02:40"},                       # отклонено → предложение
        {"status": "decision", "text": "Сколько хранить логи?", "ts": "10:03:00"},                    # вопрос
        {"status": "decision", "text": "Открытый вопрос: хранить логи девяносто дней", "ts": "10:03:00"},
        {"status": "decision", "text": "Релиз переносим на вторник, двадцатое октября", "ts": "10:00:05"},    # настоящее решение
        {"status": "proposal", "text": "Открыть ssh наружу", "ts": "10:02:30"},
    ]})
    kinds = {}
    for i in items:
        kinds.setdefault(i.text, set()).add(i.kind)
    assert kinds["Сколько хранить логи?"] == {"open"} and kinds["хранить логи девяносто дней"] == {"open"}
    assert kinds["Открыть ssh наружу"] == {"proposal"} and kinds["Релиз переносим на вторник, двадцатое октября"] == {"decision"} and st["reclassified"] >= 3


def test_questions_without_a_question_in_the_text_are_dropped_and_task_texts_lose_invented_time_words():
    items, st = verify("questions", {"questions": [{"text": "Кто будет делать миграцию?", "ts": "10:02:00"},        # в реплике-источнике нет вопроса
                                                  {"text": "Сколько хранить логи", "ts": "10:03:00"}]})            # «открытый вопрос» в реплике
    assert [i.text for i in items] == ["Сколько хранить логи"] and st["questions_unsupported"] == 1
    items, st = verify("tasks", {"tasks": [{"assignee": "p04", "task": "сделать миграцию до конца недели в августе", "deadline": "", "ts": "10:02:00"}]})
    assert items[0].text == "сделать миграцию" and st["timewords_removed"] == 2


def test_invalid_timestamp_items_are_dropped_and_quote_comes_from_the_transcript():
    items, st = verify("decisions", {"items": [{"status": "decision", "text": "Что-то нереальное", "ts": "23:59:59"},
                                                  {"status": "decision", "text": "Релиз переносим на вторник", "ts": "10:00:07"}]})
    assert [i.text for i in items] == ["Релиз переносим на вторник"] and st["bad_ts"] == 1
    assert items[0].ts == "10:00:05" and items[0].sources[0]["quote"].startswith("Релиз переносим"), "цитату берёт код по времени, а не модель"


# ------------------------------------------------------------------------------------------------ слияние кодом
def test_merge_dedupes_but_never_joins_different_owners_and_drops_decision_that_repeats_a_task():
    def mk(kind, text, aid="", name="", sec=1, frag=1):
        return ex.Item(kind, text, "10:00:%02d" % sec, sec, name, aid, True, False, "", "", "", [{"fragment": frag, "ts": "10:00:%02d" % sec, "speaker": "", "quote": text}])

    items = [mk("task", "обновить OpenSSL на серверах приложений", "p03", "Иван Петренко", 1, 1), mk("task", "обновить OpenSSL на серверах приложений", "p03", "Иван Петренко", 90, 2),
             mk("task", "обновить OpenSSL на серверах приложений", "p02", "Иван Петров", 95, 2),
             mk("decision", "Иван Петренко обновить OpenSSL на серверах приложений", "", "", 2, 1), mk("decision", "Тесты не отключаем", "", "", 3, 1), mk("decision", "Тесты не отключаем", "", "", 100, 3)]
    out = ex.merge_items(items)
    tasks = [i for i in out if i.kind == "task"]
    assert len(tasks) == 2 and {t.assignee for t in tasks} == {"Иван Петренко", "Иван Петров"}
    assert len(next(t for t in tasks if t.assignee == "Иван Петренко").sources) == 2
    assert [i.text for i in out if i.kind == "decision"] == ["Тесты не отключаем"]


# ------------------------------------------------------------------------------------------------ вывод
def test_markdown_has_sources_and_honest_marks_for_unconfirmed_owner_and_missing_due():
    def src(ts, who, q):
        return [{"fragment": 1, "ts": ts, "speaker": who, "quote": q}]

    items = [ex.Item("task", "отдать исправление прав", "10:00:20", 20, "Иван Петров", "p02", True, False, "", "в четверг к обеду", "2026-10-15", src("10:00:20", "Иван Петров", "Да, в четверг к обеду отдам.")),
             ex.Item("task", "сделать миграцию", "10:02:00", 120, "", "", True, False, "Алексей Морозов", "", "", src("10:02:00", "Алексей Мороз", "Миграцию сделаю | ночью")),
             ex.Item("proposal", "Открыть ssh наружу", "10:02:30", 150, sources=src("10:02:30", "Игорь Соколов", "Предлагаю открыть ssh наружу.")),
             ex.Item("open", "Сколько хранить логи", "10:03:00", 180, sources=src("10:03:00", "Анна Крылова", "пока не знаем"))]
    md = ex.render_markdown(["Переговорка: Тест", "Дата: 2026-10-12"], items)
    assert "| Иван Петров | отдать исправление прав | в четверг к обеду (2026-10-15) | [10:00:20] Иван Петров «Да, в четверг к обеду отдам.»" in md
    assert "не определён (модель предположила: Алексей Морозов; в репликах не подтверждено)" in md and "| не указан |" in md
    assert "## Предложения, не принятые как решение" in md and "Открыть ssh наружу" in md and "## Открытые вопросы" in md and "Сколько хранить логи" in md
    assert "Миграцию сделаю / ночью" in md, "символ | в цитате не ломает таблицу"
    st = ex.to_structured(items)
    t = st["tasks"][0]
    assert t["assignee"] == "Иван Петров" and t["assignee_id"] == "p02" and t["deadline"] == "2026-10-15" and t["deadline_phrase"] == "в четверг к обеду" and t["source"]["ts"] == "10:00:20" and t["source"]["chunk"] == 1
    assert st["tasks"][1]["assignee"] is None and st["tasks"][1]["assignee_guess"] == "Алексей Морозов"


# ------------------------------------------------------------------------------------------------ полный путь с подставной моделью
def run(llm, **kw):
    base = dict(kind="protocol", instruction="x", text=TEXT, limit=100000)
    base.update(kw)
    return asyncio.run(ex.structured_pipeline(llm, **base))


def good(body, n):
    return reply(answer(body, topics=[{"title": "Релиз", "ts": "10:00:05"}], decisions=[{"status": "decision", "text": "Релиз переносим на вторник, двадцатое октября", "ts": "10:00:05"},
                                                                                      {"status": "proposal", "text": "Открыть ssh наружу", "ts": "10:02:30"}],
                          tasks=[{"assignee": "p02", "task": "отдать исправление прав", "deadline": "в четверг к обеду", "ts": "10:00:20"}],
                          questions=[{"text": "Сколько хранить логи", "ts": "10:03:00"}]))


def test_pipeline_runs_narrow_passes_with_a_common_prefix_and_builds_a_deterministic_protocol():
    llm, seen = llm_for(good)
    res = run(llm)
    assert len(seen) == 4 and res.calls == 4 and res.parts == 1 and res.ok_fragments == 4 and not res.truncated
    assert [pass_of(b) for b in seen] == ["topics", "items", "tasks", "questions"], "темы / решения / поручения / вопросы — отдельные проходы"
    assert len({b["messages"][0]["content"] for b in seen}) == 1, "системный промпт одинаков во всех проходах (llama.cpp не обрабатывает фрагмент заново)"
    frag_part = [b["messages"][1]["content"].split("\n\n=== ЗАДАНИЕ")[0] for b in seen]
    assert len(set(frag_part)) == 1 and all(b["temperature"] == 0.0 and b["max_tokens"] <= 800 for b in seen)
    assert "p02 — Иван Петров" in seen[0]["messages"][0]["content"]
    assert "| Иван Петров | отдать исправление прав | в четверг к обеду (2026-10-15) |" in res.text and "Открыть ssh наружу" in res.text and "| 1 | Релиз переносим" in res.text
    assert res.structured["tasks"][0]["assignee"] == "Иван Петров" and res.structured["tasks"][0]["source"]["ts"] == "10:00:05" and res.structured["stats"]["fragments"] == 1


def test_truncated_pass_is_split_and_retried_instead_of_using_cut_data():
    state = {"cut": 0}

    def handler(body, n):
        if pass_of(body) == "items" and state["cut"] == 0:
            state["cut"] = 1
            return reply('{"items": [{"status": "deci', finish="length")                  # оборвано
        return good(body, n)

    llm, seen = llm_for(handler)
    res = run(llm)
    assert res.truncated is True and res.failed == 0 and res.calls == 6          # 3 прохода как обычно + оборванный + две половины
    assert any("пришлось повторить" in w and "лимиту длины — 1" in w for w in res.warnings), res.warnings
    assert res.structured["stats"]["length_retries"] == 1 and "Релиз переносим на вторник" in res.text
    sizes = [len(b["messages"][1]["content"]) for b in seen if pass_of(b) == "items"]
    assert sizes[1] < sizes[0] and sizes[2] < sizes[0], "повтор идёт меньшими частями"


def test_pass_that_never_parses_is_reported_and_pipeline_stops_early_when_the_first_one_fails():
    llm, seen = llm_for(lambda b, n: reply("это не JSON"))
    res = run(llm)
    assert res.ok_fragments == 0 and res.failed >= 1 and len(seen) <= 8, "первый проход не разобрался даже по частям — остальные не мучаем"


def test_summary_is_written_from_verified_facts_and_falls_back_when_cut_off():
    def handler(body, n):
        if "response_format" in body:
            return good(body, n)
        return reply("Релиз перенесли на вторник. Иван Петров отдаёт исправление прав в четверг к обеду.")

    llm, seen = llm_for(handler)
    res = run(llm, kind="summary")
    assert res.calls == 5 and res.text.startswith("Релиз перенесли")
    facts = seen[-1]["messages"][1]["content"]
    assert "Решение: Релиз переносим на вторник" in facts and "Поручение: Иван Петров — отдать исправление прав (срок: в четверг к обеду)" in facts and "стенограмм" not in facts.lower()
    llm, _ = llm_for(lambda body, n: good(body, n) if "response_format" in body else reply("Обрыв", finish="length"))
    res = run(llm, kind="summary")
    assert res.truncated and "Решения: Релиз переносим на вторник" in res.text and any("оборвала" in w for w in res.warnings)


# ------------------------------------------------------------------------------------------------ строгие проверки (разбор часовой встречи на настоящей модели)
FRAG_LINES = [
    "[10:00:00] Анна Крылова: Следующее: нестабильные автотесты. Иван Петренко, расскажи.",
    "[10:00:10] Иван Петренко: Тесты падают раз в несколько прогонов, надо разбираться.",
    "[10:00:30] Анна Крылова: Борис добавляет метрику по кодам ответов до конца недели.",
    "[10:00:50] Иван Петров: Окно выкладки согласую завтра до обеда.",
    "[10:01:10] Анна Крылова: Двадцать минут простоя ночью — это приемлемо?",
    "[10:01:20] Иван Петренко: Да, приемлемо.",
    "[10:01:40] Анна Крылова: Итак: тесты не отключаем, это решение принято.",
    "[10:02:00] Игорь Соколов: Давайте поставим вторую базу рядом.",
]


def verify_frag(kind, raw):
    ls = [ex.parse_transcript(HEADER + "\n".join(FRAG_LINES) + "\n")][0][1]
    names = ex.participants_of([], ls) + ["Борис"]
    people = ex.people_of(names)
    st = stats0()
    return ex.verify_pass(kind, raw, ls, 1, people, st, date(2026, 10, 12)), st, people


def test_a_speaker_is_not_the_assignee_unless_he_takes_the_task_himself():
    items, st, _p = verify_frag("tasks", {"tasks": [
        {"assignee": "p01", "task": "провести анализ нестабильных автотестов", "deadline": "", "ts": "10:00:00"},     # Анна просто объявляет тему → не её задача
        {"assignee": "p03", "task": "согласовать окно выкладки", "deadline": "завтра до обеда", "ts": "10:00:50"},    # говорит Иван Петров, а модель назвала Петренко
    ]})
    by = {i.text: i for i in items}
    assert by["провести анализ нестабильных автотестов"].assignee == "Иван Петренко", "в реплике-источнике назван ровно один участник — ответственный берётся из текста"
    assert by["согласовать окно выкладки"].assignee == "Иван Петров" and by["согласовать окно выкладки"].due == "завтра до обеда" and st["assignee_from_text"] >= 1


def test_a_name_in_a_neighbouring_line_does_not_confirm_an_assignee_and_a_named_person_in_the_source_does():
    items, _st, _p = verify_frag("tasks", {"tasks": [
        {"assignee": "p02", "task": "добавить метрику по кодам ответов", "deadline": "до конца недели", "ts": "10:00:30"},     # модель назвала Петрова; в реплике назван Борис
    ]})
    t = items[0]
    assert t.assignee == "Борис" and t.assignee_guess == "" and t.due == "до конца недели", "ответственный из текста реплики («Борис добавляет»), а не догадка модели"


def test_decision_words_in_the_line_promote_a_proposal_and_a_rhetorical_question_is_not_an_open_question():
    items, _st, _p = verify_frag("decisions", {"items": [
        {"status": "proposal", "text": "Тесты не отключаем", "ts": "10:01:40"},          # модель назвала решение предложением
        {"status": "proposal", "text": "Поставить вторую базу рядом", "ts": "10:02:00"},    # настоящее предложение
    ]})
    kinds = {i.text: i.kind for i in items}
    assert kinds["Тесты не отключаем"] == "decision" and kinds["Поставить вторую базу рядом"] == "proposal"
    q, st, _p = verify_frag("questions", {"questions": [{"text": "Приемлемо ли двадцать минут простоя", "ts": "10:01:10"}]})
    assert q == [] and st["questions_unsupported"] == 1, "вопрос с «?» и ответом сразу ниже — не открытый"


# ------------------------------------------------------------------------------------------- разбор 20-минутной встречи (стенд 1.7B): ошибки ответственных и мусор в предложениях
LINES2 = [
    "[10:06:31] Анна Крылова: Хорошо. Миграция в ночь с пятницы на субботу, делает Иван Петров. Предложение Бориса про репликацию не берём, слишком долго.",
    "[10:02:21] Анна Крылова: Решили. Релиз переносим на вторник. Борис, отдаёшь исправление Игорю Соколову в понедельник, не позже.",
    "[10:18:34] Анна Крылова: Тогда не решаем сейчас. Иван Петренко, подготовь оценку трудоёмкости обновления, какие библиотеки затрагиваются.",
    "[10:03:29] Анна Крылова: Все знают, да. Идём дальше.",
    "[10:04:00] Игорь Соколов: Может, поставим вторую базу рядом, это можно сделать за день.",
]


def verify2(kind, raw):
    ls = ex.parse_transcript(HEADER + "\n".join(LINES2) + "\n")[1]
    people = ex.people_of(ex.participants_of([], ls) + ["Борис", "Иван Петров", "Иван Петренко"])
    st = stats0()
    pid = {p.full: p.pid for p in people}
    raw = {k: [{**row, "assignee": pid.get(row.get("assignee"), row.get("assignee"))} if isinstance(row, dict) else row for row in v] for k, v in raw.items()}
    return ex.verify_pass(kind, raw, ls, 1, people, st, date(2026, 10, 12)), st, people


def test_a_name_in_the_genitive_or_dative_is_not_the_owner():
    items, st, people = verify2("tasks", {"tasks": [
        {"assignee": "Борис", "task": "выполнить миграцию", "deadline": "", "ts": "10:06:31"},       # модель назвала Бориса: «предложение Бориса» — не он делает
        {"assignee": "Анна Крылова", "task": "отдать исправление Игорю", "deadline": "", "ts": "10:02:21"},    # модель назвала говорящую
    ]})
    names = {p.pid: p.full for p in people}
    by = {i.text: i for i in items}
    assert by["выполнить миграцию"].assignee == "Иван Петров", "делает Иван Петров (именительный), а не «предложение Бориса»"
    assert by["отдать исправление Игорю"].assignee == "Борис", "«Борис, отдаёшь …» — звательная форма; Игорь — получатель (дательный)"
    assert st["assignee_from_text"] == 2 and names


def test_a_noun_ending_in_u_is_not_a_first_person_commitment():
    items, _st, people = verify2("tasks", {"tasks": [{"assignee": "Анна Крылова", "task": "подготовить оценку трудоёмкости", "deadline": "", "ts": "10:18:34"}]})
    t = items[0]
    assert t.assignee == "Иван Петренко", "«Иван Петренко, подготовь …» — адресат; «оценку» — существительное, говорящая Анна обязательства не берёт"
    assert ex._first_person_commitment("Дамп сделаю и проверю восстановление") and not ex._first_person_commitment("Нужна оценка и проверка")
    assert not ex._first_person_commitment("подготовь оценку и сборку") and ex._first_person_commitment("Я подготовлю оценку")


def test_proposals_need_a_proposal_cue_and_leaked_prompt_text_is_removed():
    items, st, _p = verify2("decisions", {"items": [
        {"status": "proposal", "text": "Все знают, да", "ts": "10:03:29"},
        {"status": "proposal", "text": "Поставить вторую базу рядом, если не утверждено. Время реплики: [10:04:00] Игорь", "ts": "10:04:00"},
    ]})
    assert [i.text for i in items] == ["Поставить вторую базу рядом"] and st.get("proposals_unsupported") == 1


def test_an_open_question_that_is_not_a_question_is_dropped_and_damaged_text_is_not_kept():
    items, st, _p = verify2("decisions", {"items": [{"status": "open", "text": "Открытый вопрос: ставить ли вторую базу", "ts": "10:04:00"}]})
    assert items == [] and st["questions_unsupported"] == 1
    items, st, _p = verify2("decisions", {"items": [{"status": "decision", "text": "Обновить приложение на версию 12.34.56.78 , если можно", "ts": "10:04:00"}]})
    assert items == [] and st.get("damaged_dropped") == 1


RECAP = ["[10:18:58] Анна Крылова: Давайте подведём итоги. Виктор увеличивает память до шестнадцати гигабайт, Галина чинит два нестабильных теста до среды, Дмитрий помогает с компонентом загрузки, Елена уточняет срок хранения логов."]


def verify_recap(task, assignee):
    ls = ex.parse_transcript(HEADER + "\n".join(RECAP) + "\n")[1]
    people = ex.people_of(["Анна Крылова", "Виктор Орлов", "Галина Белова", "Дмитрий Фёдоров", "Елена Морозова"])
    pid = {p.full: p.pid for p in people}
    st = stats0()
    items = ex.verify_pass("tasks", {"tasks": [{"assignee": pid[assignee], "task": task, "deadline": "", "ts": "10:18:58"}]}, ls, 1, people, st, date(2026, 10, 12))
    return items[0], st


def test_in_a_recap_line_with_many_names_the_task_goes_to_the_person_standing_next_to_it():
    t, st = verify_recap("чинить два нестабильных теста", "Дмитрий Фёдоров")        # модель перепутала: тесты чинит Галина
    assert t.assignee == "Галина Белова" and st["assignee_reassigned"] == 1
    t, st = verify_recap("увеличить память до шестнадцати гигабайт", "Виктор Орлов")  # назван верно — остаётся
    assert t.assignee == "Виктор Орлов" and st.get("assignee_reassigned", 0) == 0
    t, _ = verify_recap("уточнить срок хранения логов", "Елена Морозова")
    assert t.assignee == "Елена Морозова"


def test_when_the_task_words_are_not_near_any_name_the_owner_stays_unconfirmed():
    t, st = verify_recap("подготовить презентацию для руководства", "Дмитрий Фёдоров")
    assert t.assignee_guess in ("", "Дмитрий Фёдоров") and (t.assignee == "" or t.assignee == "Дмитрий Фёдоров")
    assert ex.association_distances(RECAP[0], "чинить два нестабильных теста", ex.people_of(["Галина Белова", "Дмитрий Фёдоров"]))["Галина Белова"] <= 2


REAL_RECAP = ("[10:18:58] Анна Крылова: Давайте подведём итоги. Релиз переносим на вторник. Борис отдаёт исправление прав в понедельник к обеду. "
              "Виктор в среду вечером увеличивает память до шестнадцати гигабайт, в ночь с пятницы на субботу мигрирует базу. Завтра до обеда подаёт заявку на сертификат. "
              "Галина чинит два теста до среды, Дмитрий помогает. Елена уточняет вопрос по логам у юристов.")


def verify_real(task, assignee):
    ls = ex.parse_transcript(HEADER + REAL_RECAP + "\n")[1]
    people = ex.people_of(["Анна Крылова", "Борис Мельник", "Виктор Орлов", "Галина Белова", "Дмитрий Фёдоров", "Елена Морозова"])
    pid = {p.full: p.pid for p in people}
    st = stats0()
    items = ex.verify_pass("tasks", {"tasks": [{"assignee": pid[assignee], "task": task, "deadline": "", "ts": "10:18:58"}]}, ls, 1, people, st, date(2026, 10, 12))
    return items[0]


def test_recap_names_are_matched_within_the_sentence_and_deadline_words_do_not_attract_names():
    t = verify_real("чинить два теста до среды", "Дмитрий Фёдоров")        # «до среды» стоит рядом с «Дмитрий», но задача — Галины
    assert t.assignee == "Галина Белова"
    t = verify_real("подать заявку на сертификат", "Галина Белова")          # в этом предложении имени нет (продолжение цепочки Виктора): не гадаем
    assert t.assignee == "" and t.assignee_guess == "Галина Белова"
    assert verify_real("увеличить память до шестнадцати гигабайт", "Виктор Орлов").assignee == "Виктор Орлов"
    assert verify_real("отдать исправление прав", "Борис Мельник").assignee == "Борис Мельник"
