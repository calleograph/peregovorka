"""Сервер для нагрузочных испытаний: настоящий backend с PostgreSQL и Redis, подставной каталог пользователей (сколько угодно учётных записей) и подставная языковая модель с задержкой.
Запуск: PG_URL=postgresql+asyncpg://… REDIS_URL=redis://… USERS=2000 PORT=18080 python tests/perf/devserver_perf.py   (несколько процессов: UVICORN_WORKERS=4)
Не для эксплуатации: пользователи и пароли — тестовые, LiveKit не нужен (токены выдаются локально, медиа не проверяется)."""
import asyncio, base64, json, os, sys, uuid
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "backend", "tests"))
import httpx, uvicorn
from fastapi import Body
from app.config import Settings
from app.main import create_app
from app.models import Base
from conftest import ADMIN_GROUP, FakeDirectory

N = int(os.environ.get("USERS", "100"))
PG, RDS = os.environ["PG_URL"], os.environ["REDIS_URL"]
LLM_DELAY = float(os.environ.get("LLM_DELAY", "4"))
KEY = os.environ.get("MASTER_KEY") or base64.b64encode(b"k" * 32).decode()


def directory() -> FakeDirectory:
    d = FakeDirectory()
    d.add("root", "pw", groups=(ADMIN_GROUP,), name="Администратор")
    for i in range(N):
        d.add(f"u{i:04d}", "pw", name=f"Пользователь {i:04d}")
    return d


def settings() -> Settings:
    kw = {}
    for env, name in (("DB_POOL_SIZE", "db_pool_size"), ("DB_MAX_OVERFLOW", "db_max_overflow"), ("DB_BG_POOL_SIZE", "db_bg_pool_size")):
        if os.environ.get(env):
            kw[name] = int(os.environ[env])
    return Settings(database_url=PG, redis_url=RDS, app_public_url="http://localhost", cookie_secure=False, livekit_api_key="devkey", livekit_api_secret="s" * 40,
                    livekit_public_url="ws://127.0.0.1:7880", livekit_internal_url="http://127.0.0.1:7880", app_master_key=KEY, internal_api_token="t", ldap_admin_group_dn=ADMIN_GROUP,
                    data_dir=os.environ.get("DATA", "/tmp/pg-perf-data"), docs_enabled=False, log_level="WARNING", segment_consumer_block_ms=50, app_version="perf", **kw)


async def llm(req: httpx.Request) -> httpx.Response:
    await asyncio.sleep(LLM_DELAY)                                       # «думающая» модель: во время ожидания соединение PostgreSQL занято быть не должно
    body = json.loads(req.content)
    text = body["messages"][-1]["content"]
    import re
    st = re.findall(r"\[(\d\d:\d\d:\d\d)\]", text)
    if "topics" in json.dumps(body, ensure_ascii=False)[:4000] and st:
        content = json.dumps({"topics": [{"title": "Тема", "summary": "Кратко", "category": "dev", "start": st[0], "end": st[-1]}]}, ensure_ascii=False)
    else:
        content = "**Протокол**\n\n- решение принято"
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 50, "completion_tokens": 30}})


def app_factory():
    d = directory()
    from redis.asyncio import Redis
    app = create_app(settings(), redis_factory=lambda s: __import__("app.main", fromlist=["make_redis"]).make_redis(s), directory_factory=lambda _: d)
    app.state.test_transports = {"llm": httpx.MockTransport(llm)}

    @app.post("/__dev/segment")
    async def _seg(body: dict = Body(...)):
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=float(body.get("offset", 0)))
        await app.state.redis.xadd("asr:segments", {"segment_uid": str(uuid.uuid4()), "meeting_id": f"m-{uuid.UUID(body['meeting_id']).hex}", "identity": body["identity"],
                                   "started_at": now.isoformat(), "ended_at": (now + dt.timedelta(seconds=2)).isoformat(), "text": body["text"], "language": "ru",
                                   "model": json.dumps({"provider": "gigaam", "name": "perf", "device": "cpu"}), "duration_ms": "2000", "infer_ms": "100", "queue_ms": "5"})
        return {"ok": True}
    return app


if __name__ == "__main__":
    async def init():
        from sqlalchemy.ext.asyncio import create_async_engine
        e = create_async_engine(PG)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()
    asyncio.run(init())
    uvicorn.run("devserver_perf:app_factory", factory=True, host="127.0.0.1", port=int(os.environ.get("PORT", "18080")), log_level="warning", timeout_keep_alive=75, workers=int(os.environ.get("UVICORN_WORKERS", "1")),
                app_dir=os.path.dirname(os.path.abspath(__file__)))
