"""Регрессия распознавания людей на стенограммах: ложные «Лицо N» из междометий, ролей и обрывков, границы реплик, согласованность обозначений.
Набор и измеритель — tests/privacy_corpus.py (синтетические данные)."""
from __future__ import annotations

import pytest

from app.privacy.engine import Anonymizer, Policy

from .privacy_corpus import HOLDOUT_PEOPLE, PARTICIPANTS, build, evaluate, holdout, summary

CONTROLS = ["Угу", "Ага", "Ого", "Хм", "Хмм", "Да-да-да", "Раз-раз-раз", "Ток-ток", "Бизнес-заказчик", "Закупщик", "Ахо", "Слеживать", "Стрелочки",
            "Проблинка", "Полноплан", "Соорганизует", "Отделён", "Нету", "Потерялся", "Переносишься", "Возвращаешь"]


def run(text: str, profile: str, people=PARTICIPANTS):
    return Anonymizer(Policy.from_profile(profile), people).process(text)


@pytest.mark.parametrize("profile", ["basic", "strict"])
def test_interjections_roles_and_asr_fragments_stay_as_speech(profile):
    lines = "".join(f"[00:{i // 60:02d}:{i % 60:02d}] {PARTICIPANTS[i % 2]}: {w}. Дальше по плану.\n" for i, w in enumerate(CONTROLS))
    res = run(lines, profile)
    for w in CONTROLS:
        assert f": {w}. Дальше по плану." in res.text, w
    assert not [r for r in res.replacements if r.category == "person" and r.original in CONTROLS]
    assert {c["reason"] for c in res.candidates} - {""}, "спорные кандидаты видны лаборатории с причиной"


@pytest.mark.parametrize("profile", ["basic", "strict"])
def test_speaker_name_never_swallows_the_first_word_of_the_utterance(profile):
    text = "".join(f"[00:00:{10 + i}] {PARTICIPANTS[0]}: {w}\n" for i, w in enumerate(["Подрядчика выбрали?", "Согласен, давайте.", "Шах и мат."]))
    out = run(text, profile).text.splitlines()
    alias = "Андрей В." if profile == "basic" else "Участник 1"
    assert out == [f"[00:00:10] {alias}: Подрядчика выбрали?", f"[00:00:11] {alias}: Согласен, давайте.", f"[00:00:12] {alias}: Шах и мат."]


def test_false_candidates_do_not_take_person_numbers():
    res = run("Угу. Хм. Закупщик сказал. Раз-раз-раз. Кузнецов пришёл. Ого. Потом Кузнецову передали.", "strict", [])
    assert res.text == "Угу. Хм. Закупщик сказал. Раз-раз-раз. Лицо 1 пришёл. Ого. Потом Лицу 1 передали."


def test_one_person_one_label_and_different_people_are_not_merged():
    res = run("Хорошо. Полякова — бюджет. Наталья Сергеевна, а Иван Петрович Петровой Марии передал акт? Петрова его подписала.", "strict",
              ["Полякова Наталья Сергеевна"])
    assert res.text == "Хорошо. Участник 1 — бюджет. Участник 1, а Лицо 2 Лицу 3 передал акт? Лицо 3 его подписала."


@pytest.mark.parametrize("quality", ["high", "medium", "low"])
@pytest.mark.parametrize("profile", ["basic", "strict"])
def test_regression_transcript_quality(quality, profile):
    c = build(300, quality)
    r = evaluate(c, run(c.text, profile))
    assert r.false_pos == 0 and r.lost_chars == 0 and r.broken_lines == 0, summary(r)
    assert r.controls_kept == r.controls_total, (summary(r), r.controls_lost)
    assert not r.inconsistent and not r.merged, (r.inconsistent, r.merged)
    assert r.recall >= (0.98 if quality == "low" else 1.0), (summary(r), r.missed_words)      # без заглавных — строчные инициалы «с. п.» остаются


@pytest.mark.parametrize("profile", ["basic", "strict"])
def test_hand_written_holdout_transcript(profile):
    c = holdout()
    r = evaluate(c, run(c.text, profile, HOLDOUT_PEOPLE))
    assert r.false_pos == 0 and r.lost_chars == 0 and r.broken_lines == 0 and r.controls_kept == r.controls_total, summary(r)
    assert not r.inconsistent and not r.merged, (r.inconsistent, r.merged)
    assert r.recall >= 0.9, (summary(r), r.missed_words)       # «Мельник» (только обычное слово в словаре) и «Ли» (в словаре — имя) — известные пропуски


def test_lab_shows_evidence_disputed_candidates_and_rechecks_with_an_exception(client):
    from .conftest import login

    login(client, "root")
    text = f"[00:00:05] {PARTICIPANTS[0]}: Угу. Закупщик сказал, что Кузнецов придёт.\n[00:00:09] {PARTICIPANTS[1]}: Хм. Кузнецову передам.\n"
    r = client.post("/api/v1/admin/privacy/lab", json={"text": text, "profile": "strict", "participants": PARTICIPANTS})
    assert r.status_code == 200, r.text
    out = r.json()
    people = {x["original"]: x["evidence"] for x in out["replacements"] if x["category"] == "person"}
    assert people[PARTICIPANTS[0]] == "участник встречи" and people["Кузнецов"] and "Угу" not in people and "Закупщик" not in people
    disputed = {c["original"]: c["reason"] for c in out["candidates"]}
    assert disputed.get("Угу") == "междометие" and all(text[c["start"]:c["end"]] == c["original"] for c in out["candidates"])
    again = client.post("/api/v1/admin/privacy/lab", json={"text": text, "profile": "strict", "participants": PARTICIPANTS, "draft": {"exceptions": ["Кузнецов"]}}).json()
    assert "Кузнецов придёт" in again["text"] and "Кузнецову передам" in again["text"], "исключение действует во всех формах слова"
