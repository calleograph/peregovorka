"""Стенд для живого smoke-test: backend с подставными AD/Redis, SQLite и настоящим LiveKit (ws://127.0.0.1:7880). Запуск: python tests/live/devserver_live.py"""
import base64, os, sys, uuid, datetime as dt
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "tests"))
import sqlalchemy as sa, uvicorn
from fakeredis import FakeAsyncRedis
from sqlalchemy.orm import Session
from app.config import Settings
from app.main import create_app
from app.models import Base, Meeting, MeetingParticipant, Room, RoomAcl, RoomModerator, TranscriptSegment, User, utcnow
from conftest import FakeDirectory, ADMIN_GROUP, STAFF_GROUP
from app.services import transfer as _tr
if os.environ.get('LIVE_FIXED_CAPTCHA'):          # только стенд: ответ CAPTCHA известен сценарию (алфавит из одного символа)
    from app.security import imgcaptcha as _ic
    _ic.ALPHABET = 'A'
_tr.SETTLE_S, _tr.RECENT_S, _tr.GRACE_S, _tr.READ_QUIET_S = 0, 0, 3, 4          # стенд: перенос без ожидания «устоявшейся» встречи, отсрочка удаления источника — секунды

DATA = os.environ.get("LIVE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA, exist_ok=True)
for f in os.listdir(DATA):
    if f.endswith(".db"):
        os.remove(os.path.join(DATA, f))
d = FakeDirectory()
d.add("root", "root-pass", groups=(ADMIN_GROUP,), name="Администратор")
ALICE_GUID = "11111111-1111-4111-8111-111111111111"
d.add("alice", "alice-pass", name="Алиса Крылова", guid=ALICE_GUID)
d.add("bob", "bob-pass", name="Борис Мартынов")
d.add("dave", "dave-pass", name="Дмитрий Соколов")      # сотрудник без участия во встречах — получатель доступа в scenario_room_leader
s = Settings(database_url=f"sqlite+aiosqlite:///{DATA}/dev.db", redis_url="redis://x", app_public_url="http://localhost:5173", cookie_secure=False,
             livekit_api_key="devkey", livekit_api_secret="s" * 40, livekit_public_url="ws://127.0.0.1:7880", livekit_internal_url="http://127.0.0.1:7880",
             app_master_key=base64.b64encode(os.urandom(32)).decode(), internal_api_token="t", ldap_admin_group_dn=ADMIN_GROUP, data_dir=DATA,
             docs_enabled=False, log_level="WARNING", segment_consumer_block_ms=50, app_version=open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "VERSION"), encoding="utf-8").read().strip(), app_git_commit="abcdef012345")
app = create_app(s, redis_factory=lambda _: FakeAsyncRedis(decode_responses=True), directory_factory=lambda _: d)
if os.environ.get("LIVE_MOCK_LLM") == "1":          # подставная модель вместо провайдера: документы и карта строятся без сети (см. mock_llm.py)
    import httpx
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mock_llm
    app.state.test_transports = {"llm": httpx.MockTransport(mock_llm.handler)}
if os.environ.get("LIVE_FAKE_EGRESS") == "1":        # вместо LiveKit Egress: при остановке ffmpeg создаёт настоящий короткий MP4 (H.264 + AAC), затем «приходит» итог. Служба Egress НЕ проверяется
    import asyncio as _aio, subprocess as _sp, types as _types
    from livekit.protocol import egress as _eg
    os.environ["VIDEO_EGRESS_OUT"] = os.path.join(DATA, "recordings")
    class _FakeEgress:
        def __init__(self): self.jobs = {}; self.n = 0
        async def start(self, req):
            self.n += 1; eid = f"EG_stand{self.n}"; self.jobs[eid] = req.outputs[0].file.filepath
            return _eg.EgressInfo(egress_id=eid, status=_eg.EgressStatus.EGRESS_ACTIVE)
        async def stop(self, egress_id):
            path = self.jobs.pop(egress_id, None)
            if path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _sp.run(["/usr/bin/ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
                         "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path], check=True)
                info = _eg.EgressInfo(egress_id=egress_id, status=_eg.EgressStatus.EGRESS_COMPLETE)
                info.file_results.add(duration=6_000_000_000, size=os.path.getsize(path))
                async def _later():
                    await _aio.sleep(1)
                    async with app.state.session_maker() as db:
                        await app.state.video.on_egress_event(db, _types.SimpleNamespace(event="egress_ended", egress_info=info))
                _aio.get_running_loop().create_task(_later())
            return _eg.EgressInfo(egress_id=egress_id, status=_eg.EgressStatus.EGRESS_ENDING)
        async def get(self, egress_id): return None
        async def probe(self): return None
    from app.services import video_rec as _vr
    _vr.EgressApi = lambda settings: _FakeEgress()        # VideoRecorder берёт EgressApi при создании (при запуске приложения)
# Только для стенда: подсунуть реплику стенограммы (вместо ASR) — так проверяются стенограмма, история и протоколы без распознавания речи
from fastapi import Body
@app.post("/__dev/segment")
async def _dev_segment(body: dict = Body(...)):
    import datetime as _dt, json as _json
    now = _dt.datetime.now(_dt.timezone.utc)
    start = now + _dt.timedelta(seconds=float(body.get("offset", 0)))
    await app.state.redis.xadd("asr:segments", {
        "segment_uid": str(uuid.uuid4()), "meeting_id": f"m-{uuid.UUID(body['meeting_id']).hex}", "identity": body["identity"], "started_at": start.isoformat(),
        "ended_at": (start + _dt.timedelta(seconds=2)).isoformat(), "text": body["text"], "language": "ru",
        "model": _json.dumps({"provider": "gigaam", "name": "stand", "device": "cpu"}), "duration_ms": "2000", "infer_ms": "100", "queue_ms": "5"})
    return {"ok": True}


@app.post("/__dev/pcm")
async def _dev_pcm(body: dict = Body(...)):
    """Только для стенда: положить «запись ASR» участника (синус заданной частоты) как это делает ASR-сервис: <recordings>/<livekit_room>/<identity>.pcm + .t0 (отметка начала)."""
    import array, math, time as _t
    async with app.state.session_maker() as db:
        m = await db.get(Meeting, uuid.UUID(body["meeting_id"]))
    d = os.path.join(s.recordings_path, m.livekit_room)
    os.makedirs(d, exist_ok=True)
    sec, freq = float(body.get("seconds", 10)), float(body.get("freq", 440))
    smp = array.array("h", (int(9000 * math.sin(2 * math.pi * freq * i / 16000)) for i in range(int(sec * 16000))))
    for a_, b_ in body.get("silence", []):                           # участки тишины [от, до) в секундах — чтобы волна и пауза были различимы
        for i in range(int(a_ * 16000), min(len(smp), int(b_ * 16000))):
            smp[i] = 0
    open(os.path.join(d, body["identity"] + ".pcm"), "wb").write(smp.tobytes())
    open(os.path.join(d, body["identity"] + ".t0"), "w").write(repr(_t.time() + float(body.get("start_offset", 0))))
    return {"ok": True}


@app.post("/__dev/fake_asr")
async def _dev_fake_asr():
    """Только для стенда: подставная служба распознавания для диктовки чата (контракт docs/ASR_CONTRACT.md, «Диктовка»): «жив» в heartbeat,
    на каждые ~1,5 с громкого звука — фрагмент «фраза N», в конце — done. Настоящий ASR (GigaAM) на стенде не запускается."""
    import array, asyncio, base64 as _b64, json as _json, math
    r = app.state.redis
    if getattr(app.state, "fake_asr", None):
        return {"ok": True, "already": True}

    async def session(sid):
        last, loud, n, out = "0-0", 0, 0, f"asr:dict:{sid}:out"
        while True:
            res = await r.xread({f"asr:dict:{sid}:in": last}, block=1000, count=50)
            for _s, items in res or []:
                for mid, f in items:
                    last = mid
                    if f.get("eof"):
                        if loud > 8000:
                            n += 1
                            await r.xadd(out, {"type": "final", "seq": str(n), "text": f"фраза {n}"})
                        await r.xadd(out, {"type": "done", "segments": str(n), "busy": "0", "timeout": "0"})
                        return
                    a = array.array("h"); a.frombytes(_b64.b64decode(f.get("pcm") or ""))
                    if a and math.sqrt(sum(x * x for x in a) / len(a)) > 300:
                        loud += len(a)
                        if loud >= 24000:
                            n, loud = n + 1, 0
                            await r.xadd(out, {"type": "final", "seq": str(n), "text": f"фраза {n}"})

    async def run():
        last = "$"
        while True:
            await r.set("asr:heartbeat", _json.dumps({"model_loaded": True, "stand": True}), ex=30)
            res = await r.xread({"asr:dictation:control": last}, block=5000, count=10)
            for _s, items in res or []:
                for mid, f in items:
                    last = mid
                    if f.get("type") == "start":
                        asyncio.create_task(session(f["sid"]))
    app.state.fake_asr = asyncio.create_task(run())
    return {"ok": True}


@app.post("/__dev/forget_waveforms")
async def _dev_forget_waveforms():
    """Только для стенда: забыть построенные волны — так проверяется «старая запись без волны»."""
    from app.models import RecordingWaveform
    async with app.state.session_maker() as db:
        await db.execute(RecordingWaveform.__table__.delete())
        await db.commit()
    return {"ok": True}


@app.post("/__dev/calendar")
async def _dev_calendar(body: dict = Body(default={})):
    """Только для стенда: подставной Microsoft Graph с несколькими событиями у alice (вместо Exchange) и включённый раздел «Календарь» — для проверки страницы в браузере."""
    import httpx
    from fake_graph import FakeGraph
    g = getattr(app.state, "dev_graph", None) or FakeGraph()
    app.state.dev_graph = g
    app.state.calendar_transport = httpx.MockTransport(g)
    if body.get("down") is not None:
        g.down = bool(body["down"])
    if body.get("reset") or not g.events:
        g.events.clear()
        now = utcnow().replace(minute=0, second=0, microsecond=0)
        mb = "alice@corp.test"
        def span(day, h, mi=0, minutes=45):
            s0 = (now + dt.timedelta(days=day)).replace(hour=h, minute=mi)
            return {"start": {"dateTime": s0.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}, "end": {"dateTime": (s0 + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}}
        att = lambda e, n="", k="required", r="none": {"emailAddress": {"address": e, "name": n}, "type": k, "status": {"response": r}}
        # серия: мастер (не попадает в календарное представление, как у настоящего Graph) и три занятия
        g.add_event(mb, id="SERIES", subject="Планёрка отдела", type="seriesMaster", calendar="_master", attendees=[att("bob@corp.test", "Боб Петров", r="accepted")], **span(1, 9))
        for i in range(3):
            g.add_event(mb, id=f"OCC{i}", subject="Планёрка отдела", type="occurrence", seriesMasterId="SERIES", attendees=[att("bob@corp.test", "Боб Петров", r="accepted")], **span(1 + 7 * i, 9))
        g.add_event(mb, id="BUDGET", subject="Созвон по бюджету", location={"displayName": "Переговорная 3"}, attendees=[att("bob@corp.test", "Боб Петров", r="accepted"), att("carol@corp.test", "Карина Сидорова", "optional")],
                    body={"contentType": "html", "content": "<p>Повестка:</p><p>1. Бюджет квартала<br>2. Риски</p>"}, **span(1, 10, 0, 60))
        g.add_event(mb, id="RETRO", subject="Ретроспектива спринта", attendees=[att("dan@corp.test")], **span(1, 10, 30, 60))
        g.add_event(mb, id="EARLY", subject="Ранняя зарядка", **span(1, 6, 30, 45))
        g.add_event(mb, id="LATE", subject="Вечерний созвон с Азией", attendees=[att("lee@corp.test")], **span(1, 21, 0, 60))
        g.add_event(mb, id="LUNCH", subject="Обед с партнёрами", showAs="free", **span(2, 12, 0, 60))
        g.add_event(mb, id="INVITE", subject="Планирование от руководителя", organizer={"emailAddress": {"address": "boss@corp.test", "name": "Руководитель"}}, attendees=[att("alice@corp.test", "Алиса")], **span(2, 14, 0, 60))
        g.add_event(mb, id="INC", subject="Разбор инцидента", location={"displayName": "http://localhost:5173/rooms/tmp-k7f3p2"}, **span(2, 16, 0, 60))
        g.add_event(mb, id="CANC", subject="Отменённая встреча", isCancelled=True, **span(3, 13))
        g.add_event(mb, id="ALLDAY", subject="Конференция (весь день)", isAllDay=True, **span(4, 0, 0, 1440))
        # короткие, пересекающиеся и очень длинные события — для проверки вида сетки (scenario_calendar_visual.py)
        g.add_event(mb, id="S15", subject="Быстрый вопрос", **span(1, 12, 0, 15))
        g.add_event(mb, id="S30", subject="Короткий созвон", attendees=[att("bob@corp.test", "Боб Петров")], **span(1, 12, 0, 30))
        g.add_event(mb, id="S30B", subject="Ещё один созвон", **span(1, 12, 15, 30))
        g.add_event(mb, id="LONGT", subject="Согласование квартального бюджета с финансовым департаментом и подрядчиками", location={"displayName": "Переговорная 7, второй этаж, северное крыло"}, **span(2, 10, 0, 45))
        g.add_event(mb, id="WORK", subject="Выездной воркшоп", **span(3, 15, 0, 180))
        g.add_event(mb, id="CONF15", subject="Стендап команды", location={"displayName": "http://localhost:5173/rooms/tmp-st4nd1"}, **span(3, 10, 0, 15))
        g.add_event(mb, id="PROJ", subject="Проектный созвон", calendar="CALPROJ", **span(1, 14, 0, 60))
        app.state.dev_day1 = (now + dt.timedelta(days=1)).date().isoformat()
    out = {"events": len(g.events), "day1": getattr(app.state, "dev_day1", None)}
    if body.get("dump"):
        out["items"] = {k: {"subject": e.get("subject"), "location": (e.get("location") or {}).get("displayName"), "cancelled": bool(e.get("isCancelled")), "start": e["start"]["dateTime"][:16], "end": e["end"]["dateTime"][:16],
                            "attendees": [a["emailAddress"]["address"] for a in e.get("attendees") or []], "body": ((e.get("body") or {}).get("content") or "")[:3000]} for k, e in g.events.items()}
        out["posts"] = [p for m, p in g.calls if m in ("POST", "PATCH", "DELETE")]
    return out


@app.post("/__dev/restore_seed")
async def _dev_restore_seed(body: dict = Body(default={})):
    """Только для стенда: старые материалы на диске «после переноса» — подписанный паспорт стенда (достоверно), каталог без паспорта и каталог с чужой подписью (предположительно)."""
    import base64 as b64, hashlib, json as js, pathlib
    from app.services import manifest as mf
    root = pathlib.Path(body.get("root") or os.path.join(DATA, "fs"))
    key = app.state.settings.app_master_key
    started = utcnow() - dt.timedelta(days=30)
    async with app.state.session_maker() as db:
        from sqlalchemy import select as sel
        room = (await db.execute(sel(Room).where(Room.slug == "north"))).scalars().first()
        alice = (await db.execute(sel(User).where(User.sam_account_name == "alice"))).scalars().first()
        rid, aid, guid = str(room.id), str(alice.id), alice.ad_guid.lower()
    def put(folder, rel, data):
        p = root / folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    def write_manifest(folder, directory, names, signer):
        files = []
        for n in names:
            b = (root / folder / directory / n).read_bytes()
            kind, fmt = mf.classify(n)
            files.append({"name": n, "kind": kind, "format": fmt, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()})
        ctx = {"meeting": {"id": str(uuid.uuid4()), "started_at": mf.iso(started), "ended_at": mf.iso(started + dt.timedelta(hours=1)), "end_reason": "manual", "transcription": True, "record_audio": True},
               "room": {"id": rid, "slug": "north", "name": "Переговорная «Север»", "lifetime": "permanent", "history_access": "participants"},
               "access": {"acl": [], "moderators": [], "grants": []}, "participants": [{"user_id": aid, "ad_guid": guid, "joined_at": mf.iso(started), "left_at": mf.iso(started)}], "retention": {"text_days": 365, "audio_days": 30}}
        doc = mf.sign(mf.build(ctx, instance=mf.instance_id(signer), folder=folder, directory=directory, files=files, now=utcnow()), signer)
        (root / folder / directory / "manifest.json").write_text(js.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    d1 = "Переговорная Север/2026-09-26, Суббота/10-30"
    put("Audio", d1 + "/Алиса Крылова.wav", b"RIFF" + os.urandom(4000)); put("Transcripts", d1 + "/protocol.txt", "Участвовали: Алиса Крылова\n[00:03] Алиса Крылова: Начинаем планёрку\n".encode())
    put("Protocols", d1 + "/summary.md", "Итог планёрки: утвердили план\n".encode())
    write_manifest("Audio", d1, ["Алиса Крылова.wav"], key); write_manifest("Transcripts", d1, ["protocol.txt"], key); write_manifest("Protocols", d1, ["summary.md"], key)
    d2 = "Бухгалтерия/2026-08-14, Пятница/09-00"
    put("Transcripts", d2 + "/protocol.txt", "Старая встреча без паспорта\n".encode()); put("Audio", d2 + "/Пётр Сидоров.wav", b"RIFF" + os.urandom(2500))
    d3 = "Юристы/2026-08-20, Четверг/15-00"
    put("Transcripts", d3 + "/protocol.txt", "Встреча из другой установки\n".encode())
    write_manifest("Transcripts", d3, ["protocol.txt"], b64.b64encode(os.urandom(32)).decode())
    return {"root": str(root), "dirs": [d1, d2, d3]}


eng = sa.create_engine(f"sqlite:///{DATA}/dev.db")
Base.metadata.create_all(eng)
FAM = ["Иванов", "Петрова", "Сидоренко", "Морозов", "Соколова", "Кузнецов", "Лебедева", "Орлов", "Крылова", "Мартынов"]
NAM = ["Алексей", "Ирина", "Дмитрий", "Екатерина", "Сергей", "Мария", "Андрей", "Ольга", "Павел", "Наталья"]
if os.environ.get("LIVE_CLEAN") != "1":          # LIVE_CLEAN=1 — «чистая установка»: ни комнат, ни пользователей, ни встреч (проверка восстановления конфигурации)
    from PIL import Image
    import io
    from app.services.avatars import AvatarStore
    with Session(eng) as db:
        alice = User(ad_guid=ALICE_GUID, sam_account_name="alice", display_name="Алиса Крылова", is_active=True, auth_source="ad")
        db.add(alice); db.flush()
        buf = io.BytesIO(); Image.new("RGB", (256, 256), (220, 80, 60)).save(buf, "WEBP")
        AvatarStore(DATA).save(alice.id, buf.getvalue())
        alice.avatar_mime, alice.avatar_updated_at = "image/webp", utcnow()
        people = []
        for i in range(130):
            name = f"{FAM[i % 10]} {NAM[(i // 10) % 10]} {i}" if i % 17 else f"Верещагина-Подгорная Александра Константиновна-Мария {i}"
            u = User(ad_guid=str(uuid.uuid4()), sam_account_name=f"u{i}", display_name=name, is_active=True)
            db.add(u)
            people.append(u)
        db.flush()
        specs = [
            ("it-1", "ИТ-1 · Планёрка", "Еженедельная планёрка отдела разработки и эксплуатации", {"guest_access_enabled": True, "guest_token": "devguesttoken1234567890"}, 4),
            ("north", "Переговорная «Север»", "Большой зал: совещания руководителей, показ экрана и доска", {"max_participants": 30, "auto_record": True, "record_audio": True}, 0),
            ("sec", "Безопасность", "Закрытые разборы инцидентов", {"password_hash": "x", "max_participants": 8}, 0),
            ("town", "Общее собрание", "Презентационная комната: говорят только руководители", {"room_type": "presentation", "max_participants": 100}, 12),
            ("stage", "Презентация без встречи", "Пустая презентационная комната: встреча создаётся входом (webhook LiveKit разбирает её как настоящую)", {"room_type": "presentation", "max_participants": 2000, "guest_access_enabled": True, "guest_token": "devguesttokenstage00001"}, 0),
            ("tmp-k7f3p2", "Разбор инцидента", None, {"lifetime": "temporary"}, 0),
            ("sales", "Продажи", None, {}, 0),
            ("hr", "HR и подбор персонала с очень длинным названием комнаты для проверки обрезки", "Интервью и собеседования кандидатов", {}, 0),
            ("fin", "Финансы", "Бюджетирование", {"guest_access_enabled": True, "guest_token": "devguesttoken0000000002"}, 0),
            ("legal", "Юристы", None, {}, 0), ("ops", "Эксплуатация", "Дежурная смена", {}, 2), ("pm", "Проектный офис", None, {"max_participants": 50}, 0),
            ("support", "Поддержка", "Разбор обращений", {}, 0), ("arch", "Архитектура", None, {}, 0), ("mkt", "Маркетинг", None, {}, 0),
        ]
        rooms = []
        for slug, name, desc, extra, live_n in specs:
            r = Room(slug=slug, name=name, description=desc, **extra)
            r.acl = [RoomAcl(subject_type="group", subject_ref=STAFF_GROUP)]
            db.add(r)
            db.flush()
            rooms.append(r)
            if live_n:
                m = Meeting(room_id=r.id, livekit_room="lk-" + slug, started_by_user_id=people[0].id)
                db.add(m)
                db.flush()
                for p in people[:live_n]:
                    db.add(MeetingParticipant(meeting_id=m.id, user_id=p.id))
        base = utcnow() - dt.timedelta(days=3)
        for k, (room, n, org) in enumerate([(rooms[0], 1, 0), (rooms[1], 3, 0), (rooms[3], 10, 4), (rooms[2], 130, 7), (rooms[5], 34, 0), (rooms[6], 2, 1)]):
            st = base + dt.timedelta(hours=k * 5)
            m = Meeting(room_id=room.id, livekit_room=f"lk-old{k}", started_at=st, ended_at=st + dt.timedelta(minutes=55), end_reason="manual", started_by_user_id=people[org].id)
            db.add(m)
            db.flush()
            for i, p in enumerate(people[:n]):
                db.add(MeetingParticipant(meeting_id=m.id, user_id=p.id, joined_at=st + dt.timedelta(seconds=i * 20), left_at=st + dt.timedelta(minutes=55)))
            db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=room.id, user_id=people[0].id, participant_identity="u0", started_at=st, ended_at=st + dt.timedelta(seconds=4), text="Начинаем."))
        if os.environ.get("LIVE_MOCK_LLM") == "1" or os.environ.get("LIVE_SEED_MEETING") == "1":       # (LIVE_SEED_MEETING — то же без подставной модели: для проверки на настоящей) завершённая встреча с живой стенограммой: на ней проверяются протокол и карта разговора
            st = utcnow() - dt.timedelta(hours=3)
            m = Meeting(room_id=rooms[0].id, livekit_room="lk-mock", started_at=st, ended_at=st + dt.timedelta(minutes=48), end_reason="manual", started_by_user_id=alice.id)
            db.add(m); db.flush()
            for p in (alice, people[1], people[2]):
                db.add(MeetingParticipant(meeting_id=m.id, user_id=p.id, joined_at=st, left_at=st + dt.timedelta(minutes=48)))
            script = [("Начнём с сертификатов сервера: срок действия заканчивается в ноябре.", 0), ("Нужно заменить сертификаты до конца октября, пока не поздно.", 1),
                      ("Решили заменить сертификаты сервера до конца октября.", 2), ("Кто у нас закажет новые сертификаты у поставщика?", 0),
                      ("Закажу сертификаты в понедельник, согласую цену.", 1), ("Теперь про бюджет на следующий год, нужны рамки расходов.", 0),
                      ("Предлагаю оставить расходы на уровне этого года.", 2), ("Решили утвердить бюджет на уровне этого года.", 0),
                      ("Сколько стоит лицензия на мониторинг в следующем году?", 1), ("Пока неизвестно, поставщик обещал прислать цену.", 2),
                      ("Последний вопрос: доступ подрядчиков к тестовому стенду.", 0), ("Подрядчикам нужен доступ только на чтение.", 1),
                      ("Решили выдать подрядчикам доступ только на чтение на две недели.", 2), ("Спасибо всем, на этом закончим.", 0)]
            who = (alice, people[1], people[2])
            for i, (text, w) in enumerate(script):
                a = st + dt.timedelta(seconds=i * 200 + 5)
                db.add(TranscriptSegment(segment_uid=uuid.uuid4(), meeting_id=m.id, room_id=rooms[0].id, user_id=who[w].id, participant_identity=who[w].sam_account_name,
                                         started_at=a, ended_at=a + dt.timedelta(seconds=40), text=text))
        rooms[6].moderators.append(RoomModerator(subject_type="user", subject_ref=ALICE_GUID, display_name="Алиса Крылова"))   # alice — руководитель «Продаж»
        db.commit()
    eng.dispose()
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("LIVE_PORT", "8000")), log_level="warning")
