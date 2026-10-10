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
from app.models import Base, Meeting, MeetingParticipant, Room, RoomAcl, TranscriptSegment, User, utcnow
from conftest import FakeDirectory, ADMIN_GROUP, STAFF_GROUP

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
s = Settings(database_url=f"sqlite+aiosqlite:///{DATA}/dev.db", redis_url="redis://x", app_public_url="http://localhost:5173", cookie_secure=False,
             livekit_api_key="devkey", livekit_api_secret="s" * 40, livekit_public_url="ws://127.0.0.1:7880", livekit_internal_url="http://127.0.0.1:7880",
             app_master_key=base64.b64encode(os.urandom(32)).decode(), internal_api_token="t", ldap_admin_group_dn=ADMIN_GROUP, data_dir=DATA,
             docs_enabled=False, log_level="WARNING", segment_consumer_block_ms=50, app_version=open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "VERSION"), encoding="utf-8").read().strip(), app_git_commit="abcdef012345")
app = create_app(s, redis_factory=lambda _: FakeAsyncRedis(decode_responses=True), directory_factory=lambda _: d)
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


@app.post("/__dev/forget_waveforms")
async def _dev_forget_waveforms():
    """Только для стенда: забыть построенные волны — так проверяется «старая запись без волны»."""
    from app.models import RecordingWaveform
    async with app.state.session_maker() as db:
        await db.execute(RecordingWaveform.__table__.delete())
        await db.commit()
    return {"ok": True}


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
        db.commit()
    eng.dispose()
uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
