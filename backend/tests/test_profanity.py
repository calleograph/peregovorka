"""Маскирование нецензурной лексики: словоформы и варианты написания скрываются, обычные слова с похожими сочетаниями букв — нет; обработка быстрая."""
from __future__ import annotations

import time

from app.services import profanity as p

BAD = ("хуй хуйня нахуй похуй охуеть охуенно хуево пизда пиздец спиздить распиздяй пездец ебать заебал заебешь ебаный ёбаный уебок уебище долбоеб долбоёб наебал "
       "выебон заебись ебло ебучий съебался въебать бля блять блядь выблядок мудак мудила манда залупа гандон гондон пидор пидорас педераст педик сука суки сукин "
       "сучара сучонок сучка шлюха дрочить нахер похер херня охереть ХУЙ Xуй бляяяять сУкА пИзДеЦ").split()
GOOD = ("команда мандарин мандат себе себя небо небе хлеб требовать вебинар веб бляха сук рубля корабля оскорблять мудрый мудрость застрахуйте страхуй хулиган хуже "
        "худой херувим Херсон пиджак педиатр педикюр педагог сукно сукровица тебе гребля употреблять скипидар хутор художник учебник ребёнок ребята небеса победа "
        "обед Себастьян сучья сучок вебе учебный ребро ребус оскорбление послеобеденный").split()


def test_profane_forms_and_spellings_are_detected():
    assert [w for w in BAD if not p.is_profane(w)] == []


def test_ordinary_words_are_not_masked():
    assert [w for w in GOOD if p.is_profane(w)] == []


def test_mask_keeps_length_punctuation_and_other_words():
    text = "Ну это пиздец какой-то, заебали уже с этим бюджетом, сука! Команда, давайте сроки сдвинем."
    out = p.mask(text)
    assert out == "Ну это ****** какой-то, ******* уже с этим бюджетом, ****! Команда, давайте сроки сдвинем."
    assert len(out) == len(text)
    clean = "Коллеги, обсудим бюджет на следующий квартал."
    assert p.mask(clean) is clean, "без совпадений строка не копируется"
    assert p.mask(None) is None and p.mask("") == ""
    assert p.maybe(text, False) is text


def test_mask_is_fast_enough_for_live_transcript():
    clean = "Коллеги, давайте обсудим бюджет на следующий квартал и сроки сдачи отчёта по проекту, Борис подготовит смету."
    dirty = "Ну это пиздец какой-то, заебали уже с этим бюджетом, сука, давайте сроки сдвинем."
    n = 2000
    t = time.perf_counter()
    for _ in range(n):
        p.mask(clean)
    per_clean = (time.perf_counter() - t) / n
    t = time.perf_counter()
    for _ in range(n):
        p.mask(dirty)
    per_dirty = (time.perf_counter() - t) / n
    assert per_clean < 100e-6 and per_dirty < 1e-3, (per_clean, per_dirty)          # с большим запасом: на стенде ~6 мкс и ~70 мкс


# ------------------------------------------------------------------ настройка комнаты и выдача
from app.services import events as _events  # noqa: E402
from app.services.segments import ingest_segment  # noqa: E402

from .conftest import login, make_room  # noqa: E402
from .test_mail import LEADERS  # noqa: E402
from .test_transcripts import _feed_and_consume, _join, _segment  # noqa: E402

API = "/api/v1"
PHRASE = "Ну это пиздец, давайте сроки сдвинем"
MASKED = "Ну это ******, давайте сроки сдвинем"


def test_room_setting_masks_live_history_subtitles_and_exports_without_reasr(client):
    room = make_room(client, moderators=LEADERS)
    assert room["mask_profanity"] is True, "по умолчанию включено"
    a = _join(client, "alice", room["id"])
    _feed_and_consume(client, [_segment(a["meeting_id"], a["identity"], PHRASE)])
    mid = a["meeting_id"]
    seg = client.get(f"{API}/meetings/{mid}/transcript").json()["segments"][0]
    assert seg["text"] == MASKED and seg["display_name"] and seg["started_at"]
    assert MASKED in client.get(f"{API}/meetings/{mid}/transcript.txt").text
    assert "пиздец" not in client.get(f"{API}/meetings/{mid}/transcript/export", params={"format": "txt"}).text
    # руководитель выключает — та же стенограмма показывается как есть, без повторного распознавания
    login(client, "carol")
    assert client.patch(f"{API}/rooms/{room['id']}/manage", json={"mask_profanity": False}).json()["mask_profanity"] is False
    login(client, "alice")
    assert client.get(f"{API}/meetings/{mid}/transcript").json()["segments"][0]["text"] == PHRASE
    login(client, "carol")
    client.patch(f"{API}/rooms/{room['id']}/manage", json={"mask_profanity": True})
    login(client, "alice")
    assert client.get(f"{API}/meetings/{mid}/transcript").json()["segments"][0]["text"] == MASKED
    # обычный участник настройку не меняет
    assert client.patch(f"{API}/rooms/{room['id']}/manage", json={"mask_profanity": False}).status_code in (403, 404)


def test_live_event_is_masked_and_ingest_latency_unchanged(client, capsys):
    """Живое событие с репликой уже замаскировано; задержка приёма реплики с включённой и выключенной настройкой практически одинакова (замер)."""
    import time
    import uuid

    room = make_room(client, moderators=LEADERS)
    a = _join(client, "alice", room["id"])
    mid = a["meeting_id"]
    app = client.app_obj
    seen: list[dict] = []
    orig = _events.publish

    async def spy(redis, meeting_id, event):
        seen.append(event)
        return await orig(redis, meeting_id, event)
    _events.publish = spy
    try:
        def ingest_many(n: int) -> float:
            async def go():
                t = time.perf_counter()
                async with app.state.session_maker() as db:
                    for i in range(n):
                        f = _segment(mid, a["identity"], PHRASE + f" {i}", offset=i * 3)
                        f["segment_uid"] = str(uuid.uuid4())
                        await ingest_segment(db, app.state.redis, f)
                return (time.perf_counter() - t) / n
            return client.portal.call(go)
        ingest_many(5)                                                                  # прогрев
        on = min(ingest_many(60) for _ in range(3))
        async def off_flag(v: bool):
            from app.models import Room
            async with app.state.session_maker() as db:
                r = await db.get(Room, uuid.UUID(room["id"]))
                r.mask_profanity = v
                await db.commit()
        client.portal.call(lambda: off_flag(False))
        off = min(ingest_many(60) for _ in range(3))
    finally:
        _events.publish = orig
    masked = [e for e in seen if e.get("type") == "segment" and e["segment"]["text"].startswith("Ну это ******")]
    assert masked, "живое событие уходит уже замаскированным"
    with capsys.disabled():
        print(f"\n[замер] приём реплики: маскирование включено {on * 1000:.3f} мс, выключено {off * 1000:.3f} мс, разница {(on - off) * 1000:+.3f} мс")
    assert on - off < 0.5e-3, (on, off)
